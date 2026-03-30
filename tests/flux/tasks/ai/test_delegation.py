from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from flux.tasks.ai.delegation import (
    AgentNotFoundError,
    AgentValidationError,
    DelegationResult,
    WorkflowAgentResult,
    _validate_agent,
    _validate_agent_name,
    build_agents_preamble,
    build_delegate,
    workflow_agent,
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
        r = WorkflowAgentResult(status="completed", output="done", execution_id="exec-1")
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


class _FakeAgent:
    def __init__(self, name, description):
        self.name = name
        self.description = description

    async def __call__(self, instruction, **kwargs):
        return f"result from {self.name}"


class TestBuildAgentsPreamble:
    def test_contains_agent_names(self):
        agents = [
            _FakeAgent("researcher", "Deep research."),
            _FakeAgent("reviewer", "Code review."),
        ]
        preamble = build_agents_preamble(agents)
        assert "researcher: Deep research." in preamble
        assert "reviewer: Code review." in preamble

    def test_contains_delegate_instructions(self):
        agents = [_FakeAgent("researcher", "Research.")]
        preamble = build_agents_preamble(agents)
        assert "delegate" in preamble
        assert "completed" in preamble
        assert "paused" in preamble
        assert "failed" in preamble


class TestBuildDelegate:
    def test_rejects_duplicate_names(self):
        agents = [
            _FakeAgent("researcher", "Research."),
            _FakeAgent("researcher", "Also research."),
        ]
        with pytest.raises(AgentValidationError, match="Duplicate"):
            build_delegate(agents)

    def test_rejects_invalid_agent(self):
        with pytest.raises(AgentValidationError):
            build_delegate(["not an agent"])

    def test_returns_task(self):
        agents = [_FakeAgent("researcher", "Research.")]
        delegate = build_delegate(agents)
        assert callable(delegate)
        assert delegate.name == "delegate"

    def test_delegate_completed(self):
        from flux import ExecutionContext, workflow

        agents = [_FakeAgent("researcher", "Research.")]
        delegate_tool = build_delegate(agents)

        @workflow
        async def test_wf(ctx: ExecutionContext):
            return await delegate_tool("researcher", "Find info")

        ctx = test_wf.run()
        assert ctx.has_succeeded
        result = ctx.output
        assert result["agent"] == "researcher"
        assert result["status"] == "completed"
        assert result["output"] == "result from researcher"

    def test_delegate_unknown_agent(self):
        from flux import ExecutionContext, workflow

        agents = [_FakeAgent("researcher", "Research.")]
        delegate_tool = build_delegate(agents)

        @workflow
        async def test_wf(ctx: ExecutionContext):
            return await delegate_tool("nonexistent", "Find info")

        ctx = test_wf.run()
        assert ctx.has_succeeded
        result = ctx.output
        assert result["status"] == "failed"
        assert "not found" in result["output"]
        assert "researcher" in result["output"]

    def test_delegate_handles_exception(self):
        from flux import ExecutionContext, workflow

        class FailingAgent:
            name = "broken"
            description = "Always fails."

            async def __call__(self, instruction, **kwargs):
                raise RuntimeError("boom")

        delegate_tool = build_delegate([FailingAgent()])

        @workflow
        async def test_wf(ctx: ExecutionContext):
            return await delegate_tool("broken", "Do something")

        ctx = test_wf.run()
        assert ctx.has_succeeded
        result = ctx.output
        assert result["status"] == "failed"
        assert "boom" in result["output"]

    def test_delegate_passes_execution_id(self):
        from flux import ExecutionContext, workflow

        class TrackingAgent:
            name = "tracker"
            description = "Tracks kwargs."

            async def __call__(self, instruction, **kwargs):
                return f"got execution_id={kwargs.get('execution_id')}"

        delegate_tool = build_delegate([TrackingAgent()])

        @workflow
        async def test_wf(ctx: ExecutionContext):
            return await delegate_tool("tracker", "Do it", execution_id="abc-123")

        ctx = test_wf.run()
        assert ctx.has_succeeded
        assert "abc-123" in ctx.output["output"]

    def test_delegate_passes_input(self):
        from flux import ExecutionContext, workflow

        class InputAgent:
            name = "reader"
            description = "Reads input."

            async def __call__(self, instruction, **kwargs):
                return f"got context={kwargs.get('context')}"

        delegate_tool = build_delegate([InputAgent()])

        @workflow
        async def test_wf(ctx: ExecutionContext):
            return await delegate_tool("reader", "Read this", input='{"key": "val"}')

        ctx = test_wf.run()
        assert ctx.has_succeeded
        assert "val" in ctx.output["output"]

    def test_delegate_appends_expected_output(self):
        from flux import ExecutionContext, workflow

        class EchoAgent:
            name = "echo"
            description = "Echoes."

            async def __call__(self, instruction, **kwargs):
                return instruction

        delegate_tool = build_delegate([EchoAgent()])

        @workflow
        async def test_wf(ctx: ExecutionContext):
            return await delegate_tool("echo", "Do stuff", expected_output="JSON list")

        ctx = test_wf.run()
        assert ctx.has_succeeded
        assert "Expected output format: JSON list" in ctx.output["output"]

    def test_delegate_handles_workflow_agent_result(self):
        from flux import ExecutionContext, workflow

        class WorkflowLikeAgent:
            name = "deployer"
            description = "Deploys."

            async def __call__(self, instruction, **kwargs):
                return WorkflowAgentResult(
                    status="paused",
                    output="need approval",
                    execution_id="exec-1",
                )

        delegate_tool = build_delegate([WorkflowLikeAgent()])

        @workflow
        async def test_wf(ctx: ExecutionContext):
            return await delegate_tool("deployer", "Deploy v2")

        ctx = test_wf.run()
        assert ctx.has_succeeded
        result = ctx.output
        assert result["status"] == "paused"
        assert result["output"] == "need approval"
        assert result["execution_id"] == "exec-1"

    def test_delegate_handles_pause_requested(self):
        from flux import ExecutionContext, workflow
        from flux.errors import PauseRequested

        class PausingAgent:
            name = "pauser"
            description = "Pauses."

            async def __call__(self, instruction, **kwargs):
                raise PauseRequested(name="need_info", output="I need more context")

        delegate_tool = build_delegate([PausingAgent()])

        @workflow
        async def test_wf(ctx: ExecutionContext):
            return await delegate_tool("pauser", "Review this")

        ctx = test_wf.run()
        assert ctx.has_succeeded
        result = ctx.output
        assert result["status"] == "paused"
        assert result["output"] == "I need more context"
        assert "execution_id" not in result


class TestWorkflowAgent:
    def test_returns_callable_with_name_and_description(self):
        wa = workflow_agent(
            name="deployer",
            description="Deploys things.",
            workflow="deploy_pipeline",
        )
        assert callable(wa)
        assert wa.name == "deployer"
        assert wa.description == "Deploys things."

    def test_validates_name(self):
        with pytest.raises(AgentValidationError):
            workflow_agent(name="Bad Name", description="Bad.", workflow="wf")

    @staticmethod
    def _mock_client(**methods):
        client = MagicMock()
        for name, return_value in methods.items():
            setattr(client, name, AsyncMock(return_value=return_value))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        return client

    def test_run_workflow_completed(self):
        from flux import ExecutionContext, workflow

        wa = workflow_agent(name="deployer", description="Deploys.", workflow="deploy_pipeline")

        mock_client = self._mock_client(
            run_workflow_sync={
                "execution_id": "exec-1",
                "state": "COMPLETED",
                "output": "deployed",
            },
        )

        @workflow
        async def test_wf(ctx: ExecutionContext):
            with patch("flux.tasks.ai.delegation._get_client", return_value=mock_client):
                return await wa("Deploy v2", context='{"version": "2.0"}')

        ctx = test_wf.run()
        assert ctx.has_succeeded
        result = ctx.output
        assert isinstance(result, WorkflowAgentResult)
        assert result.status == "completed"
        assert result.output == "deployed"
        assert result.execution_id == "exec-1"
        mock_client.run_workflow_sync.assert_called_once_with(
            "deploy_pipeline",
            {"instruction": "Deploy v2", "input": {"version": "2.0"}},
        )

    def test_run_workflow_paused(self):
        from flux import ExecutionContext, workflow

        wa = workflow_agent(name="deployer", description="Deploys.", workflow="deploy_pipeline")

        mock_client = self._mock_client(
            run_workflow_sync={
                "execution_id": "exec-1",
                "state": "PAUSED",
                "output": "need approval",
            },
        )

        @workflow
        async def test_wf(ctx: ExecutionContext):
            with patch("flux.tasks.ai.delegation._get_client", return_value=mock_client):
                return await wa("Deploy v2")

        ctx = test_wf.run()
        assert ctx.has_succeeded
        result = ctx.output
        assert result.status == "paused"
        assert result.output == "need approval"
        assert result.execution_id == "exec-1"

    def test_resume_workflow(self):
        from flux import ExecutionContext, workflow

        wa = workflow_agent(name="deployer", description="Deploys.", workflow="deploy_pipeline")

        mock_client = self._mock_client(
            resume_execution_sync={
                "execution_id": "exec-1",
                "state": "COMPLETED",
                "output": "done",
            },
        )

        @workflow
        async def test_wf(ctx: ExecutionContext):
            with patch("flux.tasks.ai.delegation._get_client", return_value=mock_client):
                return await wa("Approved", context='{"approved": true}', execution_id="exec-1")

        ctx = test_wf.run()
        assert ctx.has_succeeded
        mock_client.resume_execution_sync.assert_called_once_with(
            "deploy_pipeline",
            "exec-1",
            {"instruction": "Approved", "input": {"approved": True}},
        )

    def test_unexpected_state_maps_to_failed(self):
        from flux import ExecutionContext, workflow

        wa = workflow_agent(name="deployer", description="Deploys.", workflow="wf")

        mock_client = self._mock_client(
            run_workflow_sync={
                "execution_id": "exec-1",
                "state": "RUNNING",
                "output": None,
            },
        )

        @workflow
        async def test_wf(ctx: ExecutionContext):
            with patch("flux.tasks.ai.delegation._get_client", return_value=mock_client):
                return await wa("Deploy")

        ctx = test_wf.run()
        assert ctx.has_succeeded
        assert ctx.output.status == "failed"


class TestDelegationIntegration:
    async def test_agent_with_sub_agents_creates_delegate_tool(self):
        from flux.tasks.ai import agent

        sub = _FakeAgent("researcher", "Research.")
        parent = await agent(
            "You are a manager.",
            model="ollama/llama3",
            agents=[sub],
        )
        assert parent is not None

    async def test_agent_with_agents_and_skills(self):
        from flux.tasks.ai import agent
        from flux.tasks.ai.skills import Skill, SkillCatalog

        sub = _FakeAgent("researcher", "Research.")
        s = Skill(name="summarizer", description="Summarizes.", instructions="Summarize.")
        catalog = SkillCatalog([s])

        parent = await agent(
            "You are a manager.",
            model="ollama/llama3",
            agents=[sub],
            skills=catalog,
        )
        assert parent is not None

    async def test_recursive_sub_agents(self):
        from flux.tasks.ai import agent

        inner = _FakeAgent("researcher", "Research.")
        middle = await agent(
            "You are an analyst.",
            model="ollama/llama3",
            name="analyst",
            description="Analyzes.",
            agents=[inner],
        )
        outer = await agent(
            "You are a manager.",
            model="ollama/llama3",
            agents=[middle],
        )
        assert outer is not None
        assert middle.description == "Analyzes."

    def test_delegate_with_workflow_agent_result_flow(self):
        from flux import ExecutionContext, workflow

        class MockWorkflowAgent:
            name = "deployer"
            description = "Deploys."
            call_count = 0

            async def __call__(self, instruction, **kwargs):
                self.call_count += 1
                eid = kwargs.get("execution_id")
                if eid is None:
                    return WorkflowAgentResult(
                        status="paused",
                        output="need approval",
                        execution_id="exec-1",
                    )
                else:
                    return WorkflowAgentResult(
                        status="completed",
                        output="deployed",
                        execution_id=eid,
                    )

        wf_agent = MockWorkflowAgent()
        delegate_tool = build_delegate([wf_agent])

        @workflow
        async def test_wf(ctx: ExecutionContext):
            r1 = await delegate_tool("deployer", "Deploy v2")
            r2 = await delegate_tool("deployer", "Approved", execution_id="exec-1")
            return {"r1": r1, "r2": r2}

        ctx = test_wf.run()
        assert ctx.has_succeeded
        assert ctx.output["r1"]["status"] == "paused"
        assert ctx.output["r1"]["execution_id"] == "exec-1"
        assert ctx.output["r2"]["status"] == "completed"
        assert ctx.output["r2"]["output"] == "deployed"
        assert wf_agent.call_count == 2
