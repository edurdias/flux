"""
Tests for resume payload functionality in Flux workflows.

These tests verify that:
1. Workflows can be resumed with payload data
2. The payload data is correctly passed to the pause task
3. Different types of payloads are handled properly
4. Multiple pause points can receive different payloads
"""

from flux import workflow
from flux.domain import ExecutionContext
from flux.tasks import pause


@workflow
async def simple_payload_workflow(ctx: ExecutionContext):
    """A simple workflow that pauses and expects a payload on resume."""
    initial_value = "started"
    result = await pause("waiting_for_payload")
    return {"initial": initial_value, "payload_received": result}


@workflow
async def multi_pause_workflow(ctx: ExecutionContext):
    """A workflow with multiple pause points."""
    results = []

    # First pause
    first_payload = await pause("first_pause")
    results.append(first_payload)

    # Second pause
    second_payload = await pause("second_pause")
    results.append(second_payload)

    return {"payloads": results}


@workflow
async def conditional_workflow(ctx: ExecutionContext):
    """A workflow that uses payload to make decisions."""
    config = await pause("configuration")

    if isinstance(config, dict) and config.get("skip_second_step"):
        return {"status": "skipped", "config": config}

    additional_data = await pause("additional_input")
    return {"status": "completed", "config": config, "data": additional_data}


def test_basic_resume_payload():
    """Test basic resume payload functionality."""
    # Run workflow to pause point
    ctx = simple_payload_workflow.run()
    assert ctx.is_paused
    assert not ctx.has_finished

    # Resume with a string payload
    payload = "hello from resume"
    ctx_resumed = simple_payload_workflow.run(execution_id=ctx.execution_id, resume_payload=payload)

    assert ctx_resumed.has_succeeded
    assert ctx_resumed.output["initial"] == "started"
    assert ctx_resumed.output["payload_received"] == payload


def test_dict_resume_payload():
    """Test resume with dictionary payload."""
    # Run workflow to pause point
    ctx = simple_payload_workflow.run()
    assert ctx.is_paused

    # Resume with a dictionary payload
    payload = {
        "user_id": 123,
        "action": "approve",
        "data": [1, 2, 3],
        "metadata": {"timestamp": "2024-01-15", "version": "1.0"},
    }

    ctx_resumed = simple_payload_workflow.run(execution_id=ctx.execution_id, resume_payload=payload)

    assert ctx_resumed.has_succeeded
    assert ctx_resumed.output["payload_received"] == payload


def test_none_resume_payload():
    """Test resume with None payload (should fall back to pause name)."""
    # Run workflow to pause point
    ctx = simple_payload_workflow.run()
    assert ctx.is_paused

    # Resume without payload (None)
    ctx_resumed = simple_payload_workflow.run(execution_id=ctx.execution_id, resume_payload=None)

    assert ctx_resumed.has_succeeded
    # Should fall back to pause name when no payload is provided
    assert ctx_resumed.output["payload_received"] == "waiting_for_payload"


def test_multi_pause_different_payloads():
    """Test multiple pause points with different payloads."""
    # Run workflow to first pause
    ctx = multi_pause_workflow.run()
    assert ctx.is_paused

    # Resume with first payload
    first_payload = {"step": 1, "data": "first"}
    ctx = multi_pause_workflow.run(execution_id=ctx.execution_id, resume_payload=first_payload)
    assert ctx.is_paused  # Should be at second pause

    # Resume with second payload
    second_payload = {"step": 2, "data": "second"}
    ctx_final = multi_pause_workflow.run(
        execution_id=ctx.execution_id,
        resume_payload=second_payload,
    )

    assert ctx_final.has_succeeded
    expected_payloads = [first_payload, second_payload]
    assert ctx_final.output["payloads"] == expected_payloads


def test_conditional_workflow_with_skip():
    """Test conditional workflow that skips steps based on payload."""
    # Run to first pause
    ctx = conditional_workflow.run()
    assert ctx.is_paused

    # Resume with config that skips second step
    config_payload = {"skip_second_step": True, "reason": "testing"}
    ctx_final = conditional_workflow.run(
        execution_id=ctx.execution_id,
        resume_payload=config_payload,
    )

    assert ctx_final.has_succeeded
    assert ctx_final.output["status"] == "skipped"
    assert ctx_final.output["config"] == config_payload


def test_conditional_workflow_full_flow():
    """Test conditional workflow that completes all steps."""
    # Run to first pause
    ctx = conditional_workflow.run()
    assert ctx.is_paused

    # Resume with config that continues
    config_payload = {"skip_second_step": False, "mode": "full"}
    ctx = conditional_workflow.run(execution_id=ctx.execution_id, resume_payload=config_payload)
    assert ctx.is_paused  # Should be at second pause

    # Resume with additional data
    additional_payload = {"processed": True, "items": [1, 2, 3]}
    ctx_final = conditional_workflow.run(
        execution_id=ctx.execution_id,
        resume_payload=additional_payload,
    )

    assert ctx_final.has_succeeded
    assert ctx_final.output["status"] == "completed"
    assert ctx_final.output["config"] == config_payload
    assert ctx_final.output["data"] == additional_payload


def test_list_resume_payload():
    """Test resume with list payload."""
    # Run workflow to pause point
    ctx = simple_payload_workflow.run()
    assert ctx.is_paused

    # Resume with a list payload
    payload = ["item1", "item2", {"nested": "value"}]
    ctx_resumed = simple_payload_workflow.run(execution_id=ctx.execution_id, resume_payload=payload)

    assert ctx_resumed.has_succeeded
    assert ctx_resumed.output["payload_received"] == payload


def test_numeric_resume_payload():
    """Test resume with numeric payload."""
    # Run workflow to pause point
    ctx = simple_payload_workflow.run()
    assert ctx.is_paused

    # Resume with a numeric payload
    payload = 42.5
    ctx_resumed = simple_payload_workflow.run(execution_id=ctx.execution_id, resume_payload=payload)

    assert ctx_resumed.has_succeeded
    assert ctx_resumed.output["payload_received"] == payload


def test_resume_payload_properties():
    """Test that resume_payload property works correctly."""

    # Create a workflow that directly accesses the resume_payload property
    @workflow
    async def payload_property_workflow(ctx: ExecutionContext):
        # First pause
        await pause("test_pause")

        # Access the resume payload directly from context
        payload_from_property = ctx.resume_payload
        return {"direct_access": payload_from_property}

    # Run to pause
    ctx = payload_property_workflow.run()
    assert ctx.is_paused
    assert ctx.resume_payload is None  # No payload before resume

    # Resume with payload
    test_payload = {"test": "direct_access"}
    ctx_resumed = payload_property_workflow.run(
        execution_id=ctx.execution_id,
        resume_payload=test_payload,
    )

    assert ctx_resumed.has_succeeded
    assert ctx_resumed.output["direct_access"] == test_payload
    assert ctx_resumed.resume_payload == test_payload  # Property should return the payload


if __name__ == "__main__":
    # Run all tests
    test_functions = [
        test_basic_resume_payload,
        test_dict_resume_payload,
        test_none_resume_payload,
        test_multi_pause_different_payloads,
        test_conditional_workflow_with_skip,
        test_conditional_workflow_full_flow,
        test_list_resume_payload,
        test_numeric_resume_payload,
        test_resume_payload_properties,
    ]

    print("Running Resume Payload Tests")
    print("=" * 50)

    for test_func in test_functions:
        try:
            print(f"🧪 Running {test_func.__name__}...")
            test_func()
            print(f"✅ {test_func.__name__} passed")
        except Exception as e:
            print(f"❌ {test_func.__name__} failed: {e}")
        print()

    print("All tests completed!")
