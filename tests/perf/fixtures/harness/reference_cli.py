"""Establish or check the benchmark reference (`make bench-bless` / `bench-check`).

Runs the B series several times, takes the median of each tracked metric,
and either writes those medians as the new reference or compares them
against the stored one.

Repetition is the whole point. A single run cannot distinguish a change
from the machine's own variance -- documented at 815-899 ms dispatch p50
and 152-214 tasks/s on the reference box for *identical* code -- so both
blessing and checking work median-to-median over several runs.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

PERF_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PERF_DIR))

from fixtures.harness.reference import (  # noqa: E402
    METRIC_SPECS,
    REFERENCE_PATH,
    compare,
    entry_key,
    load_reference,
    metrics_from_record,
    tolerance_from_samples,
)

RESULTS = PERF_DIR / "results"


def _newest_records() -> dict[str, dict]:
    """The most recent record for each benchmark directory."""
    out = {}
    for test_dir in sorted(RESULTS.iterdir()):
        if not test_dir.is_dir():
            continue
        files = sorted(test_dir.glob("*.json"))
        if files:
            out[test_dir.name] = json.loads(files[-1].read_text())
    return out


def _run_once(selector: str, extra_env: dict[str, str]) -> dict[str, dict]:
    env = {**os.environ, "FLUX_PERF": "1", **extra_env}
    before = {name: rec.get("recorded_at") for name, rec in _newest_records().items()}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(PERF_DIR), "-k", selector, "-q", "-p", "no:warnings"],
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout[-4000:])
        raise SystemExit(f"benchmark run failed ({selector})")
    fresh = {}
    for name, record in _newest_records().items():
        # Only records this run produced: a benchmark that did not run must
        # not contribute a stale number to a reference.
        if record.get("recorded_at") != before.get(name):
            fresh[name] = record
    return fresh


def collect(
    reps: int,
    selector: str,
    extra_env: dict[str, str],
) -> dict[str, dict[str, list[float]]]:
    samples: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for rep in range(1, reps + 1):
        print(f"  run {rep}/{reps} …", flush=True)
        for test, record in _run_once(selector, extra_env).items():
            key = entry_key(test, record)
            for metric, value in metrics_from_record(test, record).items():
                samples[key][metric].append(value)
    return samples


def bless(samples, path: Path) -> None:
    entries = {}
    for key, metrics in samples.items():
        test = key.split("/")[0]
        entries[key] = {
            "samples": len(next(iter(metrics.values()), [])),
            "metrics": {
                metric: {
                    "median": round(sorted(values)[len(values) // 2], 2),
                    "tolerance": tolerance_from_samples(values),
                    "better": METRIC_SPECS[test][metric][0],
                }
                for metric, values in sorted(metrics.items())
            },
        }
    payload = {
        "blessed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "note": (
            "Medians from a deliberate run on quiet hardware. Refresh only on purpose "
            "(`make bench-bless`) -- a baseline that follows the code is not a baseline."
        ),
        "entries": entries,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"\nwrote {path} ({len(entries)} entries)")


def check(samples, strict: bool) -> int:
    reference = load_reference()
    print(f"\nreference blessed at {reference['blessed_at']}\n")
    regressions = 0
    for key in sorted(samples):
        print(key)
        try:
            comparisons = compare(samples[key], reference, key)
        except KeyError as exc:
            print(f"  no reference entry -- {exc}")
            continue
        for comparison in comparisons:
            print("  " + comparison.render())
            regressions += bool(comparison.regressed)
    if regressions:
        print(f"\n{regressions} metric(s) outside tolerance.")
        return 1 if strict else 0
    print("\nno drift outside tolerance.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["check", "bless"])
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--select", default="b1 or b2 or b3")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    extra_env = {}
    if args.database_url:
        extra_env["FLUX_PERF_DATABASE_URL"] = args.database_url
    if args.reps < 3:
        print("warning: fewer than 3 reps cannot separate a change from run-to-run spread")

    print(f"{args.mode}: {args.reps} runs of '{args.select}'")
    samples = collect(args.reps, args.select, extra_env)
    if not samples:
        raise SystemExit("no benchmark records produced")

    if args.mode == "bless":
        bless(samples, REFERENCE_PATH)
        return 0
    return check(samples, strict=bool(os.environ.get("FLUX_PERF_STRICT")))


if __name__ == "__main__":
    raise SystemExit(main())
