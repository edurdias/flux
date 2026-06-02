"""End-to-end cross-process replay guard.

Reproduces the P0: a workflow runs and pauses in one process, then resumes in a
*different* process (different ``PYTHONHASHSEED``). Tasks completed before the
pause must NOT re-execute. Before ``task_id`` was made deterministic, the
resuming process recomputed different ids, the replay short-circuit at
``flux/task.py`` missed, and every pre-pause task ran a second time.

The two phases run as separate subprocesses sharing one SQLite database and one
side-effect counter file, with explicitly different hash seeds so the guard
catches the regression deterministically rather than relying on the parent and
child happening to differ.
"""

from __future__ import annotations

import os
import subprocess
import sys

_MODULE = """
from __future__ import annotations

import os
import sys

from flux import ExecutionContext, task, workflow
from flux.tasks import pause


def _bump():
    with open(os.environ["COUNTER_FILE"], "a") as f:
        f.write("x")


@task
async def step_a(x):
    _bump()
    return x


@task
async def step_b(x):
    _bump()
    return x


@task
async def step_c(x):
    _bump()
    return x


@workflow
async def wf(ctx: ExecutionContext):
    await step_a(1)
    await step_b(2)
    await pause("gate")
    await step_c(3)
    return "done"


if __name__ == "__main__":
    if sys.argv[1] == "run":
        ctx = wf.run()
        assert ctx.is_paused, f"expected paused, got {ctx.state}"
        print(ctx.execution_id)
    else:
        ctx = wf.run(execution_id=sys.argv[2])
        assert ctx.has_succeeded, f"expected succeeded, got {ctx.state}"
        print("OK")
"""


def _env(db, counter, seed):
    return {
        **os.environ,
        "PYTHONHASHSEED": str(seed),
        "FLUX_DATABASE_URL": f"sqlite:///{db}",
        "COUNTER_FILE": str(counter),
        "FLUX_SECURITY__AUTH__ENABLED": "false",
        "FLUX_WORKERS__BOOTSTRAP_TOKEN": "test-bootstrap-token",
        "FLUX_SECURITY__ENCRYPTION__ENCRYPTION_KEY": "test-encryption-key-0123456789ab",
    }


def test_completed_tasks_not_reexecuted_on_cross_process_resume(tmp_path):
    mod = tmp_path / "xproc_wf.py"
    mod.write_text(_MODULE)
    db = tmp_path / "flux.db"
    counter = tmp_path / "counter.txt"
    counter.write_text("")

    # Phase A (seed 0): run until pause. step_a + step_b execute once each.
    a = subprocess.run(
        [sys.executable, str(mod), "run"],
        env=_env(db, counter, 0),
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert a.returncode == 0, f"run phase failed:\n{a.stderr}"
    exec_id = a.stdout.strip().splitlines()[-1]
    assert counter.read_text() == "xx", "pre-pause tasks should have run exactly once"

    # Phase B (seed 1, a different hash seed): resume in a fresh process.
    # step_a + step_b must replay (no new side effect); only step_c runs.
    b = subprocess.run(
        [sys.executable, str(mod), "resume", exec_id],
        env=_env(db, counter, 1),
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert b.returncode == 0, f"resume phase failed:\n{b.stderr}"

    side_effects = counter.read_text()
    assert side_effects == "xxx", (
        f"expected 3 side effects (step_a, step_b once + step_c), got {len(side_effects)}: "
        "completed tasks re-executed on cross-process resume"
    )
