from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from flux import ExecutionContext
from flux.domain import ExecutionState
from flux.errors import ExecutionContextNotFoundError, ExecutionError, StaleClaimError
from flux.models import ExecutionEventModel
from flux.models import ExecutionContextModel
from flux.models import RepositoryFactory
from flux.models import WorkflowModel
from flux.worker_registry import WorkerInfo

if TYPE_CHECKING:
    from flux.unit_of_work import UnitOfWork


_NO_DEMOTE_TO_PAUSED_FROM = frozenset(
    {
        ExecutionState.RESUMING,
        ExecutionState.CANCELLING,
    },
)
_TERMINAL_STATES = frozenset(
    {
        ExecutionState.COMPLETED,
        ExecutionState.FAILED,
        ExecutionState.CANCELLED,
    },
)


def _accept_state_write(new: ExecutionState, db: ExecutionState) -> bool:
    """Decide whether an incoming save/update may overwrite the persisted state.

    A persisted terminal state is final, and a persisted ``CANCELLING`` may
    only advance to a terminal state. This prevents a stale checkpoint — for
    example a worker still reporting ``RUNNING`` — from resurrecting a
    finished workflow or silently losing an in-flight cancellation. When this
    returns ``False`` the caller also holds back the row's output and event
    writes, so a stale context cannot corrupt a terminal execution's output
    or append misleading events.
    """
    if db in _TERMINAL_STATES:
        return new == db
    if db == ExecutionState.CANCELLING and new not in _TERMINAL_STATES:
        return False
    if new == ExecutionState.PAUSED and db in _NO_DEMOTE_TO_PAUSED_FROM:
        return False
    return True


class ContextManager(ABC):
    @abstractmethod
    def save(
        self,
        ctx: ExecutionContext,
        *,
        uow: UnitOfWork | None = None,
        preferred_worker: str | None = None,
    ) -> ExecutionContext:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def save_checked(
        self,
        ctx: ExecutionContext,
        *,
        uow: UnitOfWork | None = None,
        preferred_worker: str | None = None,
    ) -> bool:  # pragma: no cover
        """Like ``save`` but report whether the state write was applied.

        Returns ``False`` when ``_accept_state_write`` rejected the write
        because a concurrent terminal/cancelling transition already won the
        row. Callers chaining dependent writes (e.g. the approval gate
        creating a row) use this to abort instead of stranding orphans.
        """
        raise NotImplementedError()

    @abstractmethod
    def get(self, execution_id: str | None) -> ExecutionContext:  # pragma: no cover
        raise NotImplementedError()

    def get_summary(self, execution_id: str) -> dict:  # pragma: no cover
        """Summary fields for an execution WITHOUT hydrating its event log.

        Returns the same shape as ``ExecutionContextDTO.summary()`` — status
        polls and sync-wait loops call this instead of ``get()`` so a long
        execution's own status checks don't load and unpickle its entire
        history (D5).
        """
        raise NotImplementedError()

    def last_event_ordinal(self, execution_id: str) -> int | None:  # pragma: no cover
        """Highest event row id persisted for the execution (None when no
        events). A cheap change signal: callers re-hydrate the full context
        only when this advances."""
        raise NotImplementedError()

    @abstractmethod
    def exists(self, execution_id: str) -> bool:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def update(
        self,
        ctx: ExecutionContext,
        expected_claim_generation: int | None = None,
    ) -> ExecutionContext:  # pragma: no cover
        """Persist a checkpoint. When ``expected_claim_generation`` is given,
        reject it with ``StaleClaimError`` if the row has since been reassigned
        (fencing against partitioned-but-alive workers)."""
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
    def next_executions_batch(
        self,
        workers: list[WorkerInfo],
        limit: int,
    ) -> list[tuple[ExecutionContext, str]]:  # pragma: no cover
        """Claim up to ``limit`` pending executions and assign them across workers.

        Event-dispatch counterpart of ``next_execution``: one transaction claims
        a batch and spreads it over the eligible least-loaded workers. Returns
        ``(context, worker_name)`` pairs.
        """
        raise NotImplementedError()

    @abstractmethod
    def next_cancellations_batch(
        self,
        worker_names: list[str],
        limit: int,
    ) -> list[ExecutionContext]:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def next_resumes_batch(
        self,
        workers: list[WorkerInfo],
        limit: int,
    ) -> list[tuple[ExecutionContext, str]]:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def set_preferred_worker(
        self,
        execution_id: str,
        worker_name: str,
    ) -> None:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def get_claim_generation(self, execution_id: str) -> int:  # pragma: no cover
        """Current fencing generation for an execution (0 if never assigned)."""
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
        workflows: Sequence[tuple[str, str]] | None = None,
    ) -> tuple[list[ExecutionContext], int]:  # pragma: no cover
        """List executions with optional filtering and pagination.

        ``workflows`` restricts results to the given (namespace, name) pairs —
        the authorization filter for scoped readers. An empty list matches
        nothing (the caller may read no workflows).
        """
        raise NotImplementedError()

    def distinct_workflows(
        self,
        workflow_name: str | None = None,
        workflow_namespace: str | None = None,
        state: ExecutionState | None = None,
    ) -> Sequence[tuple[str, str]]:  # pragma: no cover
        """Distinct (namespace, name) pairs among matching executions.

        The scan runs over the executions table, but the result-set size is
        bounded by the number of distinct workflows ever executed (pairs may
        include workflows since deleted from the catalog — their permission
        checks simply fail for non-wildcard readers). Scoped readers use this
        to authorize per workflow before the paginated query."""
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

    def get_summary(self, execution_id: str) -> dict:
        from flux.domain.events import ExecutionEventType

        with self.session() as session:
            row = (
                session.query(
                    ExecutionContextModel.workflow_id,
                    ExecutionContextModel.workflow_namespace,
                    ExecutionContextModel.workflow_name,
                    ExecutionContextModel.execution_id,
                    ExecutionContextModel.input,
                    ExecutionContextModel.output,
                    ExecutionContextModel.state,
                    ExecutionContextModel.worker_name,
                )
                .filter(ExecutionContextModel.execution_id == execution_id)
                .first()
            )
            if row is None:
                raise ExecutionContextNotFoundError(execution_id)

            output = row.output
            if output is None and row.state == ExecutionState.PAUSED:
                # Mirror ExecutionContextDTO.summary(): a paused execution
                # surfaces the pause payload as its output. One targeted
                # event lookup instead of hydrating the whole log.
                paused = (
                    session.query(ExecutionEventModel.value)
                    .filter(
                        ExecutionEventModel.execution_id == execution_id,
                        ExecutionEventModel.type == ExecutionEventType.WORKFLOW_PAUSED,
                    )
                    .order_by(ExecutionEventModel.id.desc())
                    .first()
                )
                if paused is not None and paused.value is not None:
                    value = paused.value
                    output = value.get("output") if isinstance(value, dict) else value

            return {
                "workflow_id": row.workflow_id,
                "workflow_namespace": row.workflow_namespace,
                "workflow_name": row.workflow_name,
                "execution_id": row.execution_id,
                "input": row.input,
                "output": output,
                "state": row.state.value if row.state is not None else None,
                # DTO parity: the domain context coalesces a missing worker
                # to "" (see ExecutionContext.current_worker).
                "current_worker": row.worker_name or "",
            }

    def last_event_ordinal(self, execution_id: str) -> int | None:
        with self.session() as session:
            return (
                session.query(func.max(ExecutionEventModel.id))
                .filter(ExecutionEventModel.execution_id == execution_id)
                .scalar()
            )

    def exists(self, execution_id: str) -> bool:
        with self.session() as session:
            result = (
                session.query(ExecutionContextModel.execution_id)
                .filter(ExecutionContextModel.execution_id == execution_id)
                .first()
            )
            return result is not None

    def save(
        self,
        ctx: ExecutionContext,
        *,
        uow: UnitOfWork | None = None,
        preferred_worker: str | None = None,
    ) -> ExecutionContext:
        self.save_checked(ctx, uow=uow, preferred_worker=preferred_worker)
        return ctx

    def save_checked(
        self,
        ctx: ExecutionContext,
        *,
        uow: UnitOfWork | None = None,
        preferred_worker: str | None = None,
    ) -> bool:
        if uow is not None:
            return self._save_with_session(
                ctx,
                uow.session,
                manage_transaction=False,
                preferred_worker=preferred_worker,
            )
        with self.session() as session:
            return self._save_with_session(
                ctx,
                session,
                manage_transaction=True,
                preferred_worker=preferred_worker,
            )

    def _save_with_session(
        self,
        ctx: ExecutionContext,
        session: Session,
        *,
        manage_transaction: bool,
        preferred_worker: str | None = None,
    ) -> bool:
        try:
            model = self._lock_for_write(session, ctx.execution_id)
            if model:
                accepted = _accept_state_write(ctx.state, model.state)
                if accepted:
                    model.state = ctx.state
                    model.output = ctx.output
                    session.add_all(self._get_additional_events(ctx, session))
            else:
                accepted = True
                new_model = ExecutionContextModel.from_plain(ctx)
                if preferred_worker:
                    # Same transaction as the insert: event-mode dispatch can
                    # pick a fresh row up immediately, so a hint written in a
                    # follow-up UPDATE could be missed.
                    new_model.preferred_worker = preferred_worker
                session.add(new_model)
            if manage_transaction:
                session.commit()
            return accepted
        except IntegrityError:  # pragma: no cover
            if manage_transaction:
                session.rollback()
            raise

    def update(
        self,
        ctx: ExecutionContext,
        expected_claim_generation: int | None = None,
    ) -> ExecutionContext:
        with self.session() as session:
            model = self._lock_for_write(session, ctx.execution_id)
            if not model:
                raise ExecutionContextNotFoundError(ctx.execution_id)
            if (
                expected_claim_generation is not None
                and (model.claim_generation or 0) != expected_claim_generation
            ):
                raise StaleClaimError(
                    ctx.execution_id,
                    expected=expected_claim_generation,
                    actual=model.claim_generation or 0,
                )
            if _accept_state_write(ctx.state, model.state):
                model.state = ctx.state
                model.output = ctx.output
                session.add_all(self._get_additional_events(ctx, session))
            session.commit()
            return ctx

    @staticmethod
    def _lock_for_write(
        session: Session,
        execution_id: str,
    ) -> ExecutionContextModel | None:
        from sqlalchemy import select

        stmt = (
            select(ExecutionContextModel)
            .where(ExecutionContextModel.execution_id == execution_id)
            .with_for_update()
        )
        return session.execute(stmt).scalar_one_or_none()

    def _worker_matches_workflow(self, worker: WorkerInfo, workflow: WorkflowModel) -> bool:
        from flux.domain.resource_request import worker_matches

        return worker_matches(
            worker,
            workflow.requests,
            workflow.affinity,
            runner=(workflow.wf_metadata or {}).get("runner"),
        )

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

        # A workflow with metadata may carry a runner requirement (the column
        # is encoded, so it cannot be filtered in SQL) — treat it as
        # constrained so the per-row matcher runs.
        if constrained_only:
            query = query.filter(
                or_(
                    WorkflowModel.requests.is_not(None),
                    WorkflowModel.affinity.is_not(None),
                    WorkflowModel.wf_metadata.is_not(None),
                ),
            )
        else:
            query = query.filter(
                WorkflowModel.requests.is_(None),
                WorkflowModel.affinity.is_(None),
                WorkflowModel.wf_metadata.is_(None),
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
                model.worker_name = ctx.current_worker
                model.claim_generation = (model.claim_generation or 0) + 1
                session.add_all(self._get_additional_events(ctx, session))
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

        # A worker at its advertised capacity never takes new work, regardless
        # of how loaded the rest of the fleet is.
        if not self._has_free_slot(worker, load_map):
            return False

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
            model.claim_generation = (model.claim_generation or 0) + 1
            session.add_all(self._get_additional_events(ctx, session))
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

    @staticmethod
    def _has_free_slot(worker: WorkerInfo, loads: dict[str, int]) -> bool:
        """Whether the worker's advertised capacity admits one more execution.

        ``max_concurrent_executions`` of None or 0 means unlimited (legacy
        workers that predate the field).
        """
        cap = getattr(worker, "max_concurrent_executions", None)
        if not cap:
            return True
        return loads.get(worker.name, 0) < cap

    def get_claim_generation(self, execution_id: str) -> int:
        """Current fencing generation for an execution (0 if never assigned)."""
        with self.session() as session:
            row = (
                session.query(ExecutionContextModel.claim_generation)
                .filter(ExecutionContextModel.execution_id == execution_id)
                .scalar()
            )
            return row or 0

    def set_preferred_worker(self, execution_id: str, worker_name: str) -> None:
        """Record the sticky-routing hint for a relayed call() child."""
        with self.session() as session:
            session.query(ExecutionContextModel).filter(
                ExecutionContextModel.execution_id == execution_id,
            ).update(
                {ExecutionContextModel.preferred_worker: worker_name},
                synchronize_session=False,
            )
            session.commit()

    def _worker_load_map(self, session: Session, worker_names: list[str]) -> dict[str, int]:
        """Active-execution counts for the given workers, one aggregate query."""
        active_states = [
            ExecutionState.RUNNING,
            ExecutionState.CLAIMED,
            ExecutionState.SCHEDULED,
        ]
        rows = (
            session.query(
                ExecutionContextModel.worker_name,
                func.count(ExecutionContextModel.execution_id),
            )
            .filter(
                ExecutionContextModel.state.in_(active_states),
                ExecutionContextModel.worker_name.in_(worker_names),
            )
            .group_by(ExecutionContextModel.worker_name)
            .all()
        )
        loads = {name: 0 for name in worker_names}
        loads.update(dict(rows))
        return loads

    def next_executions_batch(
        self,
        workers: list[WorkerInfo],
        limit: int,
    ) -> list[tuple[ExecutionContext, str]]:
        """Claim up to ``limit`` pending executions and assign them across workers.

        One transaction per call: rows are locked with ``SKIP LOCKED`` (so
        concurrent dispatchers on other replicas pass over each other's
        claims), matched against each worker's labels/resources, and assigned
        to the least-loaded eligible worker. The load aggregate runs once per
        batch — not once per worker per poll tick as in ``next_execution``.
        Unmatched rows keep state CREATED and their locks release at commit.
        """
        if not workers or limit <= 0:
            return []
        assignments: list[tuple[ExecutionContext, str]] = []
        with self.session() as session:
            loads = self._worker_load_map(session, [w.name for w in workers])
            query = (
                session.query(ExecutionContextModel, WorkflowModel)
                .join(WorkflowModel)
                .filter(ExecutionContextModel.state == ExecutionState.CREATED)
                .with_for_update(skip_locked=True, of=ExecutionContextModel)
                .limit(limit)
            )
            for model, workflow in query:
                eligible = [
                    w
                    for w in workers
                    if self._has_free_slot(w, loads) and self._worker_matches_workflow(w, workflow)
                ]
                if not eligible:
                    continue
                preferred = getattr(model, "preferred_worker", None)
                worker = None
                policy = (workflow.wf_metadata or {}).get("routing")
                if policy is not None:
                    # Declared scoring policy owns the score stage — even a
                    # falsy/malformed one (hand-written metadata): the sticky
                    # hint participates only through an explicit sticky()
                    # term, and pick_worker returns None on a bad policy so
                    # it degrades to least-loaded, never re-enabling the
                    # hint and never stranding executions.
                    from flux.routing import pick_worker

                    worker = pick_worker(
                        eligible,
                        policy,
                        loads=loads,
                        input_value=model.input,
                        preferred=preferred,
                    )
                elif preferred:
                    # Sticky-routing hint (relayed call()): prefer the worker
                    # whose module cache is already warm, but only when it is
                    # eligible right now — otherwise fall back to least-loaded.
                    worker = next((w for w in eligible if w.name == preferred), None)
                if worker is None:
                    worker = min(eligible, key=lambda w: loads.get(w.name, 0))
                ctx = model.to_plain()
                ctx.schedule(worker)
                model.state = ctx.state
                model.worker_name = ctx.current_worker
                model.claim_generation = (model.claim_generation or 0) + 1
                session.add_all(self._get_additional_events(ctx, session))
                loads[worker.name] = loads.get(worker.name, 0) + 1
                assignments.append((ctx, worker.name))
            session.commit()
        return assignments

    def next_cancellations_batch(
        self,
        worker_names: list[str],
        limit: int,
    ) -> list[ExecutionContext]:
        """Pending cancellations for the given workers.

        Read-only like ``next_cancellation``; the state flips when the worker
        checkpoints the cancelled context, so re-delivery on later wakeups is
        possible and workers treat cancellation events idempotently.
        """
        if not worker_names or limit <= 0:
            return []
        with self.session() as session:
            models = (
                session.query(ExecutionContextModel)
                .filter(
                    ExecutionContextModel.state == ExecutionState.CANCELLING,
                    ExecutionContextModel.worker_name.in_(worker_names),
                )
                .with_for_update(skip_locked=True)
                .limit(limit)
                .all()
            )
            return [model.to_plain() for model in models]

    def next_resumes_batch(
        self,
        workers: list[WorkerInfo],
        limit: int,
    ) -> list[tuple[ExecutionContext, str]]:
        """Schedule pending resumes: sticky ones to their original worker,
        unassigned ones to the least-loaded matching worker."""
        if not workers or limit <= 0:
            return []
        by_name = {w.name: w for w in workers}
        assignments: list[tuple[ExecutionContext, str]] = []
        with self.session() as session:
            loads = self._worker_load_map(session, list(by_name))

            def _assign(model, worker: WorkerInfo):
                ctx = model.to_plain()
                ctx.resume_schedule(worker)
                model.state = ctx.state
                model.worker_name = ctx.current_worker
                model.claim_generation = (model.claim_generation or 0) + 1
                session.add_all(self._get_additional_events(ctx, session))
                loads[worker.name] = loads.get(worker.name, 0) + 1
                assignments.append((ctx, worker.name))

            sticky = (
                session.query(ExecutionContextModel)
                .filter(
                    ExecutionContextModel.state == ExecutionState.RESUMING,
                    ExecutionContextModel.worker_name.in_(list(by_name)),
                )
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
            for model in sticky:
                _assign(model, by_name[model.worker_name])

            remaining = limit - len(assignments)
            if remaining > 0:
                unassigned = (
                    session.query(ExecutionContextModel, WorkflowModel)
                    .join(WorkflowModel)
                    .filter(
                        ExecutionContextModel.state == ExecutionState.RESUMING,
                        ExecutionContextModel.worker_name.is_(None),
                    )
                    .with_for_update(skip_locked=True, of=ExecutionContextModel)
                    .limit(remaining)
                )
                for model, workflow in unassigned:
                    eligible = [
                        w
                        for w in workers
                        if self._has_free_slot(w, loads)
                        and self._worker_matches_workflow(w, workflow)
                    ]
                    if not eligible:
                        continue
                    _assign(model, min(eligible, key=lambda w: loads.get(w.name, 0)))

            session.commit()
        return assignments

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
            # Don't let the fallback hijack a row that was scheduled to a
            # different worker by the dispatcher. CREATED-from-tests still
            # passes through.
            if (
                model.state == ExecutionState.SCHEDULED
                and model.worker_name
                and model.worker_name != worker.name
            ):
                raise ExecutionError(
                    message=(
                        f"Cannot claim execution {execution_id}: scheduled to "
                        f"'{model.worker_name}', not '{worker.name}'"
                    ),
                )
            ctx = model.to_plain()
            ctx.claim(worker)
            model.state = ctx.state
            model.worker_name = ctx.current_worker
            session.add_all(self._get_additional_events(ctx, session))
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
            session.add_all(self._get_additional_events(ctx, session))
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
                # Fence the old owner: without the bump, a partitioned-but-
                # alive worker's late checkpoint (same generation) is accepted
                # and can drag the reset row back to RUNNING with no owner —
                # invisible to dispatch (CREATED-only) and to reaping.
                model.claim_generation = (model.claim_generation or 0) + 1
                session.commit()
                return model.to_plain()
            if model.state in initial_recovery:
                model.state = ExecutionState.CREATED
                model.worker_name = None
                model.claim_generation = (model.claim_generation or 0) + 1
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
        session: Session,
    ) -> list[ExecutionEventModel]:
        # Nothing to reconcile when the incoming context carries no events;
        # skip the round-trip entirely.
        if not ctx.events:
            return []

        # Project only (event_id, type) rather than loading the full event rows
        # (each carries a dill-pickled ``value``), and test membership against a
        # set. This keeps each checkpoint O(new events) instead of
        # O(existing × new) and avoids deserializing the entire event history on
        # every save. The execution_id filter rides the FK index.
        from sqlalchemy import select

        # Restrict the membership read to the ids in the incoming payload:
        # with delta checkpoints the payload carries only unacknowledged
        # events, so this read is O(delta) instead of O(full history).
        incoming_ids = [e.id for e in ctx.events]
        existing = {
            (event_id, event_type)
            for event_id, event_type in session.execute(
                select(
                    ExecutionEventModel.event_id,
                    ExecutionEventModel.type,
                ).where(
                    ExecutionEventModel.execution_id == ctx.execution_id,
                    ExecutionEventModel.event_id.in_(incoming_ids),
                ),
            ).all()
        }
        return [
            ExecutionEventModel.from_plain(ctx.execution_id, e)
            for e in ctx.events
            if (e.id, e.type) not in existing
        ]

    def list(
        self,
        workflow_name: str | None = None,
        workflow_namespace: str | None = None,
        state: ExecutionState | None = None,
        limit: int = 50,
        offset: int = 0,
        workflows: Sequence[tuple[str, str]] | None = None,
    ) -> tuple[list[ExecutionContext], int]:
        """
        List executions with optional filtering and pagination.

        Args:
            workflow_name: Optional workflow name to filter by
            workflow_namespace: Optional workflow namespace to filter by
            state: Optional execution state to filter by
            limit: Maximum number of results to return
            offset: Number of results to skip
            workflows: Optional (namespace, name) allowlist — the
                authorization filter for scoped readers; an empty list
                matches nothing.

        Returns:
            Tuple of (list of ExecutionContext, total count)
        """
        if workflows is not None and not workflows:
            return [], 0

        with self.session() as session:
            query = session.query(ExecutionContextModel)

            if workflow_name:
                query = query.filter(ExecutionContextModel.workflow_name == workflow_name)

            if workflow_namespace:
                query = query.filter(ExecutionContextModel.workflow_namespace == workflow_namespace)

            if state:
                query = query.filter(ExecutionContextModel.state == state)

            if workflows is not None:
                from sqlalchemy import tuple_ as sa_tuple

                query = query.filter(
                    sa_tuple(
                        ExecutionContextModel.workflow_namespace,
                        ExecutionContextModel.workflow_name,
                    ).in_(workflows),
                )

            # Get total count before pagination
            total = query.count()

            # Apply ordering and pagination
            models = (
                query.order_by(ExecutionContextModel.execution_id).offset(offset).limit(limit).all()
            )

            return [model.to_plain() for model in models], total

    def distinct_workflows(
        self,
        workflow_name: str | None = None,
        workflow_namespace: str | None = None,
        state: ExecutionState | None = None,
    ) -> Sequence[tuple[str, str]]:
        with self.session() as session:
            query = session.query(
                ExecutionContextModel.workflow_namespace,
                ExecutionContextModel.workflow_name,
            )
            if workflow_name:
                query = query.filter(ExecutionContextModel.workflow_name == workflow_name)
            if workflow_namespace:
                query = query.filter(ExecutionContextModel.workflow_namespace == workflow_namespace)
            if state:
                query = query.filter(ExecutionContextModel.state == state)
            return [(ns, name) for ns, name in query.distinct().all()]
