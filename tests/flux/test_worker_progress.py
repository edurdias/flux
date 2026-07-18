import asyncio

from flux.domain.execution_context import ExecutionContext
from flux.worker import Worker


def test_worker_has_progress_queue_dicts():
    worker = Worker(name="test-worker", server_url="http://localhost:8000")
    assert hasattr(worker, "_progress_queues")
    assert hasattr(worker, "_progress_flushers")
    assert isinstance(worker._progress_queues, dict)
    assert isinstance(worker._progress_flushers, dict)


def test_setup_progress_creates_queue_and_callback():
    worker = Worker(name="test-worker", server_url="http://localhost:8000")
    ctx = ExecutionContext(
        workflow_id="wf1",
        workflow_namespace="default",
        workflow_name="test",
        execution_id="exec_1",
    )
    worker._setup_progress(ctx)

    assert "exec_1" in worker._progress_queues
    assert "exec_1" in worker._progress_flushers
    assert worker._progress_queues["exec_1"].maxsize == 1000

    asyncio.run(worker._teardown_progress("exec_1"))


def test_progress_callback_enqueues_items():
    worker = Worker(name="test-worker", server_url="http://localhost:8000")
    ctx = ExecutionContext(
        workflow_id="wf1",
        workflow_namespace="default",
        workflow_name="test",
        execution_id="exec_1",
    )
    worker._setup_progress(ctx)

    ctx._progress_callback("exec_1", "task_1", "my_task", {"step": 1})

    queue = worker._progress_queues["exec_1"]
    assert not queue.empty()
    item = queue.get_nowait()
    assert item == {"task_id": "task_1", "task_name": "my_task", "value": {"step": 1}}

    asyncio.run(worker._teardown_progress("exec_1"))


def test_progress_backpressure_drops_silently():
    worker = Worker(name="test-worker", server_url="http://localhost:8000")
    ctx = ExecutionContext(
        workflow_id="wf1",
        workflow_namespace="default",
        workflow_name="test",
        execution_id="exec_1",
    )
    worker._setup_progress(ctx)

    queue = worker._progress_queues["exec_1"]
    for i in range(1000):
        ctx._progress_callback("exec_1", "task_1", "my_task", {"i": i})
    assert queue.full()

    ctx._progress_callback("exec_1", "task_1", "my_task", {"i": 1001})
    assert queue.qsize() == 1000

    asyncio.run(worker._teardown_progress("exec_1"))


def test_progress_survives_overlapping_claims_for_same_execution():
    """A paused workflow is claimed twice under one execution_id (initial run to
    the first pause, then resume). Their worker-side setup/teardown windows
    overlap: the resume claim calls ``_setup_progress`` before the run claim's
    ``_teardown_progress`` fires. The run claim's teardown must not clobber the
    resume claim's progress queue, or every progress event emitted during the
    resume (tool_start, tool_done, token) is silently dropped.

    Regression for the api-mode agent ``/chat`` path where ``tool_start`` events
    never reached the client because the run-phase teardown removed the queue.
    """
    worker = Worker(name="test-worker", server_url="http://localhost:8000")

    ctx_run = ExecutionContext(
        workflow_id="wf1",
        workflow_namespace="default",
        workflow_name="test",
        execution_id="exec_1",
    )
    ctx_resume = ExecutionContext(
        workflow_id="wf1",
        workflow_namespace="default",
        workflow_name="test",
        execution_id="exec_1",
    )

    worker._setup_progress(ctx_run)  # initial-run claim
    worker._setup_progress(ctx_resume)  # resume claim (same execution_id)
    resume_queue = worker._progress_queues["exec_1"]

    # The initial-run claim's teardown fires late, while the resume claim is
    # still the active progress channel.
    asyncio.run(worker._teardown_progress("exec_1"))

    # Resume emits a progress event; it must reach the still-active resume queue.
    worker._record_progress("exec_1", "task_1", "agent", {"type": "tool_start"})

    assert worker._progress_queues.get("exec_1") is resume_queue
    assert not resume_queue.empty(), "resume progress event was dropped"

    asyncio.run(worker._teardown_progress("exec_1"))


def test_teardown_progress_cleans_up():
    worker = Worker(name="test-worker", server_url="http://localhost:8000")
    ctx = ExecutionContext(
        workflow_id="wf1",
        workflow_namespace="default",
        workflow_name="test",
        execution_id="exec_1",
    )
    worker._setup_progress(ctx)

    assert "exec_1" in worker._progress_queues

    asyncio.run(worker._teardown_progress("exec_1"))

    assert "exec_1" not in worker._progress_queues
    assert "exec_1" not in worker._progress_flushers
