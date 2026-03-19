from flux.domain.events import ExecutionEventType, ExecutionEvent


def test_task_progress_event_type_exists():
    assert ExecutionEventType.TASK_PROGRESS == "TASK_PROGRESS"


def test_task_progress_event_creation():
    event = ExecutionEvent(
        type=ExecutionEventType.TASK_PROGRESS,
        source_id="task_123",
        name="my_task",
        value={"token": "hello"},
    )
    assert event.type == ExecutionEventType.TASK_PROGRESS
    assert event.source_id == "task_123"
    assert event.name == "my_task"
    assert event.value == {"token": "hello"}
    assert event.time is not None
    assert event.id is not None
