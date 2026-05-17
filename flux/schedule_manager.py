from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any


from flux.domain.events import ExecutionEventType
from flux.domain.schedule import Schedule
from flux.models import (
    ScheduleModel,
    RepositoryFactory,
    ExecutionContextModel,
    ExecutionEventModel,
)
from flux.errors import ExecutionError


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

                # Only executions dispatched by this schedule
                query = session.query(ExecutionContextModel).filter(
                    ExecutionContextModel.schedule_id == schedule_id,
                )

                # Get total count
                total = query.count()

                # Apply pagination
                executions = (
                    query.order_by(ExecutionContextModel.execution_id)
                    .offset(offset)
                    .limit(limit)
                    .all()
                )

                # Derive started/completed timestamps from the event log.
                exec_ids = [ex.execution_id for ex in executions]
                started_at: dict[str, str] = {}
                completed_at: dict[str, str] = {}
                if exec_ids:
                    terminal_types = {
                        ExecutionEventType.WORKFLOW_COMPLETED,
                        ExecutionEventType.WORKFLOW_FAILED,
                        ExecutionEventType.WORKFLOW_CANCELLED,
                    }
                    events = (
                        session.query(ExecutionEventModel)
                        .filter(ExecutionEventModel.execution_id.in_(exec_ids))
                        .order_by(ExecutionEventModel.id)
                        .all()
                    )
                    for ev in events:
                        if (
                            ev.type == ExecutionEventType.WORKFLOW_STARTED
                            and ev.execution_id not in started_at
                        ):
                            started_at[ev.execution_id] = ev.time.isoformat()
                        elif ev.type in terminal_types:
                            completed_at[ev.execution_id] = ev.time.isoformat()

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
