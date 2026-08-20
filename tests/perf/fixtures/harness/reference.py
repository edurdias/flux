"""Comparing a benchmark run against a blessed reference (#264 follow-up).

A single PR's benchmark run cannot answer "did this make Flux slower": the
run-to-run spread on any real machine is wider than the change most PRs
make, which is why every A/B in docs/benchmarks lands inside the noise.
The risk that hides in that is cumulative -- twenty changes each costing
2% are invisible one at a time and a 50% regression together.

So the guard is not per-PR significance, it is **drift against a stored
reference**: a set of medians measured deliberately on quiet hardware,
with the tolerance derived from that same hardware's observed spread
rather than picked. A run is compared median-to-median, never
sample-to-sample.

The reference is refreshed deliberately (``make bench-bless``), not
automatically -- a baseline that follows the code is not a baseline.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path

REFERENCE_PATH = Path(__file__).resolve().parents[2] / "reference.json"


@dataclass(frozen=True)
class Comparison:
    metric: str
    reference: float
    observed: float
    tolerance: float
    better: str  # "lower" or "higher"

    @property
    def delta_pct(self) -> float:
        return (self.observed - self.reference) / self.reference * 100

    @property
    def regressed(self) -> bool:
        """Outside tolerance in the direction that is worse.

        Faster than the reference is never a failure -- it is a reason to
        re-bless, which is a decision for a person.
        """
        if self.better == "lower":
            return self.observed > self.reference * (1 + self.tolerance)
        return self.observed < self.reference * (1 - self.tolerance)

    def render(self) -> str:
        verdict = "REGRESSED" if self.regressed else "ok"
        return (
            f"{self.metric:22s} ref={self.reference:9.1f} "
            f"observed={self.observed:9.1f} {self.delta_pct:+6.1f}% "
            f"(tol ±{self.tolerance * 100:.0f}%)  {verdict}"
        )


def load_reference(path: Path | None = None) -> dict:
    target = path or REFERENCE_PATH
    if not target.exists():
        raise FileNotFoundError(
            f"No benchmark reference at {target}. Establish one with `make bench-bless`.",
        )
    return json.loads(target.read_text())


def compare(observed: dict[str, list[float]], reference: dict, key: str) -> list[Comparison]:
    """Compare medians of ``observed`` samples against the reference entry.

    ``observed`` maps metric name to the samples collected this run --
    plural on purpose. One sample is a reading, not a measurement.
    """
    entry = reference["entries"].get(key)
    if entry is None:
        raise KeyError(f"Reference has no entry for {key!r}; known: {sorted(reference['entries'])}")
    out = []
    for metric, samples in observed.items():
        if metric not in entry["metrics"] or not samples:
            continue
        spec = entry["metrics"][metric]
        out.append(
            Comparison(
                metric=metric,
                reference=spec["median"],
                observed=statistics.median(samples),
                tolerance=spec["tolerance"],
                better=spec["better"],
            ),
        )
    return out


def tolerance_from_samples(samples: list[float], floor: float = 0.10) -> float:
    """A tolerance the observed spread actually justifies.

    Half the relative range of the blessing runs, floored so a lucky quiet
    batch cannot set a tolerance nothing can meet. Derived rather than
    chosen: a band tighter than the machine's own noise produces alerts
    that teach people to ignore alerts.
    """
    if len(samples) < 2:
        return floor
    median = statistics.median(samples)
    if median == 0:
        return floor
    spread = (max(samples) - min(samples)) / median
    return max(floor, round(spread / 2, 3))


# Which numbers each benchmark contributes to the reference, and which way
# is better. Deliberately a short list: the guard is for the figures a
# regression would show up in, not for everything a run records.
METRIC_SPECS = {
    "B1": {
        "dispatch_p50": ("lower", lambda r: r["dispatch_ms"]["p50"]),
        "dispatch_p95": ("lower", lambda r: r["dispatch_ms"]["p95"]),
        "claim_p50": ("lower", lambda r: r["claim_ms"]["p50"]),
    },
    "B2": {
        "tasks_per_s": ("higher", lambda r: r["tasks_per_s"]),
    },
    "B3": {
        "replay_fixed_ms": ("lower", lambda r: r["runs"][0]["replay_ms"]),
    },
    "B4": {
        "health_p95_under_load": ("lower", lambda r: r["health_ms_under_load"]["p95"]),
    },
}


def metrics_from_record(test: str, record: dict) -> dict[str, float]:
    """The reference-tracked figures in one run record."""
    out: dict[str, float] = {}
    for metric, (_, extract) in METRIC_SPECS.get(test, {}).items():
        try:
            value = extract(record)
        except (KeyError, IndexError, TypeError):
            continue
        if value is not None:
            out[metric] = float(value)
    return out


def entry_key(test: str, record: dict) -> str:
    """Reference keys are per benchmark, backend and profile.

    A SQLite ci number and a PostgreSQL full number are different
    measurements of different things; comparing across them would be the
    same mistake as comparing across machines.
    """
    return f"{test}/{record.get('backend', 'unknown')}/{record.get('profile', 'unknown')}"
