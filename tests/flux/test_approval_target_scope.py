"""Target-scoped standing grants (issue #143).

``approve --always`` (scope="execution") authorizes every later call of a
task name. A ``scope="target"`` grant authorizes only calls whose declared
``approval_target`` argument resolves to the same value — "this task,
against this exact target, for this execution". Minting is fail-closed: a
task that declares no target, or a call that bound no scalar value,
cannot mint or match a target-scoped grant.
"""

from __future__ import annotations

import pytest

from flux import ExecutionContext
from flux.approvals import ApprovalManager, ApprovalStatus
from flux.task import task as task_decorator
from flux.unit_of_work import UnitOfWork
from flux.workflow import workflow

pytestmark = pytest.mark.usefixtures("isolated_db")


@pytest.fixture
def isolated_db(tmp_path):
    from flux.config import Configuration
    from flux.models import DatabaseRepository

    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'approvals.db'}")
    DatabaseRepository._engines.clear()
    yield
    DatabaseRepository._engines.clear()


def _decide_single_pending(mgr, execution_id, scope, approved=True):
    pending = mgr.list(execution_id=execution_id, status=ApprovalStatus.PENDING)
    assert len(pending) == 1
    with UnitOfWork() as uow:
        row = mgr.decide(
            pending[0].execution_id,
            pending[0].task_call_id,
            approver_subject="alice",
            approver_provider="oidc",
            approved=approved,
            reason="reviewed",
            uow=uow,
            scope=scope,
        )
        uow.commit()
    return row


class TestTargetResolution:
    def test_positional_argument_resolves(self):
        @task_decorator.with_options(requires_approval=True, approval_target="recipient")
        async def send_email(recipient: str, subject: str = "hi"):
            return recipient

        assert send_email._resolve_approval_target(("ops@example.com",), {}) == "ops@example.com"

    def test_keyword_argument_resolves(self):
        @task_decorator.with_options(requires_approval=True, approval_target="recipient")
        async def send_email(recipient: str):
            return recipient

        assert (
            send_email._resolve_approval_target((), {"recipient": "ops@example.com"})
            == "ops@example.com"
        )

    def test_no_declaration_binds_nothing(self):
        @task_decorator.with_options(requires_approval=True)
        async def send_email(recipient: str):
            return recipient

        assert send_email._resolve_approval_target(("ops@example.com",), {}) is None

    @pytest.mark.parametrize("value", [None, "", "   ", ["a"], {"a": 1}, True])
    def test_unbindable_values_bind_nothing(self, value):
        @task_decorator.with_options(requires_approval=True, approval_target="recipient")
        async def send_email(recipient):
            return recipient

        assert send_email._resolve_approval_target((value,), {}) is None

    def test_numeric_scalars_bind_as_text(self):
        @task_decorator.with_options(requires_approval=True, approval_target="port")
        async def open_port(port: int):
            return port

        assert open_port._resolve_approval_target((8080,), {}) == "8080"


def _gated_workflow():
    calls = []

    @task_decorator.with_options(requires_approval=True, approval_target="recipient")
    async def send_email(recipient: str):
        calls.append(recipient)
        return f"sent-{recipient}"

    @workflow
    async def wf_target(ctx: ExecutionContext):
        first = await send_email("ops@example.com")
        second = await send_email("ops@example.com")
        third = await send_email("eve@example.com")
        return [first, second, third]

    return wf_target, calls


class TestTargetGrantFlow:
    def test_same_target_auto_approves_different_target_pauses(self):
        wf_target, calls = _gated_workflow()
        mgr = ApprovalManager()

        ctx = wf_target.run()  # pauses at the first ops@ call
        assert ctx.is_paused
        pending = mgr.list(execution_id=ctx.execution_id, status=ApprovalStatus.PENDING)
        assert pending[0].target_value == "ops@example.com"

        _decide_single_pending(mgr, ctx.execution_id, scope="target")

        # Resume: the second ops@ call is covered by the grant; the eve@ call
        # binds a different target and pauses again.
        ctx = wf_target.run(execution_id=ctx.execution_id)
        assert ctx.is_paused
        assert calls == ["ops@example.com", "ops@example.com"]

        pending = mgr.list(execution_id=ctx.execution_id, status=ApprovalStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].target_value == "eve@example.com"

        # Approving the eve@ call normally completes the workflow.
        _decide_single_pending(mgr, ctx.execution_id, scope="call")
        ctx = wf_target.run(execution_id=ctx.execution_id)
        assert ctx.has_succeeded
        assert ctx.output == [
            "sent-ops@example.com",
            "sent-ops@example.com",
            "sent-eve@example.com",
        ]

    def test_auto_approval_audit_row_names_the_grant(self):
        wf_target, _ = _gated_workflow()
        mgr = ApprovalManager()

        ctx = wf_target.run()
        grant = _decide_single_pending(mgr, ctx.execution_id, scope="target")
        ctx = wf_target.run(execution_id=ctx.execution_id)

        rows = mgr.list(execution_id=ctx.execution_id, status=ApprovalStatus.APPROVED)
        audit = [r for r in rows if r.reason and r.reason.startswith("standing grant")]
        assert len(audit) == 1
        assert grant.id in audit[0].reason
        assert "target='ops@example.com'" in audit[0].reason

    def test_execution_scope_still_covers_every_target(self):
        wf_target, calls = _gated_workflow()
        mgr = ApprovalManager()

        ctx = wf_target.run()
        _decide_single_pending(mgr, ctx.execution_id, scope="execution")

        ctx = wf_target.run(execution_id=ctx.execution_id)
        assert ctx.has_succeeded
        assert calls == ["ops@example.com", "ops@example.com", "eve@example.com"]


class TestFailClosedMinting:
    def test_target_scope_without_bound_value_is_rejected(self):
        """A task with no approval_target declaration binds nothing, so
        scope='target' must be rejected loudly, never silently widened."""
        calls = []

        @task_decorator.with_options(requires_approval=True)
        async def plain_gate(step: str):
            calls.append(step)
            return step

        @workflow
        async def wf_plain(ctx: ExecutionContext):
            return await plain_gate("one")

        mgr = ApprovalManager()
        ctx = wf_plain.run()
        assert ctx.is_paused

        pending = mgr.list(execution_id=ctx.execution_id, status=ApprovalStatus.PENDING)
        assert pending[0].target_value is None
        with pytest.raises(ValueError, match="no bound target"):
            _decide_single_pending(mgr, ctx.execution_id, scope="target")

    def test_target_scope_rejection_is_invalid(self):
        wf_target, _ = _gated_workflow()
        mgr = ApprovalManager()
        ctx = wf_target.run()
        with pytest.raises(ValueError, match="only valid for approvals"):
            _decide_single_pending(mgr, ctx.execution_id, scope="target", approved=False)

    def test_unknown_scope_is_rejected(self):
        wf_target, _ = _gated_workflow()
        mgr = ApprovalManager()
        ctx = wf_target.run()
        with pytest.raises(ValueError, match="scope must be"):
            _decide_single_pending(mgr, ctx.execution_id, scope="wildcard")
