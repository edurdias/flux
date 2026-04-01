from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

from flux import ExecutionContext, workflow
from flux.task import task
from flux.tasks.ai.formatter import LLMFormatter
from flux.tasks.ai.models import LLMResponse, ToolCall


class MockFormatter(LLMFormatter):
    def build_messages(
        self,
        system_prompt: str,
        user_content: str,
        working_memory: Any | None = None,
    ) -> tuple[list[dict], dict]:
        messages: list[dict] = []
        if working_memory:
            messages.extend(working_memory.recall())
        messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_content})
        return messages, {}

    def format_assistant_message(self, response: LLMResponse) -> dict:
        msg: dict[str, Any] = {"role": "assistant", "content": response.text}
        if response.tool_calls:
            msg["tool_calls"] = [tc.model_dump() for tc in response.tool_calls]
        return msg

    def format_tool_results(
        self,
        tool_calls: list[ToolCall],
        results: list[dict],
    ) -> list[dict]:
        return [
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result["output"],
            }
            for tc, result in zip(tool_calls, results)
        ]

    def format_user_message(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def remove_tools_from_kwargs(self, call_kwargs: dict) -> dict:
        return {k: v for k, v in call_kwargs.items() if k != "tools"}

    async def stream(
        self,
        messages: list[dict],
        call_kwargs: dict,
    ) -> AsyncIterator[str]:
        for token in ["Hello", " ", "world"]:
            yield token


def _make_llm_task(responses: list[LLMResponse]) -> task:
    call_index = {"i": 0}

    @task
    async def mock_llm(messages: list[dict], **kwargs: Any) -> LLMResponse:
        idx = call_index["i"]
        call_index["i"] += 1
        if idx < len(responses):
            return responses[idx]
        return LLMResponse(text="fallback")

    return mock_llm


@task
async def search_web(query: str) -> str:
    """Search the web for results."""
    return f"Results for: {query}"


@task
async def calculator(expression: str) -> str:
    """Evaluate a math expression."""
    return f"Result: {expression} = 42"


class WeatherResponse(BaseModel):
    temperature: float
    city: str


def test_simple_text_response():
    from flux.tasks.ai.agent_loop import run_agent_loop

    llm_task = _make_llm_task([LLMResponse(text="Hello, I can help you!")])
    formatter = MockFormatter()

    @workflow
    async def test_wf(ctx: ExecutionContext):
        return await run_agent_loop(
            llm_task=llm_task,
            formatter=formatter,
            system_prompt="You are a helpful assistant.",
            instruction="Hi there",
        )

    ctx = test_wf.run()
    assert ctx.has_succeeded, ctx.output
    assert ctx.output == "Hello, I can help you!"


def test_tool_call_then_final_response():
    from flux.tasks.ai.agent_loop import run_agent_loop

    responses = [
        LLMResponse(
            text="",
            tool_calls=[
                ToolCall(id="call_1", name="search_web", arguments={"query": "flux"}),
            ],
        ),
        LLMResponse(text="Based on the search: Flux is great."),
    ]
    llm_task = _make_llm_task(responses)
    formatter = MockFormatter()

    @workflow
    async def test_wf(ctx: ExecutionContext):
        return await run_agent_loop(
            llm_task=llm_task,
            formatter=formatter,
            system_prompt="You are helpful.",
            instruction="Tell me about flux",
            tools=[search_web],
            tool_schemas=[{"name": "search_web", "parameters": {}}],
        )

    ctx = test_wf.run()
    assert ctx.has_succeeded, ctx.output
    assert "Flux is great" in ctx.output


def test_max_tool_calls_forces_final_answer():
    from flux.tasks.ai.agent_loop import run_agent_loop

    tool_response = LLMResponse(
        text="",
        tool_calls=[
            ToolCall(id="call_1", name="search_web", arguments={"query": "test"}),
        ],
    )
    final_response = LLMResponse(text="Final answer after forced stop.")
    responses = [tool_response, tool_response, final_response]
    llm_task = _make_llm_task(responses)
    formatter = MockFormatter()

    @workflow
    async def test_wf(ctx: ExecutionContext):
        return await run_agent_loop(
            llm_task=llm_task,
            formatter=formatter,
            system_prompt="You are helpful.",
            instruction="Search repeatedly",
            tools=[search_web],
            tool_schemas=[{"name": "search_web", "parameters": {}}],
            max_tool_calls=1,
        )

    ctx = test_wf.run()
    assert ctx.has_succeeded, ctx.output
    assert ctx.output == "Final answer after forced stop."


def test_streaming_no_tools():
    from flux.tasks.ai.agent_loop import run_agent_loop

    llm_task = _make_llm_task([])
    formatter = MockFormatter()

    @workflow
    async def test_wf(ctx: ExecutionContext):
        return await run_agent_loop(
            llm_task=llm_task,
            formatter=formatter,
            system_prompt="You are helpful.",
            instruction="Say hello",
            stream=True,
        )

    ctx = test_wf.run()
    assert ctx.has_succeeded, ctx.output
    assert ctx.output == "Hello world"


def test_working_memory_save():
    from flux.tasks.ai.agent_loop import run_agent_loop
    from flux.tasks.ai.memory.working_memory import WorkingMemory

    llm_task = _make_llm_task([LLMResponse(text="I remember things.")])
    formatter = MockFormatter()
    wm = WorkingMemory()

    @workflow
    async def test_wf(ctx: ExecutionContext):
        result = await run_agent_loop(
            llm_task=llm_task,
            formatter=formatter,
            system_prompt="You are helpful.",
            instruction="Remember this",
            working_memory=wm,
        )
        messages = wm.recall()
        return {"result": result, "memory_count": len(messages)}

    ctx = test_wf.run()
    assert ctx.has_succeeded, ctx.output
    assert ctx.output["result"] == "I remember things."
    assert ctx.output["memory_count"] == 2


def test_response_format_parsing():
    from flux.tasks.ai.agent_loop import run_agent_loop

    json_text = json.dumps({"temperature": 22.5, "city": "Paris"})
    llm_task = _make_llm_task([LLMResponse(text=json_text)])
    formatter = MockFormatter()

    @workflow
    async def test_wf(ctx: ExecutionContext):
        return await run_agent_loop(
            llm_task=llm_task,
            formatter=formatter,
            system_prompt="You are helpful.",
            instruction="What is the weather?",
            response_format=WeatherResponse,
        )

    ctx = test_wf.run()
    assert ctx.has_succeeded, ctx.output
    assert isinstance(ctx.output, WeatherResponse)
    assert ctx.output.temperature == 22.5
    assert ctx.output.city == "Paris"


def test_context_appended_to_instruction():
    from flux.tasks.ai.agent_loop import run_agent_loop

    captured_messages = {}

    @task
    async def capturing_llm(messages: list[dict], **kwargs: Any) -> LLMResponse:
        captured_messages["messages"] = messages
        return LLMResponse(text="Got it.")

    formatter = MockFormatter()

    @workflow
    async def test_wf(ctx: ExecutionContext):
        return await run_agent_loop(
            llm_task=capturing_llm,
            formatter=formatter,
            system_prompt="You are helpful.",
            instruction="Do something",
            context="Previous context here",
        )

    ctx = test_wf.run()
    assert ctx.has_succeeded, ctx.output
    user_msg = captured_messages["messages"][-1]
    assert "Previous context here" in user_msg["content"]
    assert "Do something" in user_msg["content"]


def test_dict_result_from_replay_handled():
    from flux.tasks.ai.agent_loop import run_agent_loop

    @task
    async def dict_llm(messages: list[dict], **kwargs: Any) -> dict:
        return {"text": "From dict", "tool_calls": []}

    formatter = MockFormatter()

    @workflow
    async def test_wf(ctx: ExecutionContext):
        return await run_agent_loop(
            llm_task=dict_llm,
            formatter=formatter,
            system_prompt="You are helpful.",
            instruction="Test dict handling",
        )

    ctx = test_wf.run()
    assert ctx.has_succeeded, ctx.output
    assert ctx.output == "From dict"


def test_plan_continuation_on_empty_response():
    from flux.tasks.ai.agent_loop import run_agent_loop

    responses = [
        LLMResponse(
            text="",
            tool_calls=[
                ToolCall(id="call_1", name="search_web", arguments={"query": "x"}),
            ],
        ),
        LLMResponse(text="", tool_calls=[]),
        LLMResponse(text="Continued answer."),
    ]
    llm_task = _make_llm_task(responses)
    formatter = MockFormatter()

    @workflow
    async def test_wf(ctx: ExecutionContext):
        return await run_agent_loop(
            llm_task=llm_task,
            formatter=formatter,
            system_prompt="You are helpful.",
            instruction="Do a plan",
            tools=[search_web],
            tool_schemas=[{"name": "search_web", "parameters": {}}],
            max_tool_calls=10,
            plan_summary_fn=lambda: "Step 2: finish up",
        )

    ctx = test_wf.run()
    assert ctx.has_succeeded, ctx.output
    assert ctx.output == "Continued answer."


def test_multiple_tool_iterations():
    from flux.tasks.ai.agent_loop import run_agent_loop

    responses = [
        LLMResponse(
            text="",
            tool_calls=[
                ToolCall(id="call_1", name="search_web", arguments={"query": "first"}),
            ],
        ),
        LLMResponse(
            text="",
            tool_calls=[
                ToolCall(id="call_2", name="calculator", arguments={"expression": "2+2"}),
            ],
        ),
        LLMResponse(text="Search found results and calculation is 42."),
    ]
    llm_task = _make_llm_task(responses)
    formatter = MockFormatter()

    @workflow
    async def test_wf(ctx: ExecutionContext):
        return await run_agent_loop(
            llm_task=llm_task,
            formatter=formatter,
            system_prompt="You are helpful.",
            instruction="Search and calculate",
            tools=[search_web, calculator],
            tool_schemas=[
                {"name": "search_web", "parameters": {}},
                {"name": "calculator", "parameters": {}},
            ],
            max_tool_calls=10,
        )

    ctx = test_wf.run()
    assert ctx.has_succeeded, ctx.output
    assert "42" in ctx.output
