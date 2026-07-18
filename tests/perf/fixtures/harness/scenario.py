"""Shared scenario helpers used by several perf tests."""

from __future__ import annotations

import statistics
import time

from .consumer import StreamConsumer

STREAM_START_DELAY_S = 2.0


def start_stream(
    env,
    namespace: str,
    name: str,
    *,
    frames: int,
    rate: float,
    size: int = 150,
    hold_s: float = 0.0,
    start_delay: float = STREAM_START_DELAY_S,
    consumer_kwargs: dict | None = None,
) -> tuple[str, StreamConsumer]:
    """Launch one perf_stream execution with an attached consumer."""
    run = env.run_async(
        namespace,
        name,
        {
            "frames": frames,
            "rate": rate,
            "size": size,
            "start_delay": start_delay,
            "hold_s": hold_s,
        },
    )
    execution_id = run.get("execution_id") or run["id"]
    consumer = StreamConsumer(
        env.server_url,
        execution_id,
        **(consumer_kwargs or {}),
    ).start()
    return execution_id, consumer


def delivered_rate(stats: dict) -> float | None:
    """Delivered events/s over the receive window."""
    if not stats["delivered"] or stats["first_recv"] is None:
        return None
    window = stats["last_recv"] - stats["first_recv"]
    if window <= 0:
        return None
    return (stats["delivered"] - 1) / window


def offered_rate(consumer: StreamConsumer) -> float | None:
    """Actual sender-side rate, from the sender timestamps that arrived.

    Underestimates the send window if the tail was dropped; good enough to
    label loss-curve points.
    """
    sent = [f.t_sent for f in consumer.frames if f.t_sent is not None]
    if len(sent) < 2 or max(sent) == min(sent):
        return None
    return (len(sent) - 1) / (max(sent) - min(sent))


def median_or_none(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def wait_all(consumers: list[StreamConsumer], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    for c in consumers:
        c.wait(max(0.5, deadline - time.monotonic()))
