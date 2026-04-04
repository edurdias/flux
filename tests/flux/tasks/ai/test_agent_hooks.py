from __future__ import annotations

import pytest

from flux.domain.execution_context import ExecutionContext
from flux.errors import PauseRequested
from flux.tasks.ai.models import LLMResponse, ToolCall
from flux.task import task


class FakeFormatter:
    def build_messages(self, sys, user, wm):
        return [{"role": "user", "content": user}], {}

    def format_assistant_message(self, r):
        return {"role": "assistant", "content": r.text}

    def format_tool_results(self, tc, results):
        return []

    def format_user_message(self, text):
        return {"role": "user", "content": text}

    def remove_tools_from_kwargs(self, kw):
        return kw

    async def stream(self, messages, kwargs):
        yield "hello"


@task
async def fake_llm(messages, **kwargs):
    return LLMResponse(text="response text")


class TestAgentHooks:
    @pytest.mark.asyncio
    async def test_on_complete_receives_agent_id_and_value(self):
        captured = []

        async def hook(agent_id: str, value):
            captured.append({"agent_id": agent_id, "value": value})

        from flux.tasks.ai.agent_loop import run_agent_loop

        ctx = ExecutionContext(workflow_id="test", workflow_name="test")
        token = ExecutionContext.set(ctx)
        try:
            result = await run_agent_loop(
                llm_task=fake_llm,
                formatter=FakeFormatter(),
                system_prompt="test",
                instruction="test",
                stream=False,
                on_complete=[hook],
                agent_name="test_agent",
            )
            assert result == "response text"
            assert len(captured) == 1
            assert captured[0]["agent_id"] == "test_agent"
            assert captured[0]["value"] == "response text"
        finally:
            ExecutionContext.reset(token)

    @pytest.mark.asyncio
    async def test_on_complete_failure_does_not_affect_result(self):
        async def failing_hook(agent_id: str, value):
            raise RuntimeError("hook failed")

        from flux.tasks.ai.agent_loop import run_agent_loop

        ctx = ExecutionContext(workflow_id="test", workflow_name="test")
        token = ExecutionContext.set(ctx)
        try:
            result = await run_agent_loop(
                llm_task=fake_llm,
                formatter=FakeFormatter(),
                system_prompt="test",
                instruction="test",
                stream=False,
                on_complete=[failing_hook],
                agent_name="test_agent",
            )
            assert result == "response text"
        finally:
            ExecutionContext.reset(token)

    @pytest.mark.asyncio
    async def test_multiple_hooks_all_called(self):
        calls = []

        async def hook_a(agent_id: str, value):
            calls.append("a")

        async def hook_b(agent_id: str, value):
            calls.append("b")

        from flux.tasks.ai.agent_loop import run_agent_loop

        ctx = ExecutionContext(workflow_id="test", workflow_name="test")
        token = ExecutionContext.set(ctx)
        try:
            await run_agent_loop(
                llm_task=fake_llm,
                formatter=FakeFormatter(),
                system_prompt="test",
                instruction="test",
                stream=False,
                on_complete=[hook_a, hook_b],
                agent_name="test_agent",
            )
            assert calls == ["a", "b"]
        finally:
            ExecutionContext.reset(token)

    @pytest.mark.asyncio
    async def test_no_hooks_works_fine(self):
        from flux.tasks.ai.agent_loop import run_agent_loop

        ctx = ExecutionContext(workflow_id="test", workflow_name="test")
        token = ExecutionContext.set(ctx)
        try:
            result = await run_agent_loop(
                llm_task=fake_llm,
                formatter=FakeFormatter(),
                system_prompt="test",
                instruction="test",
                stream=False,
            )
            assert result == "response text"
        finally:
            ExecutionContext.reset(token)

    @pytest.mark.asyncio
    async def test_on_pause_fires_on_pause_requested(self):
        captured = []

        async def pause_hook(agent_id: str, value):
            captured.append({"agent_id": agent_id, "value": value})

        @task
        async def pausing_tool(command: str) -> str:
            """A tool that pauses."""
            raise PauseRequested("approval needed")

        @task
        async def llm_with_tool_call(messages, **kwargs):
            return LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id="call_0", name="pausing_tool", arguments={"command": "test"}),
                ],
            )

        class ToolFormatter(FakeFormatter):
            def format_tool_results(self, tc, results):
                return [{"role": "tool", "content": str(r)} for r in results]

        from flux.tasks.ai.agent_loop import run_agent_loop
        from flux.tasks.ai.tool_executor import build_tool_schemas

        tools = [pausing_tool]
        schemas = build_tool_schemas(tools)

        ctx = ExecutionContext(workflow_id="test", workflow_name="test")
        token = ExecutionContext.set(ctx)
        try:
            with pytest.raises(PauseRequested):
                await run_agent_loop(
                    llm_task=llm_with_tool_call,
                    formatter=ToolFormatter(),
                    system_prompt="test",
                    instruction="test",
                    tools=tools,
                    tool_schemas=schemas,
                    stream=False,
                    on_pause=[pause_hook],
                    agent_name="test_agent",
                )
            assert len(captured) == 1
            assert captured[0]["agent_id"] == "test_agent"
            assert captured[0]["value"] is None
        finally:
            ExecutionContext.reset(token)
