from __future__ import annotations

from examples.approvals.deploy_gate import deploy_workflow


def test_staging_runs_unattended():
    """Predicate ``environment == 'prod'`` is False for staging — workflow
    should complete without pausing."""
    ctx = deploy_workflow.run({"environment": "staging"})
    assert ctx.has_succeeded
    assert ctx.output == {
        "status": "ok",
        "deployment": "deployed artifact-staging-1.0.0 to staging",
    }


def test_prod_pauses_on_approval():
    """Production deploy should pause on the gated task."""
    ctx = deploy_workflow.run({"environment": "prod"})
    assert ctx.is_paused
    assert not ctx.has_finished


def test_prod_completes_after_approval():
    """Approve the pending request, re-run with the same execution_id, and
    expect the workflow to finish successfully with the deploy output."""
    from flux.approvals import ApprovalManager
    from flux.unit_of_work import UnitOfWork

    ctx = deploy_workflow.run({"environment": "prod"})
    assert ctx.is_paused

    mgr = ApprovalManager()
    pending = mgr.list(execution_id=ctx.execution_id)
    assert len(pending) == 1
    row = pending[0]

    with UnitOfWork() as uow:
        mgr.decide(
            row.execution_id,
            row.task_call_id,
            approver_subject="alice",
            approver_provider="oidc",
            approved=True,
            reason="ok",
            uow=uow,
        )
        uow.commit()

    resumed = deploy_workflow.run(execution_id=ctx.execution_id)
    assert resumed.has_succeeded
    assert resumed.output == {
        "status": "ok",
        "deployment": "deployed artifact-prod-1.0.0 to prod",
    }
