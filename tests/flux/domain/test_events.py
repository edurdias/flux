from flux.domain.events import ExecutionEventType


def test_approval_event_types_exist():
    assert ExecutionEventType.TASK_AWAITING_APPROVAL == "TASK_AWAITING_APPROVAL"
    assert ExecutionEventType.TASK_APPROVED == "TASK_APPROVED"
    assert ExecutionEventType.TASK_REJECTED == "TASK_REJECTED"


def test_approval_event_types_are_string_serializable():
    assert ExecutionEventType("TASK_AWAITING_APPROVAL") is ExecutionEventType.TASK_AWAITING_APPROVAL
    assert ExecutionEventType("TASK_APPROVED") is ExecutionEventType.TASK_APPROVED
    assert ExecutionEventType("TASK_REJECTED") is ExecutionEventType.TASK_REJECTED
