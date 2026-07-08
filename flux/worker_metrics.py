"""Built-in worker metrics for routing policies.

The worker publishes a standard set of metrics under the reserved ``flux.``
prefix on its heartbeat pong — no metrics provider required — so policies
like ``least(metric("flux.loop_lag_p95_seconds"))`` work out of the box.
Aggregates are computed worker-side over fixed-size windows and published as
single scalars: the control plane only ever stores the latest snapshot per
worker, never a time series (windowing happens here, at the source).

All recording is O(1): EWMAs for smoothed gauges, bounded deques for the
percentile windows. Values are quantized in the snapshot so heartbeat jitter
does not defeat the server's changed-only persistence gate.
"""

from __future__ import annotations

import os
import time
from collections import deque
from collections.abc import Callable

# Fraction of new sample mixed into smoothed gauges each refresh.
_EWMA_ALPHA = 0.3
# Samples kept per percentile window.
_WINDOW = 128
# Completion stamps kept for the per-minute rate (bounds memory; a worker
# finishing more than this many executions per minute saturates the gauge).
_RATE_WINDOW = 4096


def _p95(samples: deque[float]) -> float:
    ordered = sorted(samples)
    return ordered[int(0.95 * (len(ordered) - 1))]


def _median(samples: deque[float]) -> float:
    ordered = sorted(samples)
    return ordered[len(ordered) // 2]


class WorkerMetricsCollector:
    """Accumulates execution/loop/system signals and snapshots them as the
    built-in ``flux.*`` metrics."""

    def __init__(
        self,
        max_concurrent: int | None = None,
        warm_modules: Callable[[], int] | None = None,
    ):
        self._max_concurrent = max_concurrent
        self._warm_modules = warm_modules
        self._latest_loop_lag: float | None = None
        self._loop_lags: deque[float] = deque(maxlen=_WINDOW)
        self._durations: deque[float] = deque(maxlen=_WINDOW)
        self._startups: deque[float] = deque(maxlen=_WINDOW)
        self._outcomes: deque[str] = deque(maxlen=_WINDOW)
        self._completion_stamps: deque[float] = deque(maxlen=_RATE_WINDOW)
        self._cpu_ewma: float | None = None

    # -- recording (called from the worker's execution/probe paths) --------

    def record_loop_lag(self, lag: float) -> None:
        self._latest_loop_lag = lag
        self._loop_lags.append(lag)

    def record_outcome(self, outcome: str) -> None:
        """outcome: 'completed' | 'failed' | 'crashed'."""
        self._outcomes.append(outcome)
        self._completion_stamps.append(time.monotonic())

    def record_duration(self, seconds: float) -> None:
        self._durations.append(seconds)

    def record_startup(self, seconds: float) -> None:
        self._startups.append(seconds)

    # -- snapshot ------------------------------------------------------------

    def snapshot(self, running: int) -> dict[str, float]:
        """The current built-in metrics. Keys are omitted until their source
        has data, so a policy term simply cannot discriminate yet."""
        metrics: dict[str, float] = {"flux.running_executions": float(running)}

        if self._max_concurrent:
            metrics["flux.slots_free"] = float(max(0, self._max_concurrent - running))

        if self._latest_loop_lag is not None:
            metrics["flux.loop_lag_seconds"] = round(self._latest_loop_lag, 4)
            metrics["flux.loop_lag_p95_seconds"] = round(_p95(self._loop_lags), 4)

        if self._outcomes:
            total = len(self._outcomes)
            failed = sum(1 for o in self._outcomes if o in ("failed", "crashed"))
            crashed = sum(1 for o in self._outcomes if o == "crashed")
            metrics["flux.failure_rate"] = round(failed / total, 4)
            metrics["flux.crash_rate"] = round(crashed / total, 4)

        if self._completion_stamps:
            cutoff = time.monotonic() - 60.0
            metrics["flux.executions_per_minute"] = float(
                sum(1 for stamp in self._completion_stamps if stamp >= cutoff),
            )

        if self._durations:
            metrics["flux.execution_duration_p95_seconds"] = round(_p95(self._durations), 4)

        if self._startups:
            metrics["flux.startup_overhead_seconds"] = round(_median(self._startups), 4)

        if self._warm_modules is not None:
            try:
                metrics["flux.warm_modules"] = float(self._warm_modules())
            except Exception:  # pragma: no cover - accessor must never break pongs
                pass

        metrics.update(self._system_metrics())
        return metrics

    def _system_metrics(self) -> dict[str, float]:
        metrics: dict[str, float] = {}
        try:
            import psutil

            # interval=None: non-blocking, measures since the previous call.
            sample = float(psutil.cpu_percent(interval=None))
            ewma = self._cpu_ewma
            ewma = sample if ewma is None else ewma + _EWMA_ALPHA * (sample - ewma)
            self._cpu_ewma = ewma
            metrics["flux.cpu_percent"] = round(ewma, 1)
            # Quantized to 16 MiB steps so allocator noise does not defeat
            # the server's changed-only persistence gate.
            available = psutil.virtual_memory().available
            step = 16 * 1024 * 1024
            metrics["flux.memory_available_bytes"] = float((available // step) * step)
        except Exception:  # pragma: no cover - psutil hiccups must not break pongs
            pass
        if hasattr(os, "getloadavg"):
            try:
                metrics["flux.load_avg_1m"] = round(os.getloadavg()[0], 2)
            except OSError:  # pragma: no cover
                pass
        return metrics
