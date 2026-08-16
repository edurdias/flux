"""The drain: turn a pending delivery row into a running workflow.

The outbox writes an obligation in the transaction that records the event;
this is where the obligation is met. It runs on the scheduler tick, under
the same cross-replica dispatch lock as the other sweeps, so a delivery is
made once even with several servers up -- ``SKIP LOCKED`` is the second
belt, for the window where a lock changes hands.

Four refusals are as much the point as the delivery itself:

* **A disabled hook fires nothing**, not even the backlog enqueued while it
  was on. ``enabled=false`` is the stop button, and a stop button that lets
  the queue keep draining is not one.
* **The hop guard** runs before anything is authorized or created. A hook
  selecting ``execution:*:*:completed`` whose target is itself a workflow is
  a fork bomb, and the only cheap place to stop it is here, before the
  fan-out exists.
* **Authorization is re-checked at fire time.** A hook outlives the grant
  it was created under; a principal whose rights were revoked in between
  must not keep firing. That denial is terminal, not retried -- a revoked
  permission does not grant itself back, and retrying only buries the
  audit trail.
* **A missing target is terminal too.** Everything else (a busy database, a
  transient catalog read) retries with exponential backoff until the hook's
  ``max_attempts`` is spent.

``create_execution`` and ``authorize`` arrive as arguments rather than being
reached for: the drain is a scheduler concern, the server owns both, and
injecting them is what lets this be tested without one. Both are trusted to
answer for the target as a whole -- ``authorize`` may raise
``WorkflowNotFoundError`` when the target it was asked about is gone, which
lands on the same terminal branch as a creation that finds nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from flux.catalogs import resolve_workflow_ref
from flux.errors import WorkflowNotFoundError
from flux.models import HookDeliveryModel, HookModel, RepositoryFactory
from flux.utils import get_logger

logger = get_logger(__name__)

# (namespace, workflow_name, input_data, *, principal, on_behalf_of) -> id
CreateExecution = Callable[..., Awaitable[str]]
# (principal, permission) -> allowed
Authorize = Callable[[str, str], Awaitable[bool]]

_BACKOFF_CAP_SECONDS = 300
# 2 ** 9 already exceeds the cap, and `max_attempts` is operator-set: clamp
# the exponent rather than only its result, so a wild value cannot make the
# engine compute a colossal integer before min() discards it.
_MAX_BACKOFF_EXPONENT = 9


async def drain_once(
    create_execution: CreateExecution,
    *,
    now: datetime,
    batch_size: int,
    hop_limit: int,
    authorize: Authorize,
) -> int:
    """Settle one batch of due deliveries. Returns how many were handled.

    "Handled" means the row was decided -- delivered, scheduled for another
    attempt, or dead-lettered -- not that a workflow started. A row that is
    not yet due is not handled, and a failure against one delivery never
    stops the batch: each is caught and recorded on its own row.
    """
    if batch_size <= 0:
        return 0

    handled = 0
    with RepositoryFactory.create_repository().session() as session:
        for delivery, hook in _claim(session, now, batch_size):
            try:
                await _deliver(
                    delivery,
                    hook,
                    create_execution=create_execution,
                    authorize=authorize,
                    now=now,
                    hop_limit=hop_limit,
                )
            except Exception as ex:
                # Containment, and the reason this batch is a loop rather
                # than a transaction of equals: a row the delivery path
                # cannot even read -- a payload stored as a list, a
                # workflow_ref that is not a string -- would otherwise
                # escape past the commit below, discarding the decisions of
                # every row already settled in this batch while the
                # executions some of them started stay. `created_at`
                # ordering would then re-claim the same poison row every
                # tick, re-firing its predecessors forever.
                logger.error(
                    f"Hook delivery {delivery.id} could not be settled: {ex}",
                    exc_info=True,
                )
                _dead_letter(delivery, hook, f"delivery could not be settled: {ex}")
            handled += 1
        session.commit()
    return handled


def _claim(session: Session, now: datetime, batch_size: int) -> list[tuple[Any, Any]]:
    """Lock up to ``batch_size`` due deliveries, oldest first.

    ``next_attempt_at`` is NULL on a delivery that has never been tried (the
    enqueue leaves it so), which is due now. ``skip_locked`` is a no-op on
    SQLite and the cross-replica guard on PostgreSQL: a row another
    dispatcher already holds is passed over rather than waited on.
    """
    return (
        session.query(HookDeliveryModel, HookModel)
        .join(HookModel, HookDeliveryModel.hook_id == HookModel.id)
        .filter(
            HookDeliveryModel.status == "pending",
            or_(
                HookDeliveryModel.next_attempt_at.is_(None),
                HookDeliveryModel.next_attempt_at <= now,
            ),
        )
        .order_by(HookDeliveryModel.created_at)
        .with_for_update(skip_locked=True, of=HookDeliveryModel)
        .limit(batch_size)
        .all()
    )


async def _deliver(
    delivery: HookDeliveryModel,
    hook: HookModel,
    *,
    create_execution: CreateExecution,
    authorize: Authorize,
    now: datetime,
    hop_limit: int,
) -> None:
    if not hook.enabled:
        # `enabled=false` is the operator's stop button. The registry stops
        # matching new events on it, but a backlog enqueued before the flip
        # would otherwise still fire, which is precisely what someone
        # reaching for the switch is trying to prevent. Dead-lettered rather
        # than skipped: a skipped row stays pending with nothing to reap it,
        # while a dead one is visible and can be replayed deliberately.
        _dead_letter(delivery, hook, f"hook '{hook.name}' is disabled")
        return

    payload = dict(delivery.payload or {})

    hop = _hop_of(payload)
    if hop >= hop_limit:
        # Deliberately before authorization and before the row is counted as
        # attempted: a chain that has run its length costs one UPDATE.
        _dead_letter(
            delivery,
            hook,
            f"hop limit reached: delivery is at hop {hop} of a maximum {hop_limit}",
        )
        return

    try:
        namespace, workflow_name = resolve_workflow_ref(hook.workflow_ref)
    except ValueError as ex:
        _dead_letter(delivery, hook, f"invalid workflow reference '{hook.workflow_ref}': {ex}")
        return

    # The stored envelope is re-read verbatim -- its event data was redacted
    # at enqueue and is never rebuilt -- and only `attempt` is refreshed to
    # name the delivery about to be made.
    payload["attempt"] = delivery.attempts + 1
    delivery.attempts += 1
    # Written back before the attempt rather than after it, so every way this
    # can end -- delivered, retried, dead-lettered -- leaves the row's stored
    # envelope agreeing with `attempts` and with what the target actually
    # received. The deliveries endpoint serves this payload as "what was
    # sent", and a row reading `attempt: 1` beside `attempts: 3` is a lie an
    # operator debugging a retry would act on. Assigned as a new dict because
    # the JSON column is not mutation-tracked: an in-place edit of the loaded
    # value is not seen at flush.
    delivery.payload = payload

    permission = f"workflow:{namespace}:{workflow_name}:run"
    try:
        if not await authorize(hook.principal, permission):
            _dead_letter(
                delivery,
                hook,
                f"principal '{hook.principal}' lacks permission '{permission}'",
            )
            return
        # The principal travels with the request: the server mints the
        # started execution's own token from it, so a hook-started workflow
        # calling back is the hook's principal rather than anonymous.
        execution_id = await create_execution(
            namespace,
            workflow_name,
            payload,
            principal=hook.principal,
            on_behalf_of=f"hook:{hook.name}",
        )
    except WorkflowNotFoundError as ex:
        # The target is gone. Retrying re-reads the same empty catalog.
        _dead_letter(delivery, hook, f"target workflow '{hook.workflow_ref}' not found: {ex}")
        return
    except Exception as ex:
        _retry_or_give_up(delivery, hook, now, ex)
        return

    delivery.status = "delivered"
    delivery.execution_id = execution_id
    delivery.delivered_at = now
    delivery.next_attempt_at = None
    delivery.last_error = None
    logger.info(
        f"Hook '{hook.name}' fired {hook.workflow_ref} as execution {execution_id} "
        f"(delivery {delivery.id})",
    )


def _hop_of(payload: dict) -> int:
    """The envelope's ``hop``, or 0 when it carries nothing usable.

    A payload written by an older enqueue, or by hand, must fire as a
    first-generation delivery rather than break the drain -- so the fallback
    is the most permissive value the guard still bounds. ``bool`` is excluded
    for being an ``int`` subclass.

    Floored at 0 for the same reason: a negative hop is not a chain with
    room left, it is a stored value that makes ``hop >= hop_limit`` answer
    False for as many generations as it is far below zero.
    """
    hop = payload.get("hop")
    if not isinstance(hop, int) or isinstance(hop, bool):
        return 0
    return max(hop, 0)


def _retry_or_give_up(
    delivery: HookDeliveryModel,
    hook: HookModel,
    now: datetime,
    error: Exception,
) -> None:
    if delivery.attempts >= hook.max_attempts:
        _dead_letter(
            delivery,
            hook,
            f"gave up after {delivery.attempts} attempt(s): {error}",
        )
        return

    delay = min(2 ** min(delivery.attempts, _MAX_BACKOFF_EXPONENT), _BACKOFF_CAP_SECONDS)
    delivery.next_attempt_at = now + timedelta(seconds=delay)
    delivery.last_error = str(error)
    logger.warning(
        f"Hook '{hook.name}' delivery {delivery.id} failed "
        f"(attempt {delivery.attempts}/{hook.max_attempts}), retrying in {delay}s: {error}",
    )


def _dead_letter(delivery: HookDeliveryModel, hook: HookModel, reason: str) -> None:
    delivery.status = "dead"
    delivery.last_error = reason
    # Nothing will pick this row up again; leaving a due time on it would
    # only read as a retry that never comes.
    delivery.next_attempt_at = None
    logger.warning(f"Hook '{hook.name}' delivery {delivery.id} dead-lettered: {reason}")
