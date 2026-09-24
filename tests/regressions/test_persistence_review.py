from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from flux.agents.manager import AgentManager
from flux.agents.types import AgentDefinition
from flux.config import Configuration
from flux.config_manager import DatabaseConfigManager
from flux.context_managers import ContextManager
from flux.domain.events import ExecutionEvent, ExecutionEventType, ExecutionState
from flux.domain.execution_context import ExecutionContext
from flux.hooks import registry as registry_module
from flux.hooks.registry import HookRegistry
from flux.models import DatabaseRepository, HookDeliveryModel, RepositoryFactory


@pytest.fixture(autouse=True)
def isolated_persistence(tmp_path):
    Configuration.get().override(
        database_url=f"sqlite:///{tmp_path / 'review.db'}",
        home=str(tmp_path),
        security={"encryption": {"encryption_key": "review-only-key"}, "auth": {"enabled": False}},
        workers={"bootstrap_token": "review-only-token"},
    )
    DatabaseRepository._engines.clear()
    registry_module._snapshot = None
    registry_module._snapshot_loaded_at = None
    yield
    for engine in DatabaseRepository._engines.values():
        engine.dispose()
    DatabaseRepository._engines.clear()
    registry_module._snapshot = None
    registry_module._snapshot_loaded_at = None
    Configuration.get().reset()


def test_outbox_failure_rolls_back_and_resend_restores_delivery():
    HookRegistry.create().create_hook(
        name="review-hook",
        selectors=["execution:*"],
        workflow_ref="ops/notify",
        principal="review-principal",
        owner_ref="admin",
    )
    ctx = ExecutionContext(
        workflow_id="review-wf",
        workflow_namespace="default",
        workflow_name="review-wf",
        state=ExecutionState.PAUSED,
        events=[
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_PAUSED,
                source_id="review-wf",
                name="review-wf",
                value="gate",
            ),
        ],
    )
    manager = ContextManager.create()
    with patch.object(HookRegistry, "has_any", side_effect=RuntimeError("injected read outage")):
        with pytest.raises(RuntimeError, match="injected read outage"):
            manager.save(ctx)
    manager.save(ctx)
    with RepositoryFactory.create_repository().session() as session:
        assert session.query(HookDeliveryModel).count() == 1


async def test_agent_delete_cleanup_failure_can_be_retried():
    definition = AgentDefinition(name="review-agent", model="test/model", system_prompt="Review")
    manager = AgentManager.current()
    manager.create(definition)
    HookRegistry.create().create_hook(
        name="review-agent-hook",
        selectors=["execution:*"],
        workflow_ref="ops/notify",
        principal="review-principal",
        owner_type="agent",
        owner_ref=definition.name,
    )
    with patch.object(DatabaseConfigManager, "remove", side_effect=RuntimeError("injected outage")):
        with pytest.raises(RuntimeError, match="injected outage"):
            manager.delete(definition.name)
    manager.delete(definition.name)
    with pytest.raises(ValueError, match="not found"):
        await DatabaseConfigManager().get(["agent:review-agent"])
    assert (
        len(
            HookRegistry.create().list_owned_hooks(
                owner_type="agent",
                owner_ref=definition.name,
            ),
        )
        == 0
    )


@pytest.mark.parametrize("overlap", ["skip", "allow"])
@pytest.mark.parametrize("boundary", ["session", "advance"])
async def test_schedule_creation_failure_leaves_no_unlinked_execution(overlap, boundary):
    from flux.catalogs import WorkflowCatalog, WorkflowInfo
    from flux.domain.schedule import interval
    from flux.models import ExecutionContextModel, ScheduleModel
    from flux.schedule_manager import create_schedule_manager
    from flux.server import Server

    WorkflowCatalog.create().save(
        [
            WorkflowInfo(
                id="",
                name="review-target",
                namespace="default",
                imports=[],
                source=b"",
                metadata={},
            ),
        ],
    )
    workflow = WorkflowCatalog.create().get("default", "review-target")
    manager = create_schedule_manager()
    schedule = manager.create_schedule(
        workflow_id=workflow.id,
        workflow_name="review-target",
        name="review-schedule",
        schedule=interval(minutes=1, overlap=overlap),
    )
    now = datetime.now(timezone.utc)
    with RepositoryFactory.create_repository().session() as session:
        session.get(ScheduleModel, schedule.id).next_run_at = now - timedelta(minutes=1)
        session.commit()
    schedule = manager.get_due_schedules(current_time=now)[0]
    server = Server("127.0.0.1", 0)
    scheduler = server._scheduler()
    with patch.object(
        scheduler if boundary == "session" else ScheduleModel,
        "_session_factory" if boundary == "session" else "mark_run",
        side_effect=RuntimeError("injected link outage"),
    ):
        with pytest.raises(RuntimeError, match="injected link outage"):
            await scheduler._trigger(schedule, now)
    assert not manager.has_active_execution(schedule.id)
    assert len(manager.get_due_schedules(current_time=now)) == 1
    await scheduler._trigger(schedule, now)
    await scheduler._trigger(schedule, now)
    with RepositoryFactory.create_repository().session() as session:
        rows = session.query(ExecutionContextModel).all()
        assert len(rows) == 1
        assert rows[0].schedule_id == schedule.id
