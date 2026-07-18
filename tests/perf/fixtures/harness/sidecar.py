"""Deterministic SSE token emitter — T4's default sidecar.

Stands in for an LLM server: emits ``tokens`` SSE events at a fixed
``gap_ms`` schedule, each carrying its sequence number and emit timestamp.
The T4 measurement is the *delta* between consuming this stream directly and
consuming it through Flux `progress()`; a deterministic schedule makes the
two runs comparable without real inference (PLAN.md changelog-4). A real
llama-server can be substituted by pointing the same workflow at its URL.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output clean
        pass

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        tokens = int(q.get("tokens", ["100"])[0])
        gap_s = float(q.get("gap_ms", ["33"])[0]) / 1000.0
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        next_at = time.monotonic()
        try:
            for i in range(tokens):
                payload = json.dumps({"i": i, "t": time.time(), "token": f"tok{i}"})
                self.wfile.write(f"data: {payload}\n\n".encode())
                self.wfile.flush()
                next_at += gap_s
                delay = next_at - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


class TokenSidecar:
    """Threaded HTTP server emitting deterministic token streams."""

    def __init__(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="token-sidecar",
            daemon=True,
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/stream"

    def start(self) -> TokenSidecar:
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._thread.join(5)


def consume_direct(url: str, tokens: int, gap_ms: float) -> list[float]:
    """Consume the sidecar directly; return per-token latency (recv - emit)."""
    latencies: list[float] = []
    with httpx.Client(timeout=httpx.Timeout(10.0, read=None)) as client:
        with client.stream(
            "GET",
            url,
            params={"tokens": tokens, "gap_ms": gap_ms},
        ) as r:
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[len("data: ") :]
                if data == "[DONE]":
                    break
                event = json.loads(data)
                latencies.append(time.time() - event["t"])
    return latencies
