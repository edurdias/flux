from __future__ import annotations

from examples.approvals.standing_grant import rollout_workflow


def test_first_region_pauses_on_approval():
    ctx = rollout_workflow.run(["us-east", "eu-west"])
    assert ctx.is_paused
    assert not ctx.has_finished


def test_standing_grant_covers_remaining_regions():
    """Approve the first gate with scope=execution (what ``approve --always``
    does) and expect one resume to roll out every region — later gates
    auto-approve, each leaving a materialized audit row."""
    from flux.approvals import ApprovalManager
    from flux.models import ApprovalStatus
    from flux.unit_of_work import UnitOfWork

    ctx = rollout_workflow.run(["us-east", "eu-west", "ap-south"])
    assert ctx.is_paused

    mgr = ApprovalManager()
    pending = mgr.list(execution_id=ctx.execution_id, status=ApprovalStatus.PENDING)
    assert len(pending) == 1
    row = pending[0]

    with UnitOfWork() as uow:
        mgr.decide(
            row.execution_id,
            row.task_call_id,
            approver_subject="alice",
            approver_provider="oidc",
            approved=True,
            reason="rollout approved",
            uow=uow,
            scope="execution",
        )
        uow.commit()

    resumed = rollout_workflow.run(execution_id=ctx.execution_id)
    assert resumed.has_succeeded
    assert resumed.output == [
        "deployed to us-east",
        "deployed to eu-west",
        "deployed to ap-south",
    ]

    # One operator decision + one materialized row per covered gate.
    rows = mgr.list(execution_id=ctx.execution_id, status=ApprovalStatus.APPROVED, limit=None)
    assert len(rows) == 3
    assert sum(1 for r in rows if (r.scope or "call") == "execution") == 1
    assert sum(1 for r in rows if r.reason == "standing grant") == 2
