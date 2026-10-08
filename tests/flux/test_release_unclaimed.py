"""Dispatched-but-unclaimed executions return to dispatch.

A dispatch frame can be lost in transit: the server dequeued it (event mode)
or marked the row SCHEDULED to the worker (poll mode), the stream stalled,
and the worker reconnected inside the eviction window. Nothing then released
the row, which stayed assigned to the worker holding one of its capacity
slots until a server restart. Two releases cover it -- the worker's reconnect
and a claim deadline -- and the frame's claim generation fences the lost
frame if it does arrive.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Response
from starlette.routing import Route

from flux.config import Configuration
from flux.context_managers import DatabaseContextManager
from flux.domain import ExecutionState
from flux.domain.events import ExecutionEventType
from flux.domain.execution_context import ExecutionContext
from flux.errors import StaleClaimError
from flux.models import ExecutionContextModel, ExecutionEventModel, RepositoryFactory
from flux.security.identity import FluxIdentity
from flux.worker_registry import (
    DatabaseWorkerRegistry,
    WorkerResourcesInfo,
    WorkerRuntimeInfo,
)


@pytest.fixture
def cm(tmp_path):
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'release.db'}")
    manager = DatabaseContextManager()
    repo = RepositoryFactory.create_repository()
    from flux.models import WorkflowModel

    with repo.session() as session:
        session.add(
            WorkflowModel(
                id="default/wf",
                name="wf",
                version=1,
                imports=[],
                source=b"async def wf(ctx): pass",
                namespace="default",
            ),
        )
        session.commit()
    return manager


def _worker(name: str):
    registry = DatabaseWorkerRegistry()
    registry.register(
        name=name,
        runtime=WorkerRuntimeInfo(os_name="Linux", os_version="6", python_version="3.12"),
        packages=[],
        resources=WorkerResourcesInfo(
            cpu_total=1,
            cpu_available=1,
            memory_total=1,
            memory_available=1,
            disk_total=1,
            disk_free=1,
            gpus=[],
        ),
    )
    return registry.get(name)


def _submit(cm) -> str:
    ctx = cm.save(
        ExecutionContext(
            workflow_id="default/wf",
            workflow_namespace="default",
            workflow_name="wf",
        ),
    )
    return ctx.execution_id


def _schedule_to(cm, worker) -> str:
    execution_id = _submit(cm)
    assignments = cm.next_executions_batch([worker], limit=1)
    assert [ctx.execution_id for ctx, _ in assignments] == [execution_id]
    return execution_id


def _row(execution_id: str):
    with RepositoryFactory.create_repository().session() as session:
        model = session.get(ExecutionContextModel, execution_id)
        return model.state, model.worker_name, model.claim_generation


def _age_assignment(execution_id: str, seconds: int) -> None:
    """Backdate the schedule event, which is when the row was assigned."""
    with RepositoryFactory.create_repository().session() as session:
        events = (
            session.query(ExecutionEventModel)
            .filter(
                ExecutionEventModel.execution_id == execution_id,
                ExecutionEventModel.type.in_(
                    (
                        ExecutionEventType.WORKFLOW_SCHEDULED,
                        ExecutionEventType.WORKFLOW_RESUME_SCHEDULED,
                    ),
                ),
            )
            .all()
        )
        assert events
        for event in events:
            event.time = datetime.now(timezone.utc) - timedelta(seconds=seconds)
        session.commit()


def _set_state(execution_id: str, state: ExecutionState) -> None:
    with RepositoryFactory.create_repository().session() as session:
        session.get(ExecutionContextModel, execution_id).state = state
        session.commit()


# -- release_unclaimed --------------------------------------------------------


def test_reconnect_release_returns_only_that_workers_unclaimed_rows(cm):
    w1, w2 = _worker("w1"), _worker("w2")
    lost = _schedule_to(cm, w1)
    other = _schedule_to(cm, w2)
    claimed = _schedule_to(cm, w1)
    cm.claim(claimed, w1)

    released = cm.release_unclaimed(worker_name="w1")

    assert released == [lost]
    # Same transition as unclaim: dispatchable, unowned, generation bumped.
    assert _row(lost) == (ExecutionState.CREATED, None, 2)
    assert _row(other) == (ExecutionState.SCHEDULED, "w2", 1)
    assert _row(claimed) == (ExecutionState.CLAIMED, "w1", 1)


def test_released_row_is_dispatched_again(cm):
    w1 = _worker("w1")
    lost = _schedule_to(cm, w1)
    cm.release_unclaimed(worker_name="w1")

    assignments = cm.next_executions_batch([w1], limit=10)

    assert [(ctx.execution_id, name) for ctx, name in assignments] == [(lost, "w1")]
    assert _row(lost) == (ExecutionState.SCHEDULED, "w1", 3)


def test_unclaimed_resume_returns_to_resuming(cm):
    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)
    _set_state(execution_id, ExecutionState.RESUME_SCHEDULED)

    assert cm.release_unclaimed(worker_name="w1") == [execution_id]
    assert _row(execution_id) == (ExecutionState.RESUMING, None, 2)


def test_unclaimed_capacity_slot_is_freed(cm):
    """The production symptom: lost assignments held max_concurrent slots,
    so the worker took no new work while the rows sat SCHEDULED."""
    w1 = _worker("w1")
    w1.max_concurrent_executions = 1
    lost = _schedule_to(cm, w1)
    waiting = _submit(cm)
    assert cm.next_executions_batch([w1], limit=10) == []

    cm.release_unclaimed(worker_name="w1")

    placed = {ctx.execution_id for ctx, _ in cm.next_executions_batch([w1], limit=10)}
    assert placed & {lost, waiting}
    assert len(placed) == 1


def test_deadline_releases_only_assignments_older_than_it(cm):
    w1 = _worker("w1")
    stale = _schedule_to(cm, w1)
    fresh = _schedule_to(cm, w1)
    _age_assignment(stale, 120)

    released = cm.release_unclaimed(
        scheduled_before=datetime.now(timezone.utc) - timedelta(seconds=60),
    )

    assert released == [stale]
    assert _row(stale)[0] == ExecutionState.CREATED
    assert _row(fresh) == (ExecutionState.SCHEDULED, "w1", 1)


def test_deadline_ignores_old_rows_already_claimed(cm):
    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)
    cm.claim(execution_id, w1)
    _age_assignment(execution_id, 3600)

    assert cm.release_unclaimed(scheduled_before=datetime.now(timezone.utc)) == []
    assert _row(execution_id)[0] == ExecutionState.CLAIMED


def test_deadline_measures_from_the_latest_assignment(cm):
    """A row released and re-dispatched gets a fresh clock."""
    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)
    _age_assignment(execution_id, 3600)
    cm.release_unclaimed(worker_name="w1")
    cm.next_executions_batch([w1], limit=1)  # re-dispatched just now

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
    assert cm.release_unclaimed(scheduled_before=cutoff) == []


def test_release_is_a_compare_and_set_against_a_racing_claim(cm):
    """The release reads its candidates, then updates them. A claim landing
    between the two must win: the update matches the row as it was read and
    so touches nothing once the claim moved it."""
    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)
    with cm.session() as session:
        seen = (
            session.query(
                ExecutionContextModel.execution_id,
                ExecutionContextModel.state,
                ExecutionContextModel.worker_name,
                ExecutionContextModel.claim_generation,
                ExecutionContextModel.required_worker,
            )
            .filter(ExecutionContextModel.execution_id == execution_id)
            .one()
        )

    cm.claim(execution_id, w1)  # lands between the read and the update

    with cm.session() as session:
        assert cm._release_assignment(session, seen) is False
        session.commit()
    assert _row(execution_id) == (ExecutionState.CLAIMED, "w1", 1)


def test_release_does_not_touch_a_row_redispatched_since_it_was_read(cm):
    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)
    with cm.session() as session:
        seen = (
            session.query(
                ExecutionContextModel.execution_id,
                ExecutionContextModel.state,
                ExecutionContextModel.worker_name,
                ExecutionContextModel.claim_generation,
                ExecutionContextModel.required_worker,
            )
            .filter(ExecutionContextModel.execution_id == execution_id)
            .one()
        )
    cm.unclaim(execution_id)
    cm.next_executions_batch([w1], limit=1)  # same worker, new generation

    with cm.session() as session:
        assert cm._release_assignment(session, seen) is False
        session.commit()
    assert _row(execution_id) == (ExecutionState.SCHEDULED, "w1", 3)


# -- claim fencing ------------------------------------------------------------


def test_fenced_claim_of_the_current_assignment_succeeds(cm):
    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)

    ctx = cm.claim(execution_id, w1, expected_generation=1)

    assert ctx.state == ExecutionState.CLAIMED
    assert _row(execution_id) == (ExecutionState.CLAIMED, "w1", 1)


def test_unfenced_claim_is_unchanged(cm):
    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)

    assert cm.claim(execution_id, w1).state == ExecutionState.CLAIMED


def test_lost_frame_cannot_claim_a_released_row(cm):
    """Without the fence the old frame's claim takes the CREATED row through
    the unfenced fallback, behind the dispatcher's back."""
    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)  # the lost frame carries generation 1
    cm.release_unclaimed(worker_name="w1")

    with pytest.raises(StaleClaimError):
        cm.claim(execution_id, w1, expected_generation=1)
    assert _row(execution_id) == (ExecutionState.CREATED, None, 2)


def test_only_the_current_frame_claims_after_redispatch_to_the_same_worker(cm):
    """Old and new frame both reach the worker: exactly one claims, and it is
    the one the row is assigned under now."""
    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)  # old frame: generation 1
    cm.release_unclaimed(worker_name="w1")
    cm.next_executions_batch([w1], limit=1)  # new frame: generation 3

    with pytest.raises(StaleClaimError):
        cm.claim(execution_id, w1, expected_generation=1)
    assert cm.claim(execution_id, w1, expected_generation=3).state == ExecutionState.CLAIMED
    with pytest.raises(StaleClaimError):
        cm.claim(execution_id, w1, expected_generation=3)


def test_lost_frame_cannot_claim_after_redispatch_to_another_worker(cm):
    w1, w2 = _worker("w1"), _worker("w2")
    execution_id = _schedule_to(cm, w1)
    cm.release_unclaimed(worker_name="w1")
    assignments = cm.next_executions_batch([w2], limit=1)
    assert assignments[0][1] == "w2"

    with pytest.raises(StaleClaimError):
        cm.claim(execution_id, w1, expected_generation=1)
    assert cm.claim(execution_id, w2, expected_generation=3).current_worker == "w2"


def test_fenced_resume_claim(cm):
    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)
    _set_state(execution_id, ExecutionState.RESUME_SCHEDULED)  # assigned at generation 1

    with pytest.raises(StaleClaimError):
        cm.claim_resume(execution_id, w1, expected_generation=0)
    assert _row(execution_id) == (ExecutionState.RESUME_SCHEDULED, "w1", 1)

    claimed = cm.claim_resume(execution_id, w1, expected_generation=1)
    assert claimed.state == ExecutionState.RESUME_CLAIMED


# -- server: dispatch frame, claim route, reconnect, deadline sweep ------------


def _endpoint(app, path: str, method: str):
    for route in app.routes:
        if isinstance(route, Route) and route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"no route {method} {path}")


@pytest.fixture
def server(cm):
    from flux.server import Server

    s = Server(host="localhost", port=8000)
    s._app = s._create_api()
    return s


def _identity(name: str) -> FluxIdentity:
    return FluxIdentity(subject=name, roles=frozenset({"worker"}))


def test_dispatch_frame_carries_the_assignment_generation(cm, server):
    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)

    payload = server._build_dispatch_payload(cm.get(execution_id))

    assert payload["claim_generation"] == 1


@pytest.mark.asyncio
async def test_claim_route_refuses_a_superseded_frame_with_409(cm, server):
    from fastapi import HTTPException

    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)
    cm.release_unclaimed(worker_name="w1")
    claim = _endpoint(server._app, "/workers/{name}/claim/{execution_id}", "POST")

    with pytest.raises(HTTPException) as refused:
        await claim(
            name="w1",
            execution_id=execution_id,
            response=Response(),
            claim_generation="1",
            identity=_identity("w1"),
        )

    assert refused.value.status_code == 409
    assert "stale-claim" in refused.value.detail
    assert _row(execution_id) == (ExecutionState.CREATED, None, 2)


@pytest.mark.asyncio
async def test_claim_route_accepts_the_current_frame(cm, server):
    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)
    claim = _endpoint(server._app, "/workers/{name}/claim/{execution_id}", "POST")
    response = Response()

    await claim(
        name="w1",
        execution_id=execution_id,
        response=response,
        claim_generation="1",
        identity=_identity("w1"),
    )

    assert response.headers["X-Flux-Claim-Generation"] == "1"
    assert _row(execution_id) == (ExecutionState.CLAIMED, "w1", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["poll", "event"])
async def test_reconnect_releases_the_workers_unclaimed_assignments(cm, server, mode):
    Configuration.get().override(dispatch={"mode": mode})
    w1, w2 = _worker("w1"), _worker("w2")
    lost = _schedule_to(cm, w1)
    other = _schedule_to(cm, w2)
    running = _schedule_to(cm, w1)
    cm.claim(running, w1)
    connect = _endpoint(server._app, "/workers/{name}/connect", "GET")

    response = await connect(name="w1", identity=_identity("w1"))

    assert _row(lost) == (ExecutionState.CREATED, None, 2)
    assert _row(other) == (ExecutionState.SCHEDULED, "w2", 1)
    assert _row(running) == (ExecutionState.CLAIMED, "w1", 1)
    # A stuck send closes the stream server-side instead of hanging on it.
    assert response.send_timeout == server.heartbeat_timeout
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_event_mode_reconnect_drops_the_old_queue_without_double_release(cm, server):
    """Frames still queued for the old stream belong to rows the reconnect
    releases; they must not also be unclaimed again later, which could undo
    a fresh re-dispatch."""
    from flux.dispatcher import DispatchFrame

    Configuration.get().override(dispatch={"mode": "event"})
    w1 = _worker("w1")
    queued = _schedule_to(cm, w1)
    old_queue = __import__("asyncio").Queue()
    old_queue.put_nowait(DispatchFrame("execution_scheduled", queued, {}))
    server._worker_queues["w1"] = old_queue
    connect = _endpoint(server._app, "/workers/{name}/connect", "GET")

    response = await connect(name="w1", identity=_identity("w1"))

    assert server._worker_queues["w1"] is not old_queue
    assert _row(queued) == (ExecutionState.CREATED, None, 2)
    await response.body_iterator.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["poll", "event"])
async def test_superseded_stream_stops_dispatching(cm, server, mode):
    """In poll mode a stream nobody reads would otherwise keep assigning
    work to the worker, each frame another lost assignment."""
    Configuration.get().override(dispatch={"mode": mode})
    _worker("w1")
    connect = _endpoint(server._app, "/workers/{name}/connect", "GET")
    first = await connect(name="w1", identity=_identity("w1"))
    second = await connect(name="w1", identity=_identity("w1"))
    _submit(cm)

    with pytest.raises(StopAsyncIteration):
        await first.body_iterator.__anext__()
    assert "w1" in server._worker_names  # the stale stream did not disconnect it
    await second.body_iterator.aclose()


def test_scheduler_sweep_releases_past_the_claim_deadline(cm):
    from flux.scheduler_loop import SchedulerLoop

    w1 = _worker("w1")
    stale = _schedule_to(cm, w1)
    fresh = _schedule_to(cm, w1)
    _age_assignment(stale, 120)
    woken = []
    loop = SchedulerLoop(
        create_execution=None,
        session_factory=None,
        signals=None,
        worker_queues={},
        hook_starter=None,
        hook_authorizer=None,
        poll_interval=1.0,
        notify_work=lambda: woken.append(True),
    )

    assert loop.release_unclaimed(datetime.now(timezone.utc)) == [stale]
    assert woken == [True]
    assert _row(fresh)[0] == ExecutionState.SCHEDULED


def test_scheduler_sweep_disabled_at_zero(cm):
    from flux.scheduler_loop import SchedulerLoop

    Configuration.get().override(workers={"claim_timeout": 0})
    w1 = _worker("w1")
    stale = _schedule_to(cm, w1)
    _age_assignment(stale, 3600)
    loop = SchedulerLoop(
        create_execution=None,
        session_factory=None,
        signals=None,
        worker_queues={},
        hook_starter=None,
        hook_authorizer=None,
        poll_interval=1.0,
    )

    assert loop.release_unclaimed(datetime.now(timezone.utc)) == []
    assert _row(stale)[0] == ExecutionState.SCHEDULED


@pytest.mark.asyncio
async def test_scheduler_tick_runs_the_claim_deadline_sweep(cm, server):
    """The sweep is wired into the tick and wakes dispatch through the server."""
    import asyncio
    from contextlib import contextmanager
    from unittest.mock import MagicMock, patch

    w1 = _worker("w1")
    stale = _schedule_to(cm, w1)
    _age_assignment(stale, 120)

    @contextmanager
    def lock_held():
        yield True

    schedules = MagicMock()
    schedules.dispatch_lock = lock_held
    schedules.get_due_schedules.return_value = []
    loop = server._scheduler()
    loop.running = True
    loop._poll_interval = 0
    sleeps = 0

    async def one_cycle(_delay):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise asyncio.CancelledError()

    with (
        patch("flux.scheduler_loop.create_schedule_manager", return_value=schedules),
        patch("asyncio.sleep", side_effect=one_cycle),
    ):
        await loop._loop()

    assert _row(stale) == (ExecutionState.CREATED, None, 2)
    assert server._work_available.is_set()


# -- the older releases must not undo a claim made after this one -------------


def test_a_late_drain_cannot_undo_a_claim_made_after_the_release(cm, server):
    """Multi-replica: the worker's frame sits queued on replica B when it
    reconnects to replica A. A releases the row, re-dispatches it, and the
    worker claims it. B's dead stream drains later and must not reset the
    claimed row, or a second worker runs it while the first still is."""
    from flux.dispatcher import DispatchFrame

    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)
    stale_queue = __import__("asyncio").Queue()
    stale_queue.put_nowait(DispatchFrame("execution_scheduled", execution_id, {}))
    server._worker_queues["w1"] = stale_queue  # replica B's queue

    cm.release_unclaimed(worker_name="w1")  # replica A's reconnect
    cm.next_executions_batch([w1], limit=1)
    cm.claim(execution_id, w1, expected_generation=3)

    server._drain_worker_queue("w1")  # B's stream finally dies

    assert _row(execution_id) == (ExecutionState.CLAIMED, "w1", 3)


@pytest.mark.asyncio
async def test_a_failed_delivery_cannot_undo_a_claim_made_after_the_release(cm, server):
    """The reconnect pops the queue, then awaits its release: a delivery that
    looks the queue up in that window finds none and releases its row. By
    then the row may have been released, re-dispatched and claimed."""
    from flux.dispatcher import Dispatcher

    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)
    ctx = cm.get(execution_id)
    payload = server._build_dispatch_payload(ctx)
    cm.release_unclaimed(worker_name="w1")
    cm.next_executions_batch([w1], limit=1)
    cm.claim(execution_id, w1, expected_generation=3)
    server._worker_queues.pop("w1", None)

    await Dispatcher(server)._deliver(cm, ctx, "w1", "execution_scheduled", payload=payload)

    assert _row(execution_id) == (ExecutionState.CLAIMED, "w1", 3)


def test_a_late_drain_releases_only_the_assignment_its_frame_was_built_for(cm, server):
    """Re-dispatched to the same worker: the stale frame's drain leaves the
    fresh assignment (and the frame on its way to the worker) alone."""
    from flux.dispatcher import DispatchFrame

    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)
    stale_queue = __import__("asyncio").Queue()
    stale_queue.put_nowait(DispatchFrame("execution_scheduled", execution_id, {}, generation=1))
    server._worker_queues["w1"] = stale_queue
    cm.release_unclaimed(worker_name="w1")
    cm.next_executions_batch([w1], limit=1)

    server._drain_worker_queue("w1")

    assert _row(execution_id) == (ExecutionState.SCHEDULED, "w1", 3)


def test_an_undelivered_frame_still_releases_its_own_assignment(cm, server):
    from flux.dispatcher import DispatchFrame

    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)
    queue = __import__("asyncio").Queue()
    queue.put_nowait(DispatchFrame("execution_scheduled", execution_id, {}, generation=1))
    server._worker_queues["w1"] = queue

    server._drain_worker_queue("w1")

    assert _row(execution_id) == (ExecutionState.CREATED, None, 2)


def test_an_unfenced_claim_cannot_take_a_row_another_worker_claimed(cm):
    """A pre-fencing worker's late frame: the claim route read the row while
    it was released (CREATED), then the dispatcher re-assigned it and w2
    claimed it before w1's claim reached the database. The unfenced fallback
    must not hand w1 a row w2 is already running."""
    from flux.errors import ExecutionError

    w1, w2 = _worker("w1"), _worker("w2")
    execution_id = _schedule_to(cm, w1)
    cm.release_unclaimed(worker_name="w1")
    cm.next_executions_batch([w2], limit=1)
    cm.claim(execution_id, w2, expected_generation=3)

    with pytest.raises(ExecutionError):
        cm.claim(execution_id, w1)
    assert _row(execution_id) == (ExecutionState.CLAIMED, "w2", 3)


def test_an_unfenced_claim_still_takes_a_released_row(cm):
    """The pre-existing fallback a pre-fencing worker relies on stays."""
    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)
    cm.release_unclaimed(worker_name="w1")

    assert cm.claim(execution_id, w1).state == ExecutionState.CLAIMED


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["poll", "event"])
async def test_a_send_timeout_closes_the_stream_without_an_error(cm, server, mode):
    """The timeout is how a stuck stream is meant to end. Raised out of the
    response, uvicorn logs every one as 'Exception in ASGI application'."""
    import asyncio

    Configuration.get().override(dispatch={"mode": mode})
    _worker("w1")
    server.heartbeat_timeout = 0.1
    server.heartbeat_interval = 0  # the first loop turn yields a ping
    connect = _endpoint(server._app, "/workers/{name}/connect", "GET")
    response = await connect(name="w1", identity=_identity("w1"))

    async def stuck_send(message):
        if message["type"] == "http.response.body":
            await asyncio.sleep(3600)

    async def receive():
        await asyncio.sleep(3600)

    await asyncio.wait_for(response({"type": "http"}, receive, stuck_send), 5)

    assert "w1" not in server._worker_names  # the live stream disconnected it


@pytest.mark.asyncio
async def test_a_delivered_frame_records_the_generation_it_carries(cm, server):
    """What lets a later drain release exactly this assignment."""
    import asyncio

    from flux.dispatcher import Dispatcher

    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)
    server._worker_queues["w1"] = asyncio.Queue()

    await Dispatcher(server)._deliver(cm, cm.get(execution_id), "w1", "execution_scheduled")

    assert server._worker_queues["w1"].get_nowait().generation == 1
