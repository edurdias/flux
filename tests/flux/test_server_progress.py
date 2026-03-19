import asyncio

from flux.domain.events import ExecutionEvent, ExecutionEventType
from flux.server import Server


def test_server_has_progress_buffers_dict():
    server = Server.__new__(Server)
    server._progress_buffers = {}
    assert isinstance(server._progress_buffers, dict)


def test_server_init_creates_progress_buffers():
    server = Server.__new__(Server)
    server.__init__("127.0.0.1", 8000)
    assert hasattr(server, "_progress_buffers")
    assert isinstance(server._progress_buffers, dict)
    assert len(server._progress_buffers) == 0


def test_progress_buffer_accepts_execution_events():
    buffer: asyncio.Queue = asyncio.Queue(maxsize=10000)
    event = ExecutionEvent(
        type=ExecutionEventType.TASK_PROGRESS,
        source_id="task-1",
        name="my_task",
        value={"chunk": "hello"},
    )
    buffer.put_nowait(event)
    assert buffer.qsize() == 1
    retrieved = buffer.get_nowait()
    assert retrieved.type == ExecutionEventType.TASK_PROGRESS
    assert retrieved.source_id == "task-1"
    assert retrieved.name == "my_task"
    assert retrieved.value == {"chunk": "hello"}
