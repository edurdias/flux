"""The runner child's import graph, asserted end-to-end (issue #233).

The unit guards in tests/flux/test_child_import_graph.py prove the modules
import lean in isolation; this proves the property where it matters — inside
the real child a live worker spawns, with the full dispatch/claim/checkpoint
machinery in front of it.
"""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def test_user_code_runs_without_the_persistence_graph(cli):
    cli.register(str(FIXTURES / "leanness_workflow.py"))

    result = cli.run("leanness_probe", "null")

    assert result["state"] == "COMPLETED"
    loaded = result["output"]
    assert loaded == {"sqlalchemy": False, "flux_models": False}, (
        f"the child carried the persistence graph into user code: {loaded}"
    )
