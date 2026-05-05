"""Regression test for schedule manager cross-namespace isolation."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_config_singleton():
    """Reset the Configuration singleton before and after the test so leaks
    don't affect subsequent tests (e.g. test_postgresql_config::test_configuration_reload).
    """
    from flux.config import Configuration
    from flux.models import DatabaseRepository

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]
    DatabaseRepository._engines.clear()

    yield

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]
    DatabaseRepository._engines.clear()


def test_schedule_history_scoped_by_namespace(tmp_path, monkeypatch):
    """Two workflows with the same short name in different namespaces must
    not share execution history via the schedule manager's query path.
    """
    monkeypatch.setenv("FLUX_DATABASE_URL", f"sqlite:///{tmp_path}/sched.db")
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()

    from flux.catalogs import DatabaseWorkflowCatalog, WorkflowInfo
    from flux.domain.events import ExecutionState
    from flux.domain.schedule import CronSchedule
    from flux.models import (
        Base,
        ExecutionContextModel,
        ScheduleModel,
        RepositoryFactory,
    )
    from flux.schedule_manager import DatabaseScheduleManager

    # Bootstrap schema
    repo = RepositoryFactory.create_repository()
    Base.metadata.create_all(repo._engine)

    # Save two workflows with the same short name in different namespaces
    catalog = DatabaseWorkflowCatalog()
    billing_wf = catalog.save(
        [WorkflowInfo(id="", name="process", namespace="billing", imports=[], source=b"a")],
    )[0]
    analytics_wf = catalog.save(
        [WorkflowInfo(id="", name="process", namespace="analytics", imports=[], source=b"b")],
    )[0]

    # Create a schedule for billing/process
    schedule_obj = CronSchedule("0 0 * * *")
    with repo.session() as s:
        sm = ScheduleModel(
            workflow_id=billing_wf.id,
            workflow_namespace="billing",
            workflow_name="process",
            name="daily",
            schedule=schedule_obj,
            description=None,
        )
        s.add(sm)
        s.flush()
        sched_id = sm.id

        # Insert one execution for billing/process
        s.add(
            ExecutionContextModel(
                execution_id="e_billing",
                workflow_id=billing_wf.id,
                workflow_namespace="billing",
                workflow_name="process",
                input=None,
                state=ExecutionState.COMPLETED,
            ),
        )
        # Insert one execution for analytics/process — this MUST NOT leak into the billing query
        s.add(
            ExecutionContextModel(
                execution_id="e_analytics",
                workflow_id=analytics_wf.id,
                workflow_namespace="analytics",
                workflow_name="process",
                input=None,
                state=ExecutionState.COMPLETED,
            ),
        )
        s.commit()

    mgr = DatabaseScheduleManager()
    results, total = mgr.get_schedule_history(sched_id, limit=10, offset=0)
    assert total == 1, f"Expected 1 execution for billing schedule, got {total}"
    assert results[0]["execution_id"] == "e_billing"
