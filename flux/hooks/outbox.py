"""The transactional outbox: turn a save's events into pending deliveries.

``enqueue`` runs inside the transaction that persists the events -- it never
commits, never opens its own session -- so an event and the obligations it
creates share a fate. Nothing is delivered here: a row in ``hook_deliveries``
is all a checkpoint pays, and the drain picks it up later. Two properties
follow, and both are load-bearing:

* **No event is missed.** A crash between "event recorded" and "hook fired"
  cannot happen, because there is no between.
* **No delivery blocks a checkpoint.** This code sits on the hottest write
  path in the engine (every checkpoint of every execution), so it returns
  before touching anything when hooks are off or nothing subscribes, and a
  failure anywhere inside is logged and swallowed rather than allowed to fail
  the execution whose state is being written.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from flux.config import Configuration
from flux.domain.events import ExecutionEvent
from flux.domain.execution_context import ExecutionContext
from flux.hooks.envelope import build_envelope, parent_hop
from flux.hooks.registry import HookRegistry
from flux.hooks.selectors import HookEvent, events_from_save, selector_matches
from flux.models import HookDeliveryModel
from flux.utils import get_logger

logger = get_logger(__name__)


def enqueue(
    session: Session,
    ctx: ExecutionContext,
    new_events: Sequence[Any],
) -> int:
    """Write one pending delivery per (hook, event) match. Returns rows added.

    ``new_events`` is the set of events this save is about to persist --
    domain ``ExecutionEvent``s or the ``ExecutionEventModel``s built from
    them, whichever the call site has in hand. Deliveries are added to
    ``session`` and left uncommitted: the caller's transaction decides
    whether they exist.
    """
    try:
        # Ordering is the whole point of this block: every checkpoint of
        # every execution runs it, so the two cheapest answers come first.
        # `has_any()` reads a cached snapshot, and neither branch builds a
        # hook event or issues a query.
        if not Configuration.get().settings.hooks.enabled:
            return 0
        registry = HookRegistry.create()
        if not registry.has_any():
            return 0
        rows = _pending_rows(ctx, new_events, registry)
    except Exception as ex:
        # A broken selector, an unreachable registry, an envelope that will
        # not build: none of it is the executing workflow's problem.
        logger.warning(f"Hook enqueue skipped for execution {ctx.execution_id}: {ex}")
        return 0

    if not rows:
        return 0

    # Flush the caller's own pending state here rather than let the first
    # savepoint do it (begin_nested() flushes unconditionally before opening
    # one). Both keep the execution insert outside the savepoint, so a
    # duplicate delivery cannot roll it back — but flushing here also means a
    # constraint violation from the caller's *own* writes raises at this
    # line, where the save's IntegrityError handling still covers it, instead
    # of inside the loop below, where the duplicate-delivery `except` would
    # mistake it for one of ours and swallow it.
    session.flush()

    return _insert(session, rows, ctx)


def _pending_rows(
    ctx: ExecutionContext,
    new_events: Sequence[Any],
    registry: HookRegistry,
) -> list[HookDeliveryModel]:
    events = events_from_save(ctx, _as_domain_events(new_events))
    if not events:
        return []

    # Computed here, not at drain time: the parent execution's input is in
    # hand on this path, and re-reading it later would cost the drain a query
    # per delivery. The drain enforces the limit; the enqueue only records
    # where in a chain this delivery sits.
    hop = parent_hop(ctx.input) + 1

    rows = []
    for event in events:
        for hook in registry.matches(event):
            delivery_id = uuid4().hex
            rows.append(
                HookDeliveryModel(
                    id=delivery_id,
                    hook_id=hook.id,
                    event_key=event.delivery_key,
                    # The whole envelope, not the pieces to rebuild one: the
                    # drain re-reads this verbatim and overwrites only
                    # `attempt`. `attempt` is 1 (the delivery about to be
                    # made) while the row's `attempts` counter stays 0
                    # (attempts actually made).
                    payload=build_envelope(
                        hook,
                        _matched_selector(hook.selectors, event),
                        event,
                        delivery_id=delivery_id,
                        attempt=1,
                        hop=hop,
                    ),
                    status="pending",
                ),
            )
    return rows


def _insert(session: Session, rows: list[HookDeliveryModel], ctx: ExecutionContext) -> int:
    added = 0
    for row in rows:
        try:
            # One savepoint per row: the unique constraint on
            # (hook_id, event_key) is what makes a re-sent checkpoint
            # idempotent, and absorbing its IntegrityError inside a savepoint
            # is what keeps that duplicate from poisoning the transaction
            # carrying the execution's state.
            with session.begin_nested():
                session.add(row)
            added += 1
        except IntegrityError:
            continue
        except Exception as ex:
            logger.warning(f"Hook enqueue failed for execution {ctx.execution_id}: {ex}")
            break
    return added


def _as_domain_events(new_events: Sequence[Any]) -> list[ExecutionEvent]:
    """Accept either side of the persistence boundary.

    The insert path has the context's own ``ExecutionEvent``s; the update
    paths have the ``ExecutionEventModel``s that survived deduplication
    against what is already stored -- and those, not the context's full
    history, are the events a save actually adds.
    """
    return [e if isinstance(e, ExecutionEvent) else e.to_plain() for e in new_events]


def _matched_selector(selectors: Sequence[str], event: HookEvent) -> str:
    """The hook's first selector that fires for ``event``.

    A hook can carry several; the envelope names the one that brought this
    delivery about. ``registry.matches`` already established that at least
    one does, so the fallback never fires in practice -- it only keeps a
    hypothetical mismatch from raising on the write path.
    """
    return next(
        (selector for selector in selectors if selector_matches(selector, event.key)),
        selectors[0] if selectors else "",
    )
