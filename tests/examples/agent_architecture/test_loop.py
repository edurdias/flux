from __future__ import annotations

from examples.agent_architecture.loop import CHECKLIST, evidence_loop
from flux.domain.events import ExecutionEventType


def test_converges_within_budget():
    """Two defects, one fixed per revision — evidence is clean on attempt 3."""
    ctx = evidence_loop.run({"defect_count": 2, "max_attempts": 3})

    assert ctx.has_finished and ctx.has_succeeded
    assert ctx.output["status"] == "verified"
    assert ctx.output["attempts"] == 3
    assert ctx.output["escalated"] is False
    assert ctx.output["document"]["unresolved"] == []


def test_zero_defects_verified_on_first_attempt():
    ctx = evidence_loop.run({"defect_count": 0, "max_attempts": 3})

    assert ctx.has_succeeded
    assert ctx.output == {
        "status": "verified",
        "attempts": 1,
        "escalated": False,
        "document": ctx.output["document"],
    }


def test_exhausted_budget_escalates_to_human():
    """Six defects cannot converge in three attempts — the loop must pause
    on the named escalation path, exposing the outstanding evidence."""
    ctx = evidence_loop.run({"defect_count": 6, "max_attempts": 3})

    assert ctx.is_paused
    assert not ctx.has_finished

    pause_events = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
    assert len(pause_events) == 1
    assert pause_events[0].value["name"] == "verification_budget_exhausted"
    assert pause_events[0].value["output"]["attempts"] == 3
    # Two revisions fixed two of six defects (no revision after the final
    # verify); four remain outstanding.
    assert len(pause_events[0].value["output"]["outstanding"]) == 4


def test_human_resolution_is_also_verified():
    """The human's fix goes through the verifier too — full resolution
    succeeds, partial resolution is rejected on evidence."""
    ctx = evidence_loop.run({"defect_count": 6, "max_attempts": 3})
    assert ctx.is_paused

    resumed = evidence_loop.resume(ctx.execution_id, {"resolved": list(CHECKLIST)})
    assert resumed.has_succeeded
    assert resumed.output["status"] == "verified"
    assert resumed.output["escalated"] is True

    # A second run whose human resolution fixes only one outstanding check
    # must come back rejected, with the remaining evidence in the output.
    ctx = evidence_loop.run({"defect_count": 6, "max_attempts": 3})
    assert ctx.is_paused
    outstanding = next(e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED).value[
        "output"
    ]["outstanding"]

    resumed = evidence_loop.resume(ctx.execution_id, {"resolved": outstanding[:1]})
    assert resumed.has_succeeded
    assert resumed.output["status"] == "rejected"
    assert resumed.output["outstanding"] == outstanding[1:]
