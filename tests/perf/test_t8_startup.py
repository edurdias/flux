"""T8 — startup / image-load baseline for the three process roles.

Every Flux deployment pays three cold-start costs: the server process, the
worker process, and — for container runners, on every execution — the runner
child. T8 measures each as bare-interpreter import cost (t8a) and, when a
Flux image is available, as in-container load cost (t8b), and records the
numbers as the baseline later work is judged against (issue #233 cut the
child from ~600ms to ~70ms; these tests are how the next regression or
improvement becomes visible).

Correctness gate: every probe must run and report. Performance gates are
soft per harness policy — budgets generous enough for a shared runner,
hard-failing only under FLUX_PERF_STRICT=1.

The always-on structural guards (which modules may load per role) live in
tests/flux/test_startup_import_budget.py; T8 owns the wall-clock numbers.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import sys

import pytest

from fixtures.harness.profile import params, profile_name, soft_gate
from fixtures.harness.report import write_run

ROLES = {
    "server": "flux.server",
    "worker": "flux.worker",
    "execution-child": "flux.runners.child",
}

_PROBE = (
    "import time; t0=time.perf_counter(); import {module}; print((time.perf_counter()-t0)*1000)"
)
_MODCOUNT = "import sys, {module}; print(len(sys.modules))"


def _median_import_ms(argv_prefix: list[str], module: str, samples: int) -> dict:
    times = []
    for _ in range(samples):
        out = subprocess.run(
            [*argv_prefix, "-c", _PROBE.format(module=module)],
            capture_output=True,
            text=True,
            check=True,
        )
        times.append(float(out.stdout.strip().splitlines()[-1]))
    modules = subprocess.run(
        [*argv_prefix, "-c", _MODCOUNT.format(module=module)],
        capture_output=True,
        text=True,
        check=True,
    )
    return {
        "median_ms": round(statistics.median(times), 1),
        "min_ms": round(min(times), 1),
        "max_ms": round(max(times), 1),
        "samples": samples,
        "modules_loaded": int(modules.stdout.strip().splitlines()[-1]),
    }


def _heavy_hitters(module: str, top: int = 5) -> list[dict]:
    """Top self-time imports from -X importtime, flux's own tree excluded."""
    out = subprocess.run(
        [sys.executable, "-X", "importtime", "-c", f"import {module}"],
        capture_output=True,
        text=True,
        check=True,
    )
    rows = []
    for line in out.stderr.splitlines():
        parts = line.split("|")
        if len(parts) != 3 or "import time" not in parts[0]:
            continue
        try:
            self_us = int(parts[0].split(":")[1].strip())
        except ValueError:
            continue
        name = parts[2].strip()
        if name.startswith("flux"):
            continue
        rows.append({"module": name, "self_ms": round(self_us / 1000, 1)})
    rows.sort(key=lambda r: -r["self_ms"])
    return rows[:top]


def test_t8a_bare_interpreter_import_cost():
    p = params("t8")
    results: dict[str, dict] = {}
    verdicts: dict[str, bool] = {}
    for role, module in ROLES.items():
        measured = _median_import_ms([sys.executable], module, p["samples"])
        measured["heavy_hitters"] = _heavy_hitters(module)
        budget = p["budget_ms"][module]
        verdicts[role] = soft_gate(
            measured["median_ms"] <= budget,
            f"{role} ({module}) import median {measured['median_ms']}ms "
            f"exceeds the {budget}ms budget",
        )
        measured["budget_ms"] = budget
        measured["within_budget"] = verdicts[role]
        results[role] = measured

    write_run(
        "T8",
        f"bare-import-{profile_name()}",
        {
            "gate": "all roles measured (hard); medians within budget (soft)",
            "measured": "; ".join(
                f"{role} {r['median_ms']}ms/{r['budget_ms']}ms" for role, r in results.items()
            ),
            "passed": all(verdicts.values()),
            "sealed": False,
            "profile": profile_name(),
            "roles": results,
        },
    )

    # The hard gate: every role produced a number.
    assert set(results) == set(ROLES)


@pytest.mark.skipif(
    not os.environ.get("FLUX_TEST_DOCKER_IMAGE") or not shutil.which("docker"),
    reason="FLUX_TEST_DOCKER_IMAGE not set or docker unavailable",
)
def test_t8b_container_image_load_cost():
    """The same three roles, loaded inside the Flux image: what a container
    deployment actually pays before the process is useful. Includes runc +
    interpreter start, so the child's number here is the per-execution floor
    for the docker runners."""
    image = os.environ["FLUX_TEST_DOCKER_IMAGE"]
    p = params("t8")
    prefix = ["docker", "run", "--rm", image, "python"]
    results: dict[str, dict] = {}
    verdicts: dict[str, bool] = {}
    for role, module in ROLES.items():
        measured = _median_import_ms(prefix, module, p["samples"])
        budget = p["container_budget_ms"][module]
        verdicts[role] = soft_gate(
            measured["median_ms"] <= budget,
            f"{role} ({module}) container load median {measured['median_ms']}ms "
            f"exceeds the {budget}ms budget",
        )
        measured["budget_ms"] = budget
        measured["within_budget"] = verdicts[role]
        results[role] = measured

    write_run(
        "T8",
        f"container-load-{profile_name()}",
        {
            "gate": "all roles measured (hard); medians within budget (soft)",
            "measured": "; ".join(
                f"{role} {r['median_ms']}ms/{r['budget_ms']}ms" for role, r in results.items()
            ),
            "passed": all(verdicts.values()),
            "sealed": False,
            "profile": profile_name(),
            "image": image,
            "roles": results,
        },
    )

    assert set(results) == set(ROLES)


def test_t8c_import_graph_manifest():
    """Not a timing test: records WHICH heavyweight third-party packages each
    role loads, so a layering regression shows up as a diff in the results
    record even when wall time hides it in runner noise."""
    heavies = ["sqlalchemy", "fastapi", "uvicorn", "pydantic", "httpx", "Crypto", "alembic"]
    manifest: dict[str, dict[str, bool]] = {}
    for role, module in ROLES.items():
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import sys, {module}; print(__import__('json').dumps("
                f"{{m: any(k == m or k.startswith(m + '.') for k in sys.modules) for m in {heavies!r}}}))",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        manifest[role] = json.loads(out.stdout.strip().splitlines()[-1])

    write_run(
        "T8",
        f"import-manifest-{profile_name()}",
        {
            "gate": "manifest recorded; structural assertions live in the unit tree",
            "measured": json.dumps(manifest),
            "passed": True,
            "sealed": False,
            "profile": profile_name(),
            "manifest": manifest,
        },
    )

    # The structural invariants that must hold regardless of timing:
    assert manifest["execution-child"]["sqlalchemy"] is False
    assert manifest["worker"]["sqlalchemy"] is False
    assert manifest["worker"]["fastapi"] is False
    assert manifest["execution-child"]["fastapi"] is False
