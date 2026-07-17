"""SSE stream consumer for perf measurement.

Subscribes to ``GET /executions/{id}?mode=stream`` (the detached consumer
path — this is what creates the server-side progress buffer) in a dedicated
thread, and records per-frame timing.

Latency convention (PLAN.md §changelog-8): the server stamps event ``time``
at ingest with its own clock, so end-to-end latency is computed sender-side —
synthetic frames carry ``{"i": seq, "t": <wall ts at send>}`` in their payload
and the consumer records receive wall time on the same host.

Frame identification keys on the SSE event name ``task.progress`` — the actual
transport name (flux/server.py) — not on WIRE_TASK_PROGRESS, which belongs to
the agent-client decode layer.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from httpx_sse import connect_sse

TERMINAL_SUFFIXES = (
    ".execution.completed",
    ".execution.failed",
    ".execution.cancelled",
)


@dataclass
class FrameRecord:
    seq: int | None
    t_sent: float | None
    t_recv: float
    size_bytes: int


@dataclass
class ConsumerStats:
    delivered: int
    first_recv: float | None
    last_recv: float | None
    latency_p50: float | None
    latency_p95: float | None
    latency_p99: float | None
    interframe_p50: float | None
    interframe_p99: float | None
    max_seq: int | None
    terminal_event: str | None
    lifecycle_events: int

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile; q in [0, 1]."""
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[idx]


class StreamConsumer:
    """Consume one execution's SSE stream in a background thread."""

    def __init__(
        self,
        server_url: str,
        execution_id: str,
        detailed: bool = False,
        throttle_bytes_per_s: float | None = None,
        connect_timeout: float = 30.0,
    ):
        self.server_url = server_url.rstrip("/")
        self.execution_id = execution_id
        self.detailed = detailed
        self.throttle_bytes_per_s = throttle_bytes_per_s
        self.connect_timeout = connect_timeout

        self.frames: list[FrameRecord] = []
        self.lifecycle: list[tuple[str, float]] = []
        self.terminal_event: str | None = None
        self.error: BaseException | None = None

        self._connected = threading.Event()
        self._done = threading.Event()
        self._stop = threading.Event()
        self._client: httpx.Client | None = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"consumer-{execution_id[:8]}",
            daemon=True,
        )

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> StreamConsumer:
        self._thread.start()
        if not self._connected.wait(self.connect_timeout) and self.error:
            raise RuntimeError(
                f"Consumer for {self.execution_id} failed to connect",
            ) from self.error
        return self

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for the stream to reach a terminal event (or error out)."""
        return self._done.wait(timeout)

    def stop(self, timeout: float = 10.0):
        """Disconnect promptly: closing the client aborts a blocked read, so
        the server observes a real SSE disconnect (T5b depends on this)."""
        self._stop.set()
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
        self._thread.join(timeout)

    # -- consumption ---------------------------------------------------------

    def _run(self):
        url = f"{self.server_url}/executions/{self.execution_id}"
        params: dict[str, Any] = {"mode": "stream", "detailed": self.detailed}
        try:
            with httpx.Client(timeout=httpx.Timeout(10.0, read=None)) as client:
                self._client = client
                with connect_sse(client, "GET", url, params=params) as source:
                    self._connected.set()
                    for sse in source.iter_sse():
                        if self._stop.is_set():
                            break
                        self._handle(sse.event, sse.data)
                        if self.terminal_event:
                            break
                        if self.throttle_bytes_per_s:
                            time.sleep(len(sse.data) / self.throttle_bytes_per_s)
        except BaseException as e:
            if not self._stop.is_set():  # a stop()-forced close is not an error
                self.error = e
        finally:
            self._connected.set()
            self._done.set()

    def _handle(self, event: str, data: str):
        t_recv = time.time()
        if event == "task.progress":
            seq: int | None = None
            t_sent: float | None = None
            try:
                value = json.loads(data).get("value")
                if isinstance(value, dict):
                    seq = value.get("i")
                    t_sent = value.get("t")
            except (json.JSONDecodeError, AttributeError):
                pass
            self.frames.append(FrameRecord(seq, t_sent, t_recv, len(data)))
            return
        self.lifecycle.append((event, t_recv))
        if event.endswith(TERMINAL_SUFFIXES):
            self.terminal_event = event

    # -- reporting -----------------------------------------------------------

    def stats(self) -> ConsumerStats:
        latencies = [f.t_recv - f.t_sent for f in self.frames if f.t_sent is not None]
        recv_times = [f.t_recv for f in self.frames]
        gaps = [b - a for a, b in zip(recv_times, recv_times[1:])]
        seqs = [f.seq for f in self.frames if f.seq is not None]
        return ConsumerStats(
            delivered=len(self.frames),
            first_recv=recv_times[0] if recv_times else None,
            last_recv=recv_times[-1] if recv_times else None,
            latency_p50=percentile(latencies, 0.50),
            latency_p95=percentile(latencies, 0.95),
            latency_p99=percentile(latencies, 0.99),
            interframe_p50=percentile(gaps, 0.50),
            interframe_p99=percentile(gaps, 0.99),
            max_seq=max(seqs) if seqs else None,
            terminal_event=self.terminal_event,
            lifecycle_events=len(self.lifecycle),
        )


@dataclass
class MultiConsumerResult:
    consumers: list[StreamConsumer] = field(default_factory=list)

    def stats(self) -> list[ConsumerStats]:
        return [c.stats() for c in self.consumers]
