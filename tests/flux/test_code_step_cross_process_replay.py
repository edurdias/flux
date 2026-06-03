"""Cross-process determinism guard for code-step hashing.

A code step is persisted as its source string; on replay (possibly in a
different worker process) the same source must produce the same hash so the
step's host task_id is stable. code_hash uses SHA-256, so it must be identical
across processes with different PYTHONHASHSEED values.
"""

from __future__ import annotations

import os
import subprocess
import sys


def test_code_hash_stable_across_processes():
    code = "lambda: side_effect()"
    runner = f"from flux.tasks.ai.code_sandbox import code_hash\nprint(code_hash({code!r}))\n"
    outs = set()
    for seed in ("0", "1", "42"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.check_output([sys.executable, "-c", runner], env=env, text=True).strip()
        outs.add(out)
    assert len(outs) == 1, f"code_hash diverged across processes: {outs}"
