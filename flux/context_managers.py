from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import and_
from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from flux import ExecutionContext
from flux.domain import ExecutionState
from flux.errors import ExecutionContextNotFoundError, ExecutionError, StaleClaimError
from flux.hooks.outbox import enqueue
from flux.models import ExecutionEventModel
from flux.models import ExecutionContextModel
from flux.models import RepositoryFactory
from flux.models import WorkflowModel
from flux.utils import get_logger
from flux.worker_registry import WorkerInfo

if TYPE_CHECKING:
    from flux.unit_of_work import UnitOfWork

logger = get_logger(__name__)


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
        required_worker: str | None = None,
        routing_input: dict | None = None,
        park_ttl: int | None = None,
        name: str | None = None,
    ) -> ExecutionContext:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def save_checked(
        self,
        ctx: ExecutionContext,
        *,
        uow: UnitOfWork | None = None,
        preferred_worker: str | None = None,
        required_worker: str | None = None,
        routing_input: dict | None = None,
        park_ttl: int | None = None,
        name: str | None = None,
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

    def rename(self, execution_id: str, name: str) -> None:  # pragma: no cover
        """Set an execution's operator-facing label. Raises
        ``ExecutionContextNotFoundError`` when the execution doesn't exist."""
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
        *,
        exclude_ids: Sequence[str] | None = None,
        unmatched: set[str] | None = None,
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
                    ExecutionContextModel.name,
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
                "name": row.name,
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
        required_worker: str | None = None,
        routing_input: dict | None = None,
        park_ttl: int | None = None,
        name: str | None = None,
    ) -> ExecutionContext:
        self.save_checked(
            ctx,
            uow=uow,
            preferred_worker=preferred_worker,
            required_worker=required_worker,
            routing_input=routing_input,
            park_ttl=park_ttl,
            name=name,
        )
        return ctx

    def save_checked(
        self,
        ctx: ExecutionContext,
        *,
        uow: UnitOfWork | None = None,
        preferred_worker: str | None = None,
        required_worker: str | None = None,
        routing_input: dict | None = None,
        park_ttl: int | None = None,
        name: str | None = None,
    ) -> bool:
        if uow is not None:
            return self._save_with_session(
                ctx,
                uow.session,
                manage_transaction=False,
                preferred_worker=preferred_worker,
                required_worker=required_worker,
                routing_input=routing_input,
                park_ttl=park_ttl,
                name=name,
            )
        with self.session() as session:
            return self._save_with_session(
                ctx,
                session,
                manage_transaction=True,
                preferred_worker=preferred_worker,
                required_worker=required_worker,
                routing_input=routing_input,
                park_ttl=park_ttl,
                name=name,
            )

    @staticmethod
    def _stamp_park_deadline(model, park_ttl: int | None = None) -> None:
        """Start the unclaimed clock. 0/unset means park indefinitely (NULL).

        int() + fallback: a mocked/partial Configuration (common in tests)
        must degrade to "no deadline", never break the caller.
        """
        ttl = park_ttl
        if ttl is None:
            try:
                from flux.config import Configuration as _Configuration

                ttl = int(_Configuration.get().settings.workers.park_ttl)
            except Exception:
                ttl = 0
        model.park_deadline = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl) if ttl and ttl > 0 else None
        )

    def _save_with_session(
        self,
        ctx: ExecutionContext,
        session: Session,
        *,
        manage_transaction: bool,
        preferred_worker: str | None = None,
        required_worker: str | None = None,
        routing_input: dict | None = None,
        park_ttl: int | None = None,
        name: str | None = None,
    ) -> bool:
        try:
            model = self._lock_for_write(session, ctx.execution_id)
            if model:
                accepted = _accept_state_write(ctx.state, model.state)
                if accepted:
                    model.state = ctx.state
                    model.output = ctx.output
                    self._sync_wake_columns(model, ctx)
                    if name is not None:
                        # Only ever set, never cleared here — a later
                        # state-update save (no name kwarg) must not erase
                        # an operator-set name. The in-memory ctx is synced
                        # alongside the row so a caller serializing the
                        # returned ctx sees what was just persisted.
                        model.name = name
                        ctx._name = name
                    if (
                        ctx.state == ExecutionState.RESUMING
                        and model.required_worker
                        and model.worker_name is None
                    ):
                        # Entering the stuck state (issue #212). Restarting
                        # here rather than only in release_worker covers a row
                        # released while PAUSED, whose submission-time deadline
                        # would otherwise be long expired by the time it wakes
                        # and would fail it on arrival.
                        self._stamp_park_deadline(model)
                    self._persist_events(ctx, session)
            else:
                accepted = True
                new_model = ExecutionContextModel.from_plain(ctx)
                self._sync_wake_columns(new_model, ctx)
                if preferred_worker:
                    # Same transaction as the insert: event-mode dispatch can
                    # pick a fresh row up immediately, so a hint written in a
                    # follow-up UPDATE could be missed.
                    new_model.preferred_worker = preferred_worker
                if required_worker:
                    new_model.required_worker = required_worker
                if routing_input:
                    new_model.routing_input = routing_input
                if name is not None:
                    new_model.name = name
                    ctx._name = name
                # Park TTL (issue #157): per-run override wins; otherwise the
                # config default. 0 / unset means park indefinitely (NULL).
                # int() + fallback: a mocked/partial Configuration (common in
                # tests) must degrade to "no deadline", never break a save.
                self._stamp_park_deadline(new_model, park_ttl)
                session.add(new_model)
                # The only persist path that cannot use _persist_events: the
                # insert writes its events through the relationship, so
                # adding them again would duplicate every row. Every one of
                # them is new — an execution paused (or failed) on its very
                # first save reaches the hooks only from here.
                enqueue(session, ctx, ctx.events)
            if manage_transaction:
                session.commit()
            return accepted
        except IntegrityError:  # pragma: no cover
            if manage_transaction:
                session.rollback()
            raise

    def rename(self, execution_id: str, name: str) -> None:
        """Set an execution's operator-facing label, independent of state.

        Unlike ``save(..., name=...)`` this needs no ``ExecutionContext`` —
        the PUT /executions/{id}/name route only has an id and a string.
        """
        with self.session() as session:
            model = self._lock_for_write(session, execution_id)
            if not model:
                raise ExecutionContextNotFoundError(execution_id)
            model.name = name
            session.commit()

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
            if expected_claim_generation is None and (model.claim_generation or 0) > 0:
                # The fence was opt-in per request: omitting the generation
                # skipped it entirely, so a write fenced at claim time could
                # land by simply not carrying the header — worst case a late
                # RUNNING write onto an evicted-and-unclaimed row, leaving it
                # RUNNING with no owner, invisible to dispatch, the reaper,
                # and the cancellation sweep. Ever-claimed rows now require
                # the fence. The one writer with no claim by design stays
                # legal: a worker resolving an unowned CANCELLING row to a
                # terminal state (issue #189).
                if not (model.state == ExecutionState.CANCELLING and ctx.state in _TERMINAL_STATES):
                    raise StaleClaimError(
                        ctx.execution_id,
                        actual=model.claim_generation or 0,
                    )
            if _accept_state_write(ctx.state, model.state):
                model.state = ctx.state
                model.output = ctx.output
                self._sync_wake_columns(model, ctx)
                # The distributed checkpoint path lands here, not in save():
                # most engine events a hook can subscribe to arrive through
                # this call.
                self._persist_events(ctx, session)
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

    def _worker_matches_workflow(
        self,
        worker: WorkerInfo,
        workflow: WorkflowModel,
        model,
    ) -> bool:
        """Takes the row rather than its input so a caller cannot pass one
        value source and forget the other — a missed routing value resolves
        against ``input`` and silently matches on the wrong thing (#211)."""
        from flux.domain.resource_request import worker_matches

        return worker_matches(
            worker,
            workflow.requests,
            workflow.affinity,
            runner=(workflow.wf_metadata or {}).get("runner"),
            input_value=model.input,
            routing_value=model.routing_input,
        )

    def _affinity_diagnostic(self, workflow: WorkflowModel, model) -> str | None:
        """Execution-level reason a require(...) affinity can never match.

        Non-None only for worker-independent problems (unresolved input on a
        non-optional term, invalid resolved key, malformed spec) — those fail
        the execution instead of parking it forever. Static dict affinity
        never diagnoses: an unmatched label is fleet-dependent and waits for
        a matching worker, as it always has.
        """
        if not isinstance(workflow.affinity, (list, tuple)):
            return None
        from flux.routing import require_diagnostic

        return require_diagnostic(workflow.affinity, model.input, model.routing_input)

    def _fail_undispatchable(
        self,
        model,
        session: Session,
        diagnostic: str,
        error_type: str = "AffinityResolutionError",
    ) -> None:
        ctx = model.to_plain()
        ctx.fail(
            ctx.execution_id,
            {"type": error_type, "message": diagnostic},
        )
        model.state = ctx.state
        model.output = ctx.output
        self._persist_events(ctx, session)
        logger.warning(f"Execution {ctx.execution_id} failed at dispatch: {diagnostic}")

    def fail_expired_parked(self, now: datetime | None = None) -> list[str]:
        """Fail executions still unclaimed past their park deadline (issue #157).

        A parked execution — state CREATED, waiting for a worker its
        constraints match — normally waits indefinitely; that is right for
        elastic batch fleets and wrong for an interactive caller, for whom a
        park is indistinguishable from a hang. Rows that opted into a bound
        (``park_deadline`` set at submission, from the per-run override or
        the ``[flux.workers] park_ttl`` default) are failed terminally with a
        diagnosable ``ParkTimeoutError`` once the deadline passes.

        Runs in the scheduler tick under the dispatch lock. Row locks
        (``skip_locked``) keep the sweep from racing a concurrent claim on
        PostgreSQL: a row a worker is claiming right now is simply skipped
        and re-examined next tick — by which time it is no longer CREATED.
        Returns the failed execution ids.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        failed: list[str] = []
        with self.session() as session:
            query = (
                session.query(ExecutionContextModel, WorkflowModel)
                .join(WorkflowModel)
                .filter(
                    or_(
                        ExecutionContextModel.state == ExecutionState.CREATED,
                        # A bound execution released back to RESUMING waits on
                        # one worker that may never return; nothing else can
                        # take it, so it needs the same bound as an unclaimed
                        # row (issue #212).
                        and_(
                            ExecutionContextModel.state == ExecutionState.RESUMING,
                            ExecutionContextModel.required_worker.isnot(None),
                            ExecutionContextModel.worker_name.is_(None),
                        ),
                    ),
                    ExecutionContextModel.park_deadline.isnot(None),
                    ExecutionContextModel.park_deadline < now,
                )
                .with_for_update(skip_locked=True, of=ExecutionContextModel)
            )
            for model, workflow in query:
                constraints: list[str] = []
                if model.required_worker:
                    # Named first: it is the likeliest single cause, and the
                    # caller who set the header cannot see it any other way.
                    constraints.append(f"bound to worker '{model.required_worker}'")
                if workflow.affinity:
                    constraints.append(f"affinity={workflow.affinity!r}")
                if workflow.requests:
                    constraints.append("resource requests present")
                detail = "; ".join(constraints) if constraints else "no declared constraints"
                verb = (
                    "resumed"
                    if model.state == ExecutionState.RESUMING
                    else "claimed this execution"
                )
                self._fail_undispatchable(
                    model,
                    session,
                    (
                        f"No eligible worker {verb} before its "
                        f"park deadline ({model.park_deadline}); {detail}. "
                        "A worker matching the execution's constraints never "
                        "became available within the park TTL."
                    ),
                    error_type="ParkTimeoutError",
                )
                failed.append(model.execution_id)
            session.commit()
        return failed

    def resolve_orphaned_cancellations(
        self,
        connected_workers: Sequence[str],
        grace_seconds: int,
        now: datetime | None = None,
        liveness_seconds: int = 60,
    ) -> list[str]:
        """Resolve CANCELLING rows whose delivery target is gone (issue #225).

        Cancellation delivery matches ``worker_name`` against connected
        workers, so two orphan classes never resolve on their own: rows
        cancelled while parked (``worker_name`` NULL — matched by nobody,
        and dispatch skips non-CREATED rows so nobody ever claims them
        either) and rows whose worker died for good (eviction deliberately
        leaves CANCELLING untouched).

        NULL-owner rows resolve immediately: nothing was ever asked to
        resolve them, and a dispatched row always has a worker stamped, so
        NULL cannot be a claim in flight. Named rows resolve only when the
        worker is not connected and the row has been CANCELLING longer than
        ``grace_seconds`` (measured from the newest WORKFLOW_CANCELLING
        event, written in the same transaction as the state) — a worker in
        reconnect backoff that returns inside the grace still resolves its
        own row, which actually interrupts the running body instead of
        abandoning it. ``grace_seconds`` <= 0 disables the named sweep.

        Runs in the scheduler tick under the dispatch lock. Same locking as
        the park sweep: a concurrent worker checkpoint wins the row lock and
        the sweep skips; a sweep write landing first makes the worker's late
        write a no-op (``_accept_state_write``).

        ``connected_workers`` is the sweeping replica's SSE view, which is
        per-replica by construction — a worker connected to another replica
        is invisible in it. Recent heartbeats (``workers.last_seen_at``,
        written on every pong regardless of replica) are unioned in as the
        cross-replica liveness signal, ``liveness_seconds`` wide, so the
        sweep only resolves rows whose worker no replica has heard from.
        """
        from flux.domain.events import ExecutionEventType
        from flux.models import WorkerModel

        if now is None:
            now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=grace_seconds)
        resolved: list[str] = []
        with self.session() as session:
            # last_seen_at is naive UTC by convention (see worker_registry).
            live_cutoff = now.replace(tzinfo=None) - timedelta(seconds=liveness_seconds)
            connected = set(connected_workers) | {
                row.name
                for row in session.query(WorkerModel.name)
                .filter(
                    WorkerModel.last_seen_at.isnot(None),
                    WorkerModel.last_seen_at >= live_cutoff,
                )
                .all()
            }
            query = (
                session.query(ExecutionContextModel)
                .filter(ExecutionContextModel.state == ExecutionState.CANCELLING)
                .with_for_update(skip_locked=True)
            )
            for model in query:
                if model.worker_name is not None:
                    if grace_seconds <= 0 or model.worker_name in connected:
                        continue
                    stamped = (
                        session.query(ExecutionEventModel.time)
                        .filter(
                            ExecutionEventModel.execution_id == model.execution_id,
                            ExecutionEventModel.type == ExecutionEventType.WORKFLOW_CANCELLING,
                        )
                        .order_by(ExecutionEventModel.id.desc())
                        .limit(1)
                        .scalar()
                    )
                    # SQLite round-trips the aware write back naive.
                    if stamped is not None and stamped.tzinfo is None:
                        stamped = stamped.replace(tzinfo=timezone.utc)
                    # A CANCELLING row without its event is anomalous; waiting
                    # forever on it repeats the bug, so only a fresh stamp
                    # defers the sweep.
                    if stamped is not None and stamped > cutoff:
                        continue
                ctx = model.to_plain()
                ctx.cancel()
                model.state = ctx.state
                self._persist_events(ctx, session)
                owner = model.worker_name or "no worker"
                logger.warning(
                    f"Execution {model.execution_id} cancellation had no live "
                    f"delivery target ({owner}); resolved by the scheduler sweep",
                )
                resolved.append(model.execution_id)
            session.commit()
        return resolved

    @staticmethod
    def _sync_wake_columns(model, ctx: ExecutionContext) -> None:
        """Mirror the pause's wake condition (issue #145) onto the row.

        Stamped in the same transaction as the PAUSED state write — there is
        no window in which the execution is PAUSED without its wake being
        durable (the race class documented for approvals in #70). Every
        non-PAUSED state write clears the columns, so resuming, cancelling,
        or finishing an execution retires its pending wake atomically.
        """
        if ctx.state != ExecutionState.PAUSED:
            model.wake_at = None
            model.wake_on_complete = None
            return

        wake_at_raw: str | None = None
        wake_on_complete: str | None = None
        for event in reversed(ctx.events):
            if event.type.value == "WORKFLOW_PAUSED":
                value = event.value if isinstance(event.value, dict) else {}
                wake_at_raw = value.get("wake_at")
                wake_on_complete = value.get("wake_on_complete")
                break
        wake_at = None
        if wake_at_raw:
            try:
                wake_at = datetime.fromisoformat(wake_at_raw)
            except ValueError:
                logger.error(
                    f"Execution {ctx.execution_id}: unparsable wake_at "
                    f"{wake_at_raw!r}; treating the pause as indefinite",
                )
        model.wake_at = wake_at
        model.wake_on_complete = wake_on_complete or None

    def fire_due_wakes(self, now: datetime | None = None) -> list[str]:
        """Resume paused executions whose wake condition fired (issue #145).

        Two conditions, read from the columns ``_sync_wake_columns`` stamps:
        a timed wake (``wake_at <= now``) and a completion wake (the watched
        execution reached a terminal state). Firing is exactly the operator
        resume transition — ``start_resuming()`` — so downstream dispatch is
        unchanged, and the resume's state write clears the wake columns, so
        an overdue backlog after downtime fires each wake once.

        Runs in the scheduler tick under the dispatch lock (one firing
        replica). Returns the resumed execution ids.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        terminal = (
            ExecutionState.COMPLETED,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
        )
        due: list[str] = []
        with self.session() as session:
            timed = (
                session.query(ExecutionContextModel.execution_id)
                .filter(
                    ExecutionContextModel.state == ExecutionState.PAUSED,
                    ExecutionContextModel.wake_at.isnot(None),
                    ExecutionContextModel.wake_at <= now,
                )
                .all()
            )
            due.extend(row.execution_id for row in timed)

            watchers = (
                session.query(
                    ExecutionContextModel.execution_id,
                    ExecutionContextModel.wake_on_complete,
                )
                .filter(
                    ExecutionContextModel.state == ExecutionState.PAUSED,
                    ExecutionContextModel.wake_on_complete.isnot(None),
                )
                .all()
            )
            # One query for every watched execution's state, then check in
            # memory — a per-watcher lookup would make the tick O(paused
            # executions) round-trips.
            watched_ids = {watched_id for _, watched_id in watchers}
            watched_states = (
                dict(
                    session.query(
                        ExecutionContextModel.execution_id,
                        ExecutionContextModel.state,
                    ).filter(ExecutionContextModel.execution_id.in_(watched_ids)),
                )
                if watched_ids
                else {}
            )
            for execution_id, watched_id in watchers:
                watched_state = watched_states.get(watched_id)
                # A watched id that does not exist wakes immediately: waiting
                # forever on a typo is strictly worse than resuming, and the
                # workflow can inspect the child itself.
                if watched_state is None or watched_state in terminal:
                    due.append(execution_id)

        resumed: list[str] = []
        for execution_id in due:
            try:
                ctx = self.get(execution_id)
                if not ctx.is_paused:
                    continue  # raced a concurrent resume/cancel; theirs wins
                ctx.start_resuming()
                self.save(ctx)
                resumed.append(execution_id)
            except Exception:
                logger.error(
                    f"Failed to fire wake for execution {execution_id}",
                    exc_info=True,
                )
        return resumed

    @staticmethod
    def _has_bound_work(session: Session, worker: WorkerInfo) -> bool:
        return session.query(
            session.query(ExecutionContextModel)
            .filter(
                ExecutionContextModel.state == ExecutionState.CREATED,
                ExecutionContextModel.required_worker == worker.name,
            )
            .exists(),
        ).scalar()

    @staticmethod
    def _required_worker_clause(worker: WorkerInfo):
        """SQL rather than the per-row matcher: the unconstrained branch below
        never runs one, so a bound execution of an unconstrained workflow would
        go to whichever worker polled first."""
        return or_(
            ExecutionContextModel.required_worker.is_(None),
            ExecutionContextModel.required_worker == worker.name,
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
            .filter(self._required_worker_clause(worker))
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
            diagnostic = self._affinity_diagnostic(workflow, model)
            if diagnostic:
                self._fail_undispatchable(model, session, diagnostic)
                # Flag the staged failure so the caller commits it even when
                # nothing ends up claimed on this poll tick.
                session.info["affinity_failed"] = True
                continue
            if not self._worker_matches_workflow(worker, workflow, model):
                continue
            return model, workflow
        return None, None

    def next_execution(self, worker: WorkerInfo) -> ExecutionContext | None:
        with self.session() as session:
            # The load gate spreads work; bound work cannot be spread, so
            # letting it gate a binding parks an execution whose worker is
            # online and free just because a peer is emptier.
            if not self._is_least_loaded_worker(worker, session) and not self._has_bound_work(
                session,
                worker,
            ):
                return None

            self._refresh_worker_metadata(session, [worker])
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
                self._persist_events(ctx, session)
                session.commit()
                return ctx

            if session.info.pop("affinity_failed", False):
                session.commit()
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
                self._refresh_worker_metadata(session, [worker])
                fallback_query = (
                    session.query(ExecutionContextModel, WorkflowModel)
                    .join(WorkflowModel)
                    .filter(
                        ExecutionContextModel.state == ExecutionState.RESUMING,
                        ExecutionContextModel.worker_name.is_(None),
                        self._required_worker_clause(worker),
                    )
                    .with_for_update(skip_locked=True)
                )
                for model, workflow in fallback_query:
                    if self._worker_matches_workflow(worker, workflow, model):
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
            self._persist_events(ctx, session)
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

    def _refresh_worker_metadata(self, session: Session, workers: list[WorkerInfo]) -> None:
        """Overwrite each WorkerInfo's server-held metadata from the DB.

        Dispatch matches against in-memory snapshots taken at SSE connect,
        but admin metadata writes can land on any replica at any time — so
        every dispatch transaction re-reads the column (one PK-indexed query
        per batch), making updates effective on the next claim/dispatch
        without the worker reconnecting.
        """
        if not workers:
            return
        from flux.models import WorkerModel

        rows = (
            session.query(WorkerModel.name, WorkerModel.worker_metadata)
            .filter(WorkerModel.name.in_([w.name for w in workers]))
            .all()
        )
        metadata = dict(rows)
        for w in workers:
            w.metadata = metadata.get(w.name) or None

    def next_executions_batch(
        self,
        workers: list[WorkerInfo],
        limit: int,
        *,
        exclude_ids: Sequence[str] | None = None,
        unmatched: set[str] | None = None,
    ) -> list[tuple[ExecutionContext, str]]:
        """Claim up to ``limit`` pending executions and assign them across workers.

        One transaction per call: rows are locked with ``SKIP LOCKED`` (so
        concurrent dispatchers on other replicas pass over each other's
        claims), matched against each worker's labels/resources, and assigned
        to the least-loaded eligible worker. The load aggregate runs once per
        batch — not once per worker per poll tick as in ``next_execution``.
        Unmatched rows keep state CREATED and their locks release at commit.

        ``exclude_ids`` skips rows the caller already knows no worker can take;
        ``unmatched`` collects the ids of rows found unplaceable here. Together
        they let the caller advance past a head of unmatchable work instead of
        re-selecting it every call (#213). Saturation is deliberately not
        reported as unmatchable — those rows are placeable once a slot frees.
        """
        if not workers or limit <= 0:
            return []
        assignments: list[tuple[ExecutionContext, str]] = []
        with self.session() as session:
            loads = self._worker_load_map(session, [w.name for w in workers])
            self._refresh_worker_metadata(session, workers)
            query = (
                session.query(ExecutionContextModel, WorkflowModel)
                .join(WorkflowModel)
                .filter(ExecutionContextModel.state == ExecutionState.CREATED)
            )
            if exclude_ids:
                # Rows the caller already found unplaceable. Without this the
                # LIMIT re-selects the same head every call, so matchable work
                # queued behind an unmatchable row is never reached (#213).
                query = query.filter(ExecutionContextModel.execution_id.notin_(exclude_ids))
            query = query.with_for_update(skip_locked=True, of=ExecutionContextModel).limit(limit)
            for model, workflow in query:
                diagnostic = self._affinity_diagnostic(workflow, model)
                if diagnostic:
                    self._fail_undispatchable(model, session, diagnostic)
                    continue
                with_capacity = [w for w in workers if self._has_free_slot(w, loads)]
                if not with_capacity:
                    # Saturated, not unmatchable: these rows become placeable
                    # as soon as a slot frees, so they must not be excluded
                    # from the next pass, and scanning on would place nothing.
                    break
                eligible = [
                    w
                    for w in with_capacity
                    # Binding before the matcher: a bound execution has one
                    # candidate, so matching the fleet is discarded work.
                    if (not model.required_worker or w.name == model.required_worker)
                    and self._worker_matches_workflow(w, workflow, model)
                ]
                if not eligible:
                    if unmatched is not None:
                        unmatched.add(model.execution_id)
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
                        routing_value=model.routing_input,
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
                self._persist_events(ctx, session)
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
                self._persist_events(ctx, session)
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
                self._refresh_worker_metadata(session, workers)
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
                        and (not model.required_worker or w.name == model.required_worker)
                        and self._worker_matches_workflow(w, workflow, model)
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
            # The dispatch queries filter on the binding, but any registered
            # worker can POST here and the fallback above accepts a CREATED
            # row — exactly where a bound execution waits.
            if model.required_worker and model.required_worker != worker.name:
                raise ExecutionError(
                    message=(
                        f"Cannot claim execution {execution_id}: bound to "
                        f"'{model.required_worker}', not '{worker.name}'"
                    ),
                )
            ctx = model.to_plain()
            ctx.claim(worker)
            model.state = ctx.state
            model.worker_name = ctx.current_worker
            self._persist_events(ctx, session)
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
            self._persist_events(ctx, session)
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
                if model.required_worker:
                    # Now waiting on one worker with no fallback, so restart
                    # the clock here rather than reusing the submission-time
                    # deadline, which a long run would already be past.
                    self._stamp_park_deadline(model)
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
            if model.required_worker and model.state == ExecutionState.RESUMING:
                self._stamp_park_deadline(model)
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

    def _persist_events(self, ctx: ExecutionContext, session: Session) -> None:
        """The one door for writing an execution's new events.

        Every path that persists events — checkpoints, dispatch, claim,
        resume, the failure and cancellation sweeps — goes through here, so
        the outbox cannot silently miss a state transition because a new
        persist path forgot to enqueue. Routing dispatch through it costs
        nothing when no hook exists: ``enqueue`` answers from config and a
        cached index before touching the events or the database.
        """
        new_events = self._get_additional_events(ctx, session)
        session.add_all(new_events)
        enqueue(session, ctx, new_events)

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
