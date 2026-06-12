from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func

from flux.domain.events import ExecutionEventType
from flux.domain.schedule import Schedule
from flux.models import (
    ScheduleModel,
    RepositoryFactory,
    ExecutionContextModel,
    ExecutionEventModel,
)
from flux.errors import ExecutionError
from flux.utils import get_logger

logger = get_logger(__name__)


class ScheduleManagerError(ExecutionError):
    """Error raised by schedule manager operations"""

    def __init__(self, message: str, inner_exception: Exception | None = None):
        super().__init__(inner_exception, message)


class ScheduleManager(ABC):
    """Abstract base class for schedule management"""

    @abstractmethod
    def create_schedule(
        self,
        workflow_id: str,
        workflow_name: str,
        name: str,
        schedule: Schedule,
        description: str | None = None,
        input_data: Any = None,
        run_as_service_account: str | None = None,
        workflow_namespace: str = "default",
    ) -> ScheduleModel:
        """Create a new schedule"""
        pass

    @abstractmethod
    def get_schedule(self, schedule_id: str) -> ScheduleModel | None:
        """Get schedule by ID"""
        pass

    @abstractmethod
    def get_schedule_by_name(self, workflow_id: str, name: str) -> ScheduleModel | None:
        """Get schedule by workflow ID and name"""
        pass

    @abstractmethod
    def list_schedules(
        self,
        workflow_id: str | None = None,
        active_only: bool = True,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ScheduleModel]:
        """List schedules, optionally filtered by workflow with pagination"""
        pass

    @abstractmethod
    def update_schedule(
        self,
        schedule_id: str,
        schedule: Schedule | None = None,
        description: str | None = None,
        input_data: Any = None,
        run_as_service_account: str | None = None,
    ) -> ScheduleModel:
        """Update an existing schedule"""
        pass

    @abstractmethod
    def pause_schedule(self, schedule_id: str) -> ScheduleModel:
        """Pause a schedule"""
        pass

    @abstractmethod
    def resume_schedule(self, schedule_id: str) -> ScheduleModel:
        """Resume a paused schedule"""
        pass

    @abstractmethod
    def delete_schedule(self, schedule_id: str) -> bool:
        """Delete a schedule"""
        pass

    @abstractmethod
    def get_due_schedules(
        self,
        current_time: datetime | None = None,
        limit: int | None = None,
    ) -> list[ScheduleModel]:
        """Get schedules that are due to run"""
        pass

    @abstractmethod
    def record_run(self, schedule_id: str, run_time: datetime) -> None:
        """Persist a successful trigger: advance next_run_at and run stats."""
        pass

    @abstractmethod
    def record_failure(self, schedule_id: str) -> None:
        """Persist a failed trigger: increment the failure count."""
        pass

    @abstractmethod
    def get_schedule_history(
        self,
        schedule_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Any], int]:
        """Get execution history for a schedule"""
        pass


class DatabaseScheduleManager(ScheduleManager):
    """Database-backed schedule manager implementation"""

    def __init__(self):
        self._repository = RepositoryFactory.create_repository()

    def create_schedule(
        self,
        workflow_id: str,
        workflow_name: str,
        name: str,
        schedule: Schedule,
        description: str | None = None,
        input_data: Any = None,
        run_as_service_account: str | None = None,
        workflow_namespace: str = "default",
    ) -> ScheduleModel:
        """Create a new schedule"""
        try:
            with self._repository.session() as session:
                # Check if schedule with same name already exists for this workflow
                existing = (
                    session.query(ScheduleModel)
                    .filter(
                        ScheduleModel.workflow_id == workflow_id,
                        ScheduleModel.name == name,
                    )
                    .first()
                )

                if existing:
                    raise ScheduleManagerError(
                        f"Schedule '{name}' already exists for workflow '{workflow_name}'",
                    )

                schedule_model = ScheduleModel(
                    workflow_id=workflow_id,
                    workflow_namespace=workflow_namespace,
                    workflow_name=workflow_name,
                    name=name,
                    schedule=schedule,
                    description=description,
                    input_data=input_data,
                    run_as_service_account=run_as_service_account,
                )

                session.add(schedule_model)
                session.commit()
                session.refresh(schedule_model)
                return schedule_model

        except Exception as e:
            raise ScheduleManagerError(f"Failed to create schedule: {str(e)}", e)

    def get_schedule(self, schedule_id: str) -> ScheduleModel | None:
        """Get schedule by ID"""
        try:
            with self._repository.session() as session:
                return session.query(ScheduleModel).filter(ScheduleModel.id == schedule_id).first()
        except Exception as e:
            raise ScheduleManagerError(f"Failed to get schedule: {str(e)}", e)

    def get_schedule_by_name(self, workflow_id: str, name: str) -> ScheduleModel | None:
        """Get schedule by workflow ID and name"""
        try:
            with self._repository.session() as session:
                return (
                    session.query(ScheduleModel)
                    .filter(
                        ScheduleModel.workflow_id == workflow_id,
                        ScheduleModel.name == name,
                    )
                    .first()
                )
        except Exception as e:
            raise ScheduleManagerError(f"Failed to get schedule by name: {str(e)}", e)

    def list_schedules(
        self,
        workflow_id: str | None = None,
        active_only: bool = True,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ScheduleModel]:
        """List schedules, optionally filtered by workflow with pagination"""
        try:
            with self._repository.session() as session:
                query = session.query(ScheduleModel)

                if workflow_id:
                    query = query.filter(ScheduleModel.workflow_id == workflow_id)

                if active_only:
                    from flux.domain.schedule import ScheduleStatus

                    query = query.filter(ScheduleModel.status == ScheduleStatus.ACTIVE)

                # Apply ordering
                query = query.order_by(ScheduleModel.created_at.desc())

                # Apply pagination
                if offset:
                    query = query.offset(offset)
                if limit:
                    query = query.limit(limit)

                return query.all()

        except Exception as e:
            raise ScheduleManagerError(f"Failed to list schedules: {str(e)}", e)

    def update_schedule(
        self,
        schedule_id: str,
        schedule: Schedule | None = None,
        description: str | None = None,
        input_data: Any = None,
        run_as_service_account: str | None = None,
    ) -> ScheduleModel:
        """Update an existing schedule"""
        try:
            with self._repository.session() as session:
                schedule_model = (
                    session.query(ScheduleModel).filter(ScheduleModel.id == schedule_id).first()
                )

                if not schedule_model:
                    raise ScheduleManagerError(f"Schedule with ID '{schedule_id}' not found")

                if schedule is not None:
                    schedule_model.schedule_config = schedule
                    schedule_model.update_next_run()

                if description is not None:
                    schedule_model.description = description

                if input_data is not None:
                    schedule_model.input_data = input_data

                if run_as_service_account is not None:
                    schedule_model.run_as_service_account = run_as_service_account

                schedule_model.updated_at = datetime.now(timezone.utc)
                session.commit()
                session.refresh(schedule_model)
                return schedule_model

        except Exception as e:
            raise ScheduleManagerError(f"Failed to update schedule: {str(e)}", e)

    def pause_schedule(self, schedule_id: str) -> ScheduleModel:
        """Pause a schedule"""
        try:
            with self._repository.session() as session:
                schedule_model = (
                    session.query(ScheduleModel).filter(ScheduleModel.id == schedule_id).first()
                )

                if not schedule_model:
                    raise ScheduleManagerError(f"Schedule with ID '{schedule_id}' not found")

                from flux.domain.schedule import ScheduleStatus

                schedule_model.status = ScheduleStatus.PAUSED
                schedule_model.updated_at = datetime.now(timezone.utc)
                session.commit()
                session.refresh(schedule_model)
                return schedule_model

        except Exception as e:
            raise ScheduleManagerError(f"Failed to pause schedule: {str(e)}", e)

    def resume_schedule(self, schedule_id: str) -> ScheduleModel:
        """Resume a paused schedule"""
        try:
            with self._repository.session() as session:
                schedule_model = (
                    session.query(ScheduleModel).filter(ScheduleModel.id == schedule_id).first()
                )

                if not schedule_model:
                    raise ScheduleManagerError(f"Schedule with ID '{schedule_id}' not found")

                from flux.domain.schedule import ScheduleStatus

                schedule_model.status = ScheduleStatus.ACTIVE
                schedule_model.updated_at = datetime.now(timezone.utc)
                # Update next run time when resuming
                schedule_model.update_next_run()
                session.commit()
                session.refresh(schedule_model)
                return schedule_model

        except Exception as e:
            raise ScheduleManagerError(f"Failed to resume schedule: {str(e)}", e)

    def delete_schedule(self, schedule_id: str) -> bool:
        """Delete a schedule"""
        try:
            with self._repository.session() as session:
                schedule_model = (
                    session.query(ScheduleModel).filter(ScheduleModel.id == schedule_id).first()
                )

                if not schedule_model:
                    return False

                session.delete(schedule_model)
                session.commit()
                return True

        except Exception as e:
            raise ScheduleManagerError(f"Failed to delete schedule: {str(e)}", e)

    def get_due_schedules(
        self,
        current_time: datetime | None = None,
        limit: int | None = None,
    ) -> list[ScheduleModel]:
        """Get schedules that are due to run"""
        if current_time is None:
            current_time = datetime.now(timezone.utc)

        try:
            with self._repository.session() as session:
                from flux.domain.schedule import ScheduleStatus

                query = (
                    session.query(ScheduleModel)
                    .filter(
                        ScheduleModel.status == ScheduleStatus.ACTIVE,
                        ScheduleModel.next_run_at <= current_time,
                    )
                    .order_by(ScheduleModel.next_run_at)
                )

                if limit:
                    query = query.limit(limit)

                return query.all()

        except Exception as e:
            raise ScheduleManagerError(f"Failed to get due schedules: {str(e)}", e)

    def record_run(self, schedule_id: str, run_time: datetime) -> None:
        """Persist a successful trigger against the stored schedule row.

        ``get_due_schedules`` returns detached objects, so mutating those never
        reaches the database; this loads the row in a fresh session and applies
        ``mark_run`` (last_run_at, run_count, next_run_at) there. Without the
        persisted ``next_run_at`` advance, a due schedule would re-fire on every
        scheduler poll.

        Never raises: stats/next-run recording must not break the dispatch loop;
        failures are logged instead.
        """
        try:
            with self._repository.session() as session:
                model = session.query(ScheduleModel).filter(ScheduleModel.id == schedule_id).first()
                if model is None:
                    logger.warning(
                        f"Schedule '{schedule_id}' disappeared before its run could be recorded",
                    )
                    return
                model.mark_run(run_time)
                session.commit()
        except Exception:
            logger.error(
                f"Failed to record run for schedule '{schedule_id}'",
                exc_info=True,
            )

    def record_failure(self, schedule_id: str) -> None:
        """Persist a failed trigger (failure_count) for the stored schedule row.

        Never raises — see ``record_run``.
        """
        try:
            with self._repository.session() as session:
                model = session.query(ScheduleModel).filter(ScheduleModel.id == schedule_id).first()
                if model is None:
                    logger.warning(
                        f"Schedule '{schedule_id}' disappeared before its failure could be recorded",
                    )
                    return
                model.mark_failure()
                session.commit()
        except Exception:
            logger.error(
                f"Failed to record failure for schedule '{schedule_id}'",
                exc_info=True,
            )

    def health_check(self) -> bool:
        """Check database connectivity"""
        return self._repository.health_check()

    def get_schedule_history(
        self,
        schedule_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Any], int]:
        """
        Get execution history for a schedule.

        Executions are linked to their originating schedule at dispatch time
        (``executions.schedule_id``), so history is scoped to this schedule
        rather than every execution of the underlying workflow.

        Args:
            schedule_id: The schedule ID
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            Tuple of (list of execution summaries, total count)
        """
        try:
            with self._repository.session() as session:
                # Get the schedule first
                schedule = (
                    session.query(ScheduleModel).filter(ScheduleModel.id == schedule_id).first()
                )

                if not schedule:
                    raise ScheduleManagerError(f"Schedule with ID '{schedule_id}' not found")

                # Only executions dispatched by this schedule. The executions
                # table has no timestamp column, so order by each execution's
                # earliest event id (a monotonic autoincrement) DESC — newest
                # runs first — instead of by the arbitrary execution_id string.
                first_event = (
                    session.query(
                        ExecutionEventModel.execution_id.label("execution_id"),
                        func.min(ExecutionEventModel.id).label("first_id"),
                    )
                    .group_by(ExecutionEventModel.execution_id)
                    .subquery()
                )

                query = (
                    session.query(ExecutionContextModel)
                    .outerjoin(
                        first_event,
                        first_event.c.execution_id == ExecutionContextModel.execution_id,
                    )
                    .filter(ExecutionContextModel.schedule_id == schedule_id)
                )

                # Get total count
                total = query.count()

                # Apply pagination
                executions = (
                    query.order_by(first_event.c.first_id.desc()).offset(offset).limit(limit).all()
                )

                # Derive started/completed timestamps (and the failure reason)
                # from the event log. Only the few event types needed are
                # fetched, as plain columns rather than full ORM rows, so a
                # page never loads a workflow's entire event history.
                exec_ids = [ex.execution_id for ex in executions]
                started_at: dict[str, str] = {}
                completed_at: dict[str, str] = {}
                error: dict[str, str] = {}
                if exec_ids:
                    terminal_types = {
                        ExecutionEventType.WORKFLOW_COMPLETED,
                        ExecutionEventType.WORKFLOW_FAILED,
                        ExecutionEventType.WORKFLOW_CANCELLED,
                    }
                    relevant_types = {ExecutionEventType.WORKFLOW_STARTED, *terminal_types}
                    rows = (
                        session.query(
                            ExecutionEventModel.execution_id,
                            ExecutionEventModel.type,
                            ExecutionEventModel.time,
                            ExecutionEventModel.value,
                        )
                        .filter(
                            ExecutionEventModel.execution_id.in_(exec_ids),
                            ExecutionEventModel.type.in_(relevant_types),
                        )
                        .order_by(ExecutionEventModel.id)
                        .all()
                    )
                    for ev_exec_id, ev_type, ev_time, ev_value in rows:
                        if ev_type == ExecutionEventType.WORKFLOW_STARTED:
                            started_at.setdefault(ev_exec_id, ev_time.isoformat())
                        elif ev_type in terminal_types:
                            completed_at[ev_exec_id] = ev_time.isoformat()
                            if ev_type == ExecutionEventType.WORKFLOW_FAILED and ev_value:
                                error[ev_exec_id] = str(ev_value)

                # Return execution summaries
                results = []
                for ex in executions:
                    results.append(
                        {
                            "execution_id": ex.execution_id,
                            "workflow_name": ex.workflow_name,
                            "state": ex.state.value
                            if hasattr(ex.state, "value")
                            else str(ex.state),
                            "worker_name": ex.worker_name,
                            "started_at": started_at.get(ex.execution_id),
                            "completed_at": completed_at.get(ex.execution_id),
                            "error": error.get(ex.execution_id),
                        },
                    )

                return results, total

        except ScheduleManagerError:
            raise
        except Exception as e:
            raise ScheduleManagerError(f"Failed to get schedule history: {str(e)}", e)


def create_schedule_manager() -> ScheduleManager:
    """Factory function to create a schedule manager instance"""
    return DatabaseScheduleManager()
