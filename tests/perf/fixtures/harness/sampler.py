"""psutil-based process sampler: CPU% and RSS at a fixed interval."""

from __future__ import annotations

import csv
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import psutil


@dataclass
class Sample:
    t: float
    process: str
    cpu_percent: float
    rss_bytes: int


class ProcessSampler:
    """Sample named processes at ``interval`` seconds in a background thread.

    ``cpu_percent`` uses psutil's non-blocking form, so each reading covers
    the interval since the previous one (the first reading per process is a
    meaningless 0.0 and is discarded).
    """

    def __init__(self, pids: dict[str, int], interval: float = 1.0):
        self._procs: dict[str, psutil.Process] = {}
        for name, pid in pids.items():
            try:
                self._procs[name] = psutil.Process(pid)
            except psutil.NoSuchProcess:
                pass
        self.interval = interval
        self.samples: list[Sample] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="perf-sampler",
            daemon=True,
        )
        self._primed = False

    def start(self) -> ProcessSampler:
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(self.interval + 5)

    def _run(self):
        # Prime cpu_percent so the first recorded reading is meaningful.
        for proc in self._procs.values():
            try:
                proc.cpu_percent()
            except psutil.Error:
                pass
        while not self._stop.wait(self.interval):
            now = time.time()
            for name, proc in self._procs.items():
                try:
                    with proc.oneshot():
                        self.samples.append(
                            Sample(
                                t=now,
                                process=name,
                                cpu_percent=proc.cpu_percent(),
                                rss_bytes=proc.memory_info().rss,
                            ),
                        )
                except psutil.Error:
                    continue

    # -- reporting -----------------------------------------------------------

    def summary(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for name in self._procs:
            series = [s for s in self.samples if s.process == name]
            if not series:
                continue
            cpu = [s.cpu_percent for s in series]
            rss = [s.rss_bytes for s in series]
            out[name] = {
                "samples": len(series),
                "cpu_mean": sum(cpu) / len(cpu),
                "cpu_max": max(cpu),
                "rss_first": rss[0],
                "rss_max": max(rss),
                "rss_last": rss[-1],
            }
        return out

    def to_csv(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t", "process", "cpu_percent", "rss_bytes"])
            for s in self.samples:
                w.writerow([f"{s.t:.3f}", s.process, s.cpu_percent, s.rss_bytes])
