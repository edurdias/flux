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
    ctx = ExecutionContext(workflow_id="wf1", workflow_name="test", execution_id="exec_1")
    worker._setup_progress(ctx)

    assert "exec_1" in worker._progress_queues
    assert "exec_1" in worker._progress_flushers
    assert worker._progress_queues["exec_1"].maxsize == 1000

    asyncio.run(worker._teardown_progress("exec_1"))


def test_progress_callback_enqueues_items():
    worker = Worker(name="test-worker", server_url="http://localhost:8000")
    ctx = ExecutionContext(workflow_id="wf1", workflow_name="test", execution_id="exec_1")
    worker._setup_progress(ctx)

    ctx._progress_callback("exec_1", "task_1", "my_task", {"step": 1})

    queue = worker._progress_queues["exec_1"]
    assert not queue.empty()
    item = queue.get_nowait()
    assert item == {"task_id": "task_1", "task_name": "my_task", "value": {"step": 1}}

    asyncio.run(worker._teardown_progress("exec_1"))


def test_progress_backpressure_drops_silently():
    worker = Worker(name="test-worker", server_url="http://localhost:8000")
    ctx = ExecutionContext(workflow_id="wf1", workflow_name="test", execution_id="exec_1")
    worker._setup_progress(ctx)

    queue = worker._progress_queues["exec_1"]
    for i in range(1000):
        ctx._progress_callback("exec_1", "task_1", "my_task", {"i": i})
    assert queue.full()

    ctx._progress_callback("exec_1", "task_1", "my_task", {"i": 1001})
    assert queue.qsize() == 1000

    asyncio.run(worker._teardown_progress("exec_1"))


def test_teardown_progress_cleans_up():
    worker = Worker(name="test-worker", server_url="http://localhost:8000")
    ctx = ExecutionContext(workflow_id="wf1", workflow_name="test", execution_id="exec_1")
    worker._setup_progress(ctx)

    assert "exec_1" in worker._progress_queues

    asyncio.run(worker._teardown_progress("exec_1"))

    assert "exec_1" not in worker._progress_queues
    assert "exec_1" not in worker._progress_flushers
