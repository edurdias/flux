"""The transactional outbox: one delivery row per matching hook, written in
the same transaction that records the event.

The outbox exists so an event and its obligations share a fate -- both
commit or neither does -- while the delivery itself happens later, off the
write path, so no hook can slow or fail a checkpoint. These tests pin both
halves: the shared fate (commit, rollback, re-sent checkpoint) and the fast
path that must not touch the database when nothing subscribes.
"""

from __future__ import annotations

import pytest

from flux.config import Configuration
from flux.context_managers import ContextManager
from flux.domain.events import ExecutionEvent, ExecutionEventType, ExecutionState
from flux.domain.execution_context import ExecutionContext
from flux.hooks.outbox import enqueue
from flux.hooks.registry import HookRegistry
from flux.models import DatabaseRepository, HookDeliveryModel, RepositoryFactory
from flux.unit_of_work import UnitOfWork
from flux.worker_registry import WorkerInfo


@pytest.fixture
def isolated_db(tmp_path):
    """A real Configuration, unlike tests/flux/conftest.py's MagicMock patch.

    ``test_disabled_by_config_enqueues_nothing`` asserts on ``[flux.hooks]
    enabled``: against a MagicMock every attribute reads truthy and
    ``override`` merely records a call, so the disabled path could never be
    exercised. The autouse ``_seed_required_config`` fixture resets the
    configuration singleton at teardown.
    """
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'outbox.db'}")
    DatabaseRepository._engines.clear()
    yield
    DatabaseRepository._engines.clear()


def _hook(*, selectors: list[str], workflow_ref: str, name: str = "notify"):
    return HookRegistry.create().create_hook(
        name=name,
        selectors=selectors,
        workflow_ref=workflow_ref,
        principal_id="p-1",
        owner_ref="admin",
    )


def _paused_execution(
    *,
    execution_input: dict | None = None,
    reason: str = "gate",
) -> ExecutionContext:
    return ExecutionContext(
        workflow_id="wf-1",
        workflow_namespace="release",
        workflow_name="pipeline",
        input=execution_input if execution_input is not None else {"env": "prod"},
        state=ExecutionState.PAUSED,
        events=[
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_PAUSED,
                source_id="wf-1",
                name="pipeline",
                value={"reason": reason},
            ),
        ],
    )


def _execution_with_awaiting_approval(task_name: str) -> ExecutionContext:
    return ExecutionContext(
        workflow_id="wf-1",
        workflow_namespace="release",
        workflow_name="pipeline",
        input={"env": "prod"},
        state=ExecutionState.RUNNING,
        events=[
            ExecutionEvent(
                type=ExecutionEventType.TASK_AWAITING_APPROVAL,
                source_id="call-1",
                name=task_name,
                value={"target": "prod"},
            ),
        ],
    )


def _deliveries() -> list[HookDeliveryModel]:
    with RepositoryFactory.create_repository().session() as session:
        return session.query(HookDeliveryModel).all()


class TestOutbox:
    def test_a_committed_state_write_leaves_one_delivery(self, isolated_db):
        _hook(selectors=["execution:*:*:paused"], workflow_ref="ops/notify")
        manager = ContextManager.create()
        ctx = _paused_execution()

        manager.save(ctx)

        rows = _deliveries()
        assert len(rows) == 1
        assert rows[0].status == "pending"
        assert rows[0].payload["event"]["type"] == "paused"

    def test_a_rolled_back_write_leaves_none(self, isolated_db):
        """The enqueue is in the caller's transaction: no event, no delivery.

        The row has to be shown to exist *inside* the transaction first,
        otherwise the assertion after it passes just as well against an
        enqueue that never ran.
        """
        _hook(selectors=["execution:*"], workflow_ref="ops/notify")
        with UnitOfWork() as uow:
            ContextManager.create().save(_paused_execution(), uow=uow)
            assert uow.session.query(HookDeliveryModel).count() == 1
            uow.rollback()

        assert _deliveries() == []

    def test_saving_twice_does_not_duplicate(self, isolated_db):
        """Checkpoints re-send events; the unique constraint absorbs it."""
        _hook(selectors=["execution:*"], workflow_ref="ops/notify")
        ctx = _paused_execution()
        ContextManager.create().save(ctx)
        ContextManager.create().save(ctx)

        assert len(_deliveries()) == 1

    def test_a_duplicate_delivery_does_not_poison_the_transaction(self, isolated_db):
        """What makes the re-send above safe, reached directly.

        A re-sent checkpoint normally stops at the event dedup, so the
        constraint only fires when two writers race the same event. Enqueuing
        the same pair twice in one transaction is that race, minus the
        timing: the duplicate must skip on its own savepoint and leave the
        execution write it rode in with intact.
        """
        _hook(selectors=["execution:*"], workflow_ref="ops/notify")
        ctx = _paused_execution()
        with UnitOfWork() as uow:
            ContextManager.create().save(ctx, uow=uow)
            assert enqueue(uow.session, ctx, ctx.events) == 0
            uow.commit()

        assert len(_deliveries()) == 1
        assert ContextManager.create().get(ctx.execution_id).state == ExecutionState.PAUSED

    def test_no_hooks_means_no_work(self, isolated_db, monkeypatch):
        """The fast path must not query or match when nothing subscribes."""
        calls = []
        monkeypatch.setattr(HookRegistry, "matches", lambda self, e: calls.append(e) or [])

        ContextManager.create().save(_paused_execution())

        assert calls == []

    def test_task_events_enqueue_under_the_task_domain(self, isolated_db):
        _hook(selectors=["task:*:*:promote_prod:awaiting_approval"], workflow_ref="ops/notify")

        ContextManager.create().save(_execution_with_awaiting_approval("promote_prod"))

        [row] = _deliveries()
        assert row.payload["event"]["task_name"] == "promote_prod"

    def test_each_event_in_a_delta_keys_on_its_own_transition(self, isolated_db):
        """A checkpoint carries every unacknowledged event, not one
        transition. Two workflow events in one save are two transitions, and
        each delivery must describe the event it was made for -- not the
        state the execution happens to have reached by the time it lands."""
        _hook(selectors=["execution:*"], workflow_ref="ops/notify")
        manager = ContextManager.create()
        ctx = _paused_execution()
        manager.save(ctx)

        ctx.start(ctx.execution_id)
        ctx.complete(ctx.execution_id, "done")
        manager.save(ctx)

        by_type = {row.payload["event"]["type"]: row.payload for row in _deliveries()}
        assert set(by_type) == {"paused", "running", "completed"}
        assert by_type["running"]["event"]["state"] == "running"
        started = next(e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_STARTED)
        assert by_type["running"]["event"]["occurred_at"] == started.time.isoformat()
        assert by_type["running"]["event"]["value"] == started.value

    def test_dispatch_transitions_enqueue_too(self, isolated_db):
        """Every path that persists events feeds the outbox, not just the
        checkpoint ones: an `execution:*` hook means every transition, and
        claim/dispatch write theirs outside save()."""
        _hook(selectors=["execution:*"], workflow_ref="ops/notify")
        manager = ContextManager.create()
        ctx = ExecutionContext(
            workflow_id="wf-1",
            workflow_namespace="release",
            workflow_name="pipeline",
            input={"env": "prod"},
        )
        manager.save(ctx)

        manager.claim(ctx.execution_id, WorkerInfo(name="worker-1"))

        assert [row.payload["event"]["type"] for row in _deliveries()] == ["claimed"]

    def test_the_delivery_records_its_place_in_the_chain(self, isolated_db):
        """hop is stamped here, not at drain time: this is where the parent
        execution's input is in hand. 0 for an event from an execution no
        hook started, parent + 1 for one that a delivery itself started."""
        _hook(selectors=["execution:*"], workflow_ref="ops/notify")
        manager = ContextManager.create()

        manager.save(_paused_execution())
        manager.save(
            _paused_execution(
                execution_input={"hook": "notify", "hop": 0},
                reason="started-by-a-hook",
            ),
        )

        assert sorted(row.payload["hop"] for row in _deliveries()) == [0, 1]

    def test_disabled_by_config_enqueues_nothing(self, isolated_db):
        _hook(selectors=["execution:*"], workflow_ref="ops/notify")
        Configuration.get().override(hooks={"enabled": False})

        ContextManager.create().save(_paused_execution())

        assert _deliveries() == []
