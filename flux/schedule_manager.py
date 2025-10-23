from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any


from flux.domain.schedule import Schedule
from flux.models import ScheduleModel, RepositoryFactory
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
                    workflow_name=workflow_name,
                    name=name,
                    schedule=schedule,
                    description=description,
                    input_data=input_data,
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


def create_schedule_manager() -> ScheduleManager:
    """Factory function to create a schedule manager instance"""
    return DatabaseScheduleManager()
