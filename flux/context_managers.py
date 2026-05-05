from __future__ import annotations

from abc import ABC
from abc import abstractmethod

from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from flux import ExecutionContext
from flux.domain import ExecutionState
from flux.domain import ResourceRequest
from flux.errors import ExecutionContextNotFoundError, ExecutionError
from flux.models import ExecutionEventModel
from flux.models import ExecutionContextModel
from flux.models import RepositoryFactory
from flux.models import WorkflowModel
from flux.worker_registry import WorkerInfo


class ContextManager(ABC):
    @abstractmethod
    def save(self, ctx: ExecutionContext) -> ExecutionContext:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def get(self, execution_id: str | None) -> ExecutionContext:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def exists(self, execution_id: str) -> bool:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def update(self, ctx: ExecutionContext) -> ExecutionContext:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def next_execution(
        self,
        worker: WorkerInfo,
    ) -> ExecutionContext | None:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def next_cancellation(
        self,
        worker: WorkerInfo,
    ) -> ExecutionContext | None:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def next_resume(
        self,
        worker: WorkerInfo,
    ) -> ExecutionContext | None:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def claim(self, execution_id: str, worker: WorkerInfo) -> ExecutionContext:
        raise NotImplementedError()

    @abstractmethod
    def claim_resume(
        self,
        execution_id: str,
        worker: WorkerInfo,
    ) -> ExecutionContext:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def unclaim(self, execution_id: str) -> ExecutionContext:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def release_worker(self, execution_id: str) -> ExecutionContext:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def find_by_worker(self, worker_name: str) -> list[ExecutionContext]:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def list(
        self,
        workflow_name: str | None = None,
        workflow_namespace: str | None = None,
        state: ExecutionState | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ExecutionContext], int]:  # pragma: no cover
        """List executions with optional filtering and pagination."""
        raise NotImplementedError()

    @staticmethod
    def create() -> ContextManager:
        return DatabaseContextManager()


class DatabaseContextManager(ContextManager):
    """Dialect-agnostic context manager.

    Delegates engine creation to ``RepositoryFactory`` so the same query
    implementation works against SQLite and PostgreSQL. All query methods
    use SQLAlchemy ORM constructs that are portable across both backends.
    """

    def __init__(self):
        self._repository = RepositoryFactory.create_repository()

    def session(self) -> Session:
        return self._repository.session()

    def get(self, execution_id: str | None) -> ExecutionContext:
        with self.session() as session:
            model = session.get(ExecutionContextModel, execution_id)
            if model:
                return model.to_plain()
            raise ExecutionContextNotFoundError(execution_id)

    def exists(self, execution_id: str) -> bool:
        with self.session() as session:
            result = (
                session.query(ExecutionContextModel.execution_id)
                .filter(ExecutionContextModel.execution_id == execution_id)
                .first()
            )
            return result is not None

    def save(self, ctx: ExecutionContext) -> ExecutionContext:
        with self.session() as session:
            try:
                model = session.get(
                    ExecutionContextModel,
                    ctx.execution_id,
                )
                if model:
                    model.output = ctx.output
                    model.state = ctx.state
                    model.events.extend(self._get_additional_events(ctx, model))
                else:
                    session.add(ExecutionContextModel.from_plain(ctx))
                session.commit()
                return ctx
            except IntegrityError:  # pragma: no cover
                session.rollback()
                raise

    def update(self, ctx: ExecutionContext) -> ExecutionContext:
        with self.session() as session:
            model = session.get(
                ExecutionContextModel,
                ctx.execution_id,
            )
            if not model:
                raise ExecutionContextNotFoundError(ctx.execution_id)
            model.output = ctx.output
            model.state = ctx.state
            model.events.extend(self._get_additional_events(ctx, model))
            session.commit()
            return ctx

    def _worker_matches_workflow(self, worker: WorkerInfo, workflow: WorkflowModel) -> bool:
        if workflow.affinity is not None:
            if not ResourceRequest.matches_labels(worker.labels, workflow.affinity):
                return False
        if workflow.requests is not None:
            requests = ResourceRequest(**(workflow.requests or {}))
            if worker.resources is None:
                return False
            if not requests.matches_worker(worker.resources, worker.packages):
                return False
        return True

    def _next_matching_execution(
        self,
        worker: WorkerInfo,
        session: Session,
        state: ExecutionState = ExecutionState.CREATED,
        constrained_only: bool = False,
    ):
        query = (
            session.query(ExecutionContextModel, WorkflowModel)
            .join(WorkflowModel)
            .filter(ExecutionContextModel.state == state)
            .with_for_update(skip_locked=True)
        )

        if constrained_only:
            query = query.filter(
                or_(WorkflowModel.requests.is_not(None), WorkflowModel.affinity.is_not(None)),
            )
        else:
            query = query.filter(
                WorkflowModel.requests.is_(None),
                WorkflowModel.affinity.is_(None),
            )

        if not constrained_only:
            result = query.limit(1).first()
            return result if result else (None, None)

        for model, workflow in query:
            if not self._worker_matches_workflow(worker, workflow):
                continue
            return model, workflow
        return None, None

    def next_execution(self, worker: WorkerInfo) -> ExecutionContext | None:
        with self.session() as session:
            if not self._is_least_loaded_worker(worker, session):
                return None

            model, workflow = self._next_matching_execution(
                worker,
                session,
                constrained_only=True,
            )

            if not model or not workflow:
                model, workflow = self._next_matching_execution(
                    worker,
                    session,
                    constrained_only=False,
                )

            if model and workflow:
                ctx = model.to_plain()
                ctx.schedule(worker)
                model.state = ctx.state
                model.events.extend(self._get_additional_events(ctx, model))
                session.commit()
                return ctx

            return None

    def _is_least_loaded_worker(self, worker: WorkerInfo, session: Session) -> bool:
        active_states = [
            ExecutionState.RUNNING,
            ExecutionState.CLAIMED,
            ExecutionState.SCHEDULED,
        ]

        worker_loads = (
            session.query(
                ExecutionContextModel.worker_name,
                func.count(ExecutionContextModel.execution_id).label("count"),
            )
            .filter(ExecutionContextModel.state.in_(active_states))
            .group_by(ExecutionContextModel.worker_name)
            .all()
        )

        # Only consider workers with active executions plus the current worker.
        # This prevents disconnected workers (with 0 active load) from blocking
        # assignment to connected workers.
        load_map = {name: count for name, count in worker_loads}

        if worker.name not in load_map:
            load_map[worker.name] = 0

        if len(load_map) <= 1:
            return True

        worker_count = load_map[worker.name]
        min_load = min(load_map.values())

        return worker_count <= min_load

    def next_cancellation(self, worker: WorkerInfo) -> ExecutionContext | None:
        with self.session() as session:
            query = (
                session.query(ExecutionContextModel)
                .filter(
                    ExecutionContextModel.state == ExecutionState.CANCELLING,
                    ExecutionContextModel.worker_name == worker.name,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            model = query.first()
            if model:
                return model.to_plain()
            return None

    def next_resume(self, worker: WorkerInfo) -> ExecutionContext | None:
        with self.session() as session:
            sticky_query = (
                session.query(ExecutionContextModel, WorkflowModel)
                .join(WorkflowModel)
                .filter(
                    ExecutionContextModel.state == ExecutionState.RESUMING,
                    ExecutionContextModel.worker_name == worker.name,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            result = sticky_query.first()

            if result is None:
                fallback_query = (
                    session.query(ExecutionContextModel, WorkflowModel)
                    .join(WorkflowModel)
                    .filter(
                        ExecutionContextModel.state == ExecutionState.RESUMING,
                        ExecutionContextModel.worker_name.is_(None),
                    )
                    .with_for_update(skip_locked=True)
                )
                for model, workflow in fallback_query:
                    if self._worker_matches_workflow(worker, workflow):
                        result = (model, workflow)
                        break

            if result is None:
                return None

            model, workflow = result
            ctx = model.to_plain()
            ctx.resume_schedule(worker)
            model.state = ctx.state
            model.worker_name = ctx.current_worker
            model.events.extend(self._get_additional_events(ctx, model))
            session.commit()

            from flux.domain.events import ExecutionEventType
            from flux.observability import get_metrics

            m = get_metrics()
            if m:
                resuming_events = [
                    e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_RESUMING
                ]
                scheduled_events = [
                    e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_RESUME_SCHEDULED
                ]
                if resuming_events and scheduled_events:
                    duration = (
                        scheduled_events[-1].time - resuming_events[-1].time
                    ).total_seconds()
                    m.record_resume_scheduled(
                        ctx.workflow_namespace,
                        ctx.workflow_name,
                        max(duration, 0.0),
                    )

            return ctx

    def claim(self, execution_id: str, worker: WorkerInfo) -> ExecutionContext:
        with self.session() as session:
            # Race-safe path: the row was already SCHEDULED to this worker by
            # next_execution(). Lock it for update so a second worker can't
            # double-claim between the SELECT and the COMMIT.
            model = (
                session.query(ExecutionContextModel)
                .filter(
                    ExecutionContextModel.execution_id == execution_id,
                    ExecutionContextModel.state == ExecutionState.SCHEDULED,
                    ExecutionContextModel.worker_name == worker.name,
                )
                .with_for_update(skip_locked=True)
                .first()
            )
            # Fall back to the plain lookup so direct ctx.claim() callers
            # (tests, in-process flows that skip the dispatcher) still work.
            if model is None:
                model = session.get(ExecutionContextModel, execution_id)
            if model is None:
                raise ExecutionContextNotFoundError(execution_id)
            ctx = model.to_plain()
            ctx.claim(worker)
            model.state = ctx.state
            model.worker_name = ctx.current_worker
            model.events.extend(self._get_additional_events(ctx, model))
            session.commit()
            return ctx

    def claim_resume(self, execution_id: str, worker: WorkerInfo) -> ExecutionContext:
        with self.session() as session:
            model = (
                session.query(ExecutionContextModel)
                .filter(
                    ExecutionContextModel.execution_id == execution_id,
                    ExecutionContextModel.state == ExecutionState.RESUME_SCHEDULED,
                    ExecutionContextModel.worker_name == worker.name,
                )
                .with_for_update(skip_locked=True)
                .first()
            )
            if not model:
                # Either the execution doesn't exist, isn't RESUME_SCHEDULED,
                # or was scheduled for a different worker. resume_claim() will
                # produce the precise error after we re-fetch.
                fallback = session.get(ExecutionContextModel, execution_id)
                if not fallback:
                    raise ExecutionContextNotFoundError(execution_id)
                ctx = fallback.to_plain()
                ctx.resume_claim(worker)
                # Unreachable: resume_claim raises above. Kept for type safety.
                raise ExecutionError(message="claim_resume failed without a specific reason")
            ctx = model.to_plain()
            ctx.resume_claim(worker)
            model.state = ctx.state
            model.worker_name = ctx.current_worker
            model.events.extend(self._get_additional_events(ctx, model))
            session.commit()

            from flux.domain.events import ExecutionEventType
            from flux.observability import get_metrics

            m = get_metrics()
            if m:
                scheduled_events = [
                    e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_RESUME_SCHEDULED
                ]
                claimed_events = [
                    e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_RESUME_CLAIMED
                ]
                if scheduled_events and claimed_events:
                    duration = (claimed_events[-1].time - scheduled_events[-1].time).total_seconds()
                    m.record_resume_claimed(
                        ctx.workflow_namespace,
                        ctx.workflow_name,
                        max(duration, 0.0),
                    )

            return ctx

    def unclaim(self, execution_id: str) -> ExecutionContext:
        """Reset an active execution for rescheduling.

        Recovery rules:
        - RESUME_SCHEDULED or RESUME_CLAIMED → RESUMING (preserves resume input)
        - SCHEDULED, CLAIMED, or RUNNING → CREATED (existing behaviour)
        - Any other state → no-op (returns the current context)
        """
        resume_recovery = {
            ExecutionState.RESUME_SCHEDULED,
            ExecutionState.RESUME_CLAIMED,
        }
        initial_recovery = {
            ExecutionState.SCHEDULED,
            ExecutionState.CLAIMED,
            ExecutionState.RUNNING,
        }
        with self.session() as session:
            model = session.get(ExecutionContextModel, execution_id)
            if not model:
                raise ExecutionContextNotFoundError(execution_id)
            if model.state in resume_recovery:
                model.state = ExecutionState.RESUMING
                model.worker_name = None
                session.commit()
                return model.to_plain()
            if model.state in initial_recovery:
                model.state = ExecutionState.CREATED
                model.worker_name = None
                session.commit()
                return model.to_plain()
            return model.to_plain()

    def release_worker(self, execution_id: str) -> ExecutionContext:
        """Clear worker assignment on a suspended execution.

        For PAUSED or RESUMING executions, clears worker_name without
        changing state.  Called during worker eviction so that another
        worker can pick up the execution via affinity matching.
        """
        releasable = {
            ExecutionState.PAUSED,
            ExecutionState.RESUMING,
        }
        with self.session() as session:
            model = session.get(ExecutionContextModel, execution_id)
            if not model:
                raise ExecutionContextNotFoundError(execution_id)
            if model.state not in releasable:
                return model.to_plain()
            model.worker_name = None
            session.commit()
            return model.to_plain()

    def find_by_worker(self, worker_name: str) -> list[ExecutionContext]:
        active_states = [
            ExecutionState.SCHEDULED,
            ExecutionState.CLAIMED,
            ExecutionState.RUNNING,
            ExecutionState.PAUSED,
            ExecutionState.RESUMING,
            ExecutionState.RESUME_SCHEDULED,
            ExecutionState.RESUME_CLAIMED,
        ]
        with self.session() as session:
            models = (
                session.query(ExecutionContextModel)
                .filter(
                    ExecutionContextModel.worker_name == worker_name,
                    ExecutionContextModel.state.in_(active_states),
                )
                .all()
            )
            return [m.to_plain() for m in models]

    def _get_additional_events(
        self,
        ctx: ExecutionContext,
        model: ExecutionContextModel,
    ):
        existing_events = [(e.event_id, e.type) for e in model.events]
        return [
            ExecutionEventModel.from_plain(ctx.execution_id, e)
            for e in ctx.events
            if (e.id, e.type) not in existing_events
        ]

    def list(
        self,
        workflow_name: str | None = None,
        workflow_namespace: str | None = None,
        state: ExecutionState | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ExecutionContext], int]:
        """
        List executions with optional filtering and pagination.

        Args:
            workflow_name: Optional workflow name to filter by
            workflow_namespace: Optional workflow namespace to filter by
            state: Optional execution state to filter by
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            Tuple of (list of ExecutionContext, total count)
        """

        with self.session() as session:
            query = session.query(ExecutionContextModel)

            if workflow_name:
                query = query.filter(ExecutionContextModel.workflow_name == workflow_name)

            if workflow_namespace:
                query = query.filter(ExecutionContextModel.workflow_namespace == workflow_namespace)

            if state:
                query = query.filter(ExecutionContextModel.state == state)

            # Get total count before pagination
            total = query.count()

            # Apply ordering and pagination
            models = (
                query.order_by(ExecutionContextModel.execution_id).offset(offset).limit(limit).all()
            )

            return [model.to_plain() for model in models], total
