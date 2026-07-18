"""Synthetic progress load generator for T3 (server aggregate knee).

Posts progress batches directly to ``POST /workers/{name}/progress/{id}``,
bypassing real workers — the point is to load the *server's* ingest + SSE
fan-out, not the worker pipeline (T1/T2 cover that). With auth disabled the
worker-identity check is a no-op (flux/server.py::_verify_worker_identity),
so no registration handshake is needed; target executions must exist and
have subscribed consumers or ingest discards every frame by design.

Batches are capped at 50 frames to match the real worker flusher
(flux/worker.py) — bigger batches would flatter the knee.
"""

from __future__ import annotations

import json
import threading
import time

import httpx

BATCH_MAX = 50


class ProgressLoadGenerator:
    """N sender threads round-robining batches across target executions."""

    def __init__(
        self,
        server_url: str,
        execution_ids: list[str],
        pad_bytes: int = 150,
        worker_name: str = "perf-loadgen",
        senders: int = 4,
    ):
        self.server_url = server_url.rstrip("/")
        self.execution_ids = execution_ids
        self.pad = "x" * pad_bytes
        self.worker_name = worker_name
        self.senders = senders
        self.offered = 0
        self.post_errors = 0
        self._lock = threading.Lock()

    def run_step(self, rate: float, seconds: float) -> dict:
        """Offer ``rate`` events/s for ``seconds``; return step accounting."""
        stop_at = time.monotonic() + seconds
        per_sender = rate / self.senders
        offered_before = self.offered
        errors_before = self.post_errors
        threads = [
            threading.Thread(
                target=self._sender_loop,
                args=(i, per_sender, stop_at),
                daemon=True,
            )
            for i in range(self.senders)
        ]
        t0 = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        t1 = time.time()
        return {
            "rate_target": rate,
            "seconds": seconds,
            "offered": self.offered - offered_before,
            "post_errors": self.post_errors - errors_before,
            "window": [t0, t1],
        }

    def _sender_loop(self, sender_idx: int, rate: float, stop_at: float):
        seq = 0
        target_idx = sender_idx
        interval_per_batch = BATCH_MAX / rate if rate > 0 else 0.0
        next_at = time.monotonic()
        with httpx.Client(base_url=self.server_url, timeout=10) as client:
            while time.monotonic() < stop_at:
                execution_id = self.execution_ids[target_idx % len(self.execution_ids)]
                target_idx += 1
                now = time.time()
                batch = [
                    {
                        "task_id": f"loadgen_{sender_idx}",
                        "task_name": "loadgen",
                        "value": {"i": seq + k, "t": now, "pad": self.pad},
                    }
                    for k in range(BATCH_MAX)
                ]
                seq += BATCH_MAX
                try:
                    r = client.post(
                        f"/workers/{self.worker_name}-{sender_idx}/progress/{execution_id}",
                        content=json.dumps(batch),
                        headers={"Content-Type": "application/json"},
                    )
                    r.raise_for_status()
                except httpx.HTTPError:
                    with self._lock:
                        self.post_errors += 1
                with self._lock:
                    self.offered += BATCH_MAX
                if interval_per_batch:
                    next_at += interval_per_batch
                    delay = next_at - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    else:
                        next_at = time.monotonic()
