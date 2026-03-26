from __future__ import annotations

import pytest

from flux.tasks.ai.delegation import (
    AgentNotFoundError,
    AgentValidationError,
    DelegationResult,
    WorkflowAgentResult,
    _validate_agent,
    _validate_agent_name,
)
from flux.errors import ExecutionError


class TestDelegationResult:
    def test_completed_to_dict(self):
        r = DelegationResult(agent="researcher", status="completed", output="done")
        d = r.to_dict()
        assert d == {"agent": "researcher", "status": "completed", "output": "done"}
        assert "execution_id" not in d

    def test_paused_with_execution_id(self):
        r = DelegationResult(
            agent="deployer",
            status="paused",
            output="need notes",
            execution_id="abc-123",
        )
        d = r.to_dict()
        assert d["execution_id"] == "abc-123"
        assert d["status"] == "paused"

    def test_failed_to_dict(self):
        r = DelegationResult(agent="reviewer", status="failed", output="timeout")
        d = r.to_dict()
        assert d["status"] == "failed"
        assert "execution_id" not in d


class TestWorkflowAgentResult:
    def test_construction(self):
        r = WorkflowAgentResult(
            status="completed", output="done", execution_id="exec-1"
        )
        assert r.status == "completed"
        assert r.output == "done"
        assert r.execution_id == "exec-1"


class TestValidateAgentName:
    def test_valid_names(self):
        for name in ["researcher", "code-reviewer", "agent1", "a", "a-b-c"]:
            _validate_agent_name(name)

    def test_empty_name(self):
        with pytest.raises(AgentValidationError, match="must not be empty"):
            _validate_agent_name("")

    def test_too_long(self):
        with pytest.raises(AgentValidationError, match="exceeds 64"):
            _validate_agent_name("a" * 65)

    def test_consecutive_hyphens(self):
        with pytest.raises(AgentValidationError, match="consecutive hyphens"):
            _validate_agent_name("bad--name")

    def test_uppercase(self):
        with pytest.raises(AgentValidationError, match="invalid"):
            _validate_agent_name("BadName")

    def test_starts_with_hyphen(self):
        with pytest.raises(AgentValidationError, match="invalid"):
            _validate_agent_name("-bad")

    def test_ends_with_hyphen(self):
        with pytest.raises(AgentValidationError, match="invalid"):
            _validate_agent_name("bad-")


class TestValidateAgent:
    def test_valid_agent(self):
        class FakeAgent:
            name = "researcher"
            description = "Researches things."

            def __call__(self):
                pass

        _validate_agent(FakeAgent())

    def test_not_callable(self):
        class NotCallable:
            name = "researcher"
            description = "Researches."

        with pytest.raises(AgentValidationError, match="callable"):
            _validate_agent(NotCallable())

    def test_missing_name(self):
        class NoName:
            description = "Researches."

            def __call__(self):
                pass

        with pytest.raises(AgentValidationError, match="name"):
            _validate_agent(NoName())

    def test_missing_description(self):
        class NoDesc:
            name = "researcher"

            def __call__(self):
                pass

        with pytest.raises(AgentValidationError, match="description"):
            _validate_agent(NoDesc())

    def test_empty_description(self):
        class EmptyDesc:
            name = "researcher"
            description = ""

            def __call__(self):
                pass

        with pytest.raises(AgentValidationError, match="description"):
            _validate_agent(EmptyDesc())


class TestErrorClasses:
    def test_agent_not_found_is_execution_error(self):
        err = AgentNotFoundError("Agent 'x' not found.")
        assert isinstance(err, ExecutionError)
        assert err.message == "Agent 'x' not found."

    def test_agent_validation_error_is_value_error(self):
        err = AgentValidationError("bad config")
        assert isinstance(err, ValueError)
