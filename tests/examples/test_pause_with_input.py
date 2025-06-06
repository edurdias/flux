from __future__ import annotations


from examples.pause_with_input import (
    pause_with_input_workflow,
    multiple_input_pause_workflow,
    conditional_pause_workflow,
)
from flux.context_managers import ContextManager
from flux.domain.events import ExecutionEventType


class TestPauseWithInput:
    """Tests for pause/resume functionality with input."""

    def test_pause_with_simple_input(self):
        """Test pause/resume with simple input data."""
        # Start the workflow - it should pause at the first pause point
        ctx = pause_with_input_workflow.run()

        # Verify the workflow is paused
        assert ctx.is_paused, "Workflow should be paused"
        assert not ctx.has_finished, "Workflow should not be finished while paused"

        # Check the pause event
        pause_events = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
        assert len(pause_events) == 1
        assert pause_events[0].value == "waiting_for_user_input"

        # Resume with input using the correct pattern:
        # 1. Save the paused context
        # 2. Load it, add resuming input, and save again
        # 3. Then run the workflow
        user_input = {"multiplier": 5, "comment": "Test input"}

        resumed_ctx = pause_with_input_workflow.resume(ctx.execution_id, user_input)

        # Verify the workflow completed successfully
        assert resumed_ctx.has_finished, "Workflow should be finished after resume"
        assert resumed_ctx.has_succeeded, "Workflow should have succeeded"

        # Verify the output contains both initial data and user input
        output = resumed_ctx.output
        assert output["stage"] == "processed"
        assert output["initial_data"]["stage"] == "initial"
        assert output["user_input"] == user_input
        assert output["result"] == 6 + 5  # sum([1,2,3]) + multiplier

        return resumed_ctx

    def test_pause_with_complex_input(self):
        """Test pause/resume with complex nested input data."""
        # Start the workflow
        ctx = pause_with_input_workflow.run()
        assert ctx.is_paused, "Workflow should be paused"

        # Create complex input data
        complex_input = {
            "multiplier": 10,
            "metadata": {
                "user": "test_user",
                "timestamp": "2024-01-01T00:00:00Z",
                "approval_level": "high",
            },
            "options": {"validate": True, "backup": False, "notification": ["email", "slack"]},
        }

        resumed_ctx = pause_with_input_workflow.resume(ctx.execution_id, complex_input)

        # Verify the complex input was preserved
        assert resumed_ctx.has_succeeded, "Workflow should have succeeded"
        output = resumed_ctx.output
        assert output["user_input"] == complex_input
        assert output["result"] == 6 + 10  # sum([1,2,3]) + multiplier

        return resumed_ctx

    def test_pause_with_null_input(self):
        """Test pause/resume with null/None input."""
        # Start the workflow
        ctx = pause_with_input_workflow.run()
        assert ctx.is_paused, "Workflow should be paused"

        resumed_ctx = pause_with_input_workflow.resume(ctx.execution_id, None)

        # Verify the workflow handles None input gracefully
        assert resumed_ctx.has_succeeded, "Workflow should have succeeded"
        output = resumed_ctx.output
        assert output["user_input"] is None
        assert output["result"] == 6 + 1  # sum([1,2,3]) + default multiplier(1)

        return resumed_ctx

    def test_pause_with_empty_dict_input(self):
        """Test pause/resume with empty dictionary input."""
        # Start the workflow
        ctx = pause_with_input_workflow.run()
        assert ctx.is_paused, "Workflow should be paused"

        # Resume with empty dict input using correct pattern
        empty_input = {}
        resumed_ctx = pause_with_input_workflow.resume(ctx.execution_id, empty_input)

        # Verify the workflow handles empty input
        assert resumed_ctx.has_succeeded, "Workflow should have succeeded"
        output = resumed_ctx.output
        assert output["user_input"] == empty_input
        assert output["result"] == 6 + 1  # sum([1,2,3]) + default multiplier(1)

        return resumed_ctx

    def test_multiple_pause_points_with_different_inputs(self):
        """Test workflow with multiple pause points receiving different inputs."""
        # Start the workflow - should pause at first point
        ctx = multiple_input_pause_workflow.run()
        assert ctx.is_paused, "Workflow should be paused at first point"

        # Check first pause point
        pause_events = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
        assert len(pause_events) == 1
        assert pause_events[0].value == "collect_config"

        # Resume with config input using proper pattern
        config_input = {"database_url": "localhost:5432", "timeout": 30}
        ctx = multiple_input_pause_workflow.resume(ctx.execution_id, config_input)

        # Should be paused at second point
        assert ctx.is_paused, "Workflow should be paused at second point"
        pause_events = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
        unique_pause_points = {e.value for e in pause_events}
        assert "collect_parameters" in unique_pause_points
        assert len(unique_pause_points) == 2

        # Resume with parameters input using proper pattern
        param_input = {"batch_size": 100, "parallel_workers": 4}
        ctx = multiple_input_pause_workflow.resume(ctx.execution_id, param_input)

        # Should be paused at third point
        assert ctx.is_paused, "Workflow should be paused at third point"
        pause_events = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
        unique_pause_points = {e.value for e in pause_events}
        assert "final_approval" in unique_pause_points
        assert len(unique_pause_points) == 3

        # Resume with approval input using proper pattern
        approval_input = {"approved": True, "approver": "admin"}
        ctx = multiple_input_pause_workflow.resume(ctx.execution_id, approval_input)

        # Should be completed now
        assert ctx.has_finished, "Workflow should be finished"
        assert ctx.has_succeeded, "Workflow should have succeeded"

        # Verify all inputs were collected
        output = ctx.output
        assert output["stage"] == "completed"
        assert len(output["inputs"]) == 3

        # Check each input was preserved correctly
        inputs_by_type = {inp["type"]: inp["value"] for inp in output["inputs"]}
        assert inputs_by_type["config"] == config_input
        assert inputs_by_type["parameters"] == param_input
        assert inputs_by_type["approval"] == approval_input

        return ctx

    def test_conditional_pause_no_approval_needed(self):
        """Test conditional pause when no approval is required."""
        ctx = conditional_pause_workflow.run({"requires_approval": False})

        # Should complete without pausing
        assert ctx.has_finished, "Workflow should be finished"
        assert ctx.has_succeeded, "Workflow should have succeeded"
        assert not ctx.is_paused, "Workflow should not be paused"

        # Check output
        output = ctx.output
        assert output["approved"] is True
        assert output["message"] == "No approval required"

        return ctx

    def test_conditional_pause_with_approval_needed(self):
        """Test conditional pause when approval is required."""
        ctx = conditional_pause_workflow.run({"requires_approval": True})

        # Should be paused waiting for approval
        assert ctx.is_paused, "Workflow should be paused"
        assert not ctx.has_finished, "Workflow should not be finished"

        # Check pause point
        pause_events = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
        assert len(pause_events) == 1
        assert pause_events[0].value == "approval_required"

        # Resume with approval using proper pattern
        approval_input = {"approved": True, "reason": "Looks good"}
        resumed_ctx = conditional_pause_workflow.resume(ctx.execution_id, approval_input)

        # Should be completed
        assert resumed_ctx.has_finished, "Workflow should be finished"
        assert resumed_ctx.has_succeeded, "Workflow should have succeeded"

        # Check output
        output = resumed_ctx.output
        assert output["approved"] is True
        assert output["message"] == "Approval processed"

        return resumed_ctx

    def test_conditional_pause_with_rejection(self):
        """Test conditional pause when approval is rejected."""
        ctx = conditional_pause_workflow.run({"requires_approval": True})
        assert ctx.is_paused, "Workflow should be paused"

        # Resume with rejection using proper pattern
        rejection_input = {"approved": False, "reason": "Needs more work"}
        resumed_ctx = conditional_pause_workflow.resume(ctx.execution_id, rejection_input)

        # Should be completed but marked as not approved
        assert resumed_ctx.has_finished, "Workflow should be finished"
        assert resumed_ctx.has_succeeded, "Workflow should have succeeded"

        # Check output
        output = resumed_ctx.output
        assert output["approved"] is False
        assert output["message"] == "Approval processed"

        return resumed_ctx

    def test_pause_resume_events_with_input(self):
        """Test that pause/resume events are properly recorded with input."""
        # Start workflow
        ctx = pause_with_input_workflow.run()
        assert ctx.is_paused, "Workflow should be paused"

        # Resume with input
        user_input = {"test": "data"}
        resumed_ctx = pause_with_input_workflow.resume(ctx.execution_id, user_input)

        # Check for resume-related events
        resume_events = [
            e
            for e in resumed_ctx.events
            if e.type
            in (
                ExecutionEventType.WORKFLOW_RESUMING,
                ExecutionEventType.WORKFLOW_RESUMED,
                ExecutionEventType.TASK_RESUMED,
            )
        ]

        # Should have resuming, resumed, and task resumed events
        assert len(resume_events) >= 3, "Should have resume-related events"

        # Check resuming event contains input
        resuming_events = [
            e for e in resume_events if e.type == ExecutionEventType.WORKFLOW_RESUMING
        ]
        assert len(resuming_events) == 1
        assert resuming_events[0].value == user_input

        # Check task resumed event
        task_resumed_events = [
            e for e in resume_events if e.type == ExecutionEventType.TASK_RESUMED
        ]
        assert len(task_resumed_events) == 1
        task_resumed_event = task_resumed_events[0]
        assert task_resumed_event.value["name"] == "waiting_for_user_input"
        assert task_resumed_event.value["input"] == user_input

        return resumed_ctx

    def test_workflow_skip_if_already_finished_with_input(self):
        """Test that finished workflows are skipped even when input was used."""
        # Complete a workflow with input
        finished_ctx = self.test_pause_with_simple_input()

        # Try to run it again with the same execution_id
        repeat_ctx = pause_with_input_workflow.run(execution_id=finished_ctx.execution_id)

        # Should have same execution_id and output
        assert finished_ctx.execution_id == repeat_ctx.execution_id
        assert finished_ctx.output == repeat_ctx.output
        assert repeat_ctx.has_finished, "Repeated workflow should still be finished"

        return repeat_ctx

    def test_input_preservation_across_context_manager_operations(self):
        """Test that input is preserved when context is saved and loaded."""
        # Start workflow and pause
        ctx = pause_with_input_workflow.run()
        assert ctx.is_paused, "Workflow should be paused"

        # Save context to context manager
        cm = ContextManager.create()
        saved_ctx = cm.save(ctx)

        # Load context and add input
        loaded_ctx = cm.get(saved_ctx.execution_id)
        user_input = {"preserved": True, "data": "test"}
        loaded_ctx.start_resuming(user_input)

        # Save again after adding input
        updated_ctx = cm.save(loaded_ctx)

        # Load again and resume
        final_ctx = cm.get(updated_ctx.execution_id)
        resumed_ctx = pause_with_input_workflow.run(execution_id=final_ctx.execution_id)

        # Verify input was preserved through all operations
        assert resumed_ctx.has_succeeded, "Workflow should have succeeded"
        output = resumed_ctx.output
        assert output["user_input"] == user_input

        return resumed_ctx

    def test_large_input_data(self):
        """Test pause/resume with large input data to ensure no data corruption."""
        # Start workflow
        ctx = pause_with_input_workflow.run()
        assert ctx.is_paused, "Workflow should be paused"

        # Create large input data
        large_input = {
            "data": list(range(1000)),  # Large list
            "metadata": {f"key_{i}": f"value_{i}" for i in range(100)},  # Large dict
            "nested": {"level1": {"level2": {"level3": ["deep"] * 50}}},
        }

        # Resume with large input using correct pattern
        resumed_ctx = pause_with_input_workflow.resume(ctx.execution_id, large_input)

        # Verify large input was preserved correctly
        assert resumed_ctx.has_succeeded, "Workflow should have succeeded"
        output = resumed_ctx.output
        assert output["user_input"] == large_input

        # Verify nested data integrity
        assert len(output["user_input"]["data"]) == 1000
        assert output["user_input"]["data"][999] == 999
        assert len(output["user_input"]["metadata"]) == 100
        assert output["user_input"]["nested"]["level1"]["level2"]["level3"] == ["deep"] * 50

        return resumed_ctx
