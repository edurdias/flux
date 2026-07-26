from __future__ import annotations

from examples.agent_architecture.graph import document_graph

WELL_SOURCED = (
    "Flux persists every state transition as an execution event. Sources: flux/domain/events.py."
)


def test_well_sourced_document_is_published():
    """Both branches run, the join merges them, the gate opens, publish runs."""
    ctx = document_graph.run({"document": WELL_SOURCED})

    assert ctx.has_finished and ctx.has_succeeded
    assert ctx.output["published"] is True
    assert ctx.output["quality"] == 0.9
    # The digest carries the summary (branch 2) and keywords (branch 1),
    # proving the join saw both fan-out branches.
    assert ctx.output["digest"].startswith("Flux persists every state transition")
    assert "keywords:" in ctx.output["digest"]


def test_unsourced_document_is_rejected_by_the_gate():
    """The conditional edge closes: publish never runs, the graph yields None,
    and the workflow reports an explicit non-publication."""
    ctx = document_graph.run({"document": "Trust me, this is fine."})

    assert ctx.has_finished and ctx.has_succeeded
    assert ctx.output == {
        "published": False,
        "reason": "quality gate rejected the document",
    }
