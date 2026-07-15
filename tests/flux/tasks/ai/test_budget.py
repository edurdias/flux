"""Budget: usage accounting + the pre-flight spend ceiling."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from flux import ExecutionContext, workflow
from flux.errors import BudgetExceededError
from flux.task import task
from flux.tasks.ai.budget import Budget
from flux.tasks.ai.models import LLMResponse, ToolCall, Usage

from tests.flux.tasks.ai.test_agent_loop import MockFormatter, _make_llm_task


@task
async def get_weather(city: str) -> str:
    """Look up the weather."""
    return f"22.5 degrees in {city}"


def _run_loop(llm_task, budget, **kwargs):
    from flux.tasks.ai.agent_loop import run_agent_loop

    @workflow
    async def wf(ctx: ExecutionContext):
        return await run_agent_loop(
            llm_task=llm_task,
            formatter=MockFormatter(),
            system_prompt="You are an agent.",
            instruction="Do the thing.",
            budget=budget,
            **kwargs,
        )

    return wf.run()


class TestBudgetObject:
    def test_tracking_only_by_default(self):
        budget = Budget()
        budget.record(Usage(input_tokens=100, output_tokens=50))
        assert budget.spent() == 150
        assert budget.remaining() is None
        budget.check()  # no ceiling, never raises

    def test_remaining_floors_at_zero(self):
        budget = Budget(max_tokens=100)
        budget.record(Usage(input_tokens=90, output_tokens=60))
        assert budget.spent() == 150
        assert budget.remaining() == 0

    def test_check_raises_at_ceiling(self):
        budget = Budget(max_tokens=100)
        budget.record(Usage(input_tokens=60, output_tokens=40))
        with pytest.raises(BudgetExceededError) as exc_info:
            budget.check()
        assert exc_info.value.spent_tokens == 100
        assert exc_info.value.max_tokens == 100

    def test_none_usage_is_noop(self):
        budget = Budget(max_tokens=100)
        budget.record(None)
        assert budget.spent() == 0
        budget.check()

    def test_invalid_ceiling_rejected(self):
        with pytest.raises(ValueError):
            Budget(max_tokens=0)


class TestAgentLoopAccounting:
    def test_accumulates_across_calls(self):
        budget = Budget(max_tokens=10_000)
        responses = [
            LLMResponse(
                text="",
                tool_calls=[ToolCall(id="1", name="get_weather", arguments={"city": "Paris"})],
                usage=Usage(input_tokens=100, output_tokens=20),
            ),
            LLMResponse(text="done", usage=Usage(input_tokens=150, output_tokens=30)),
        ]
        ctx = _run_loop(
            _make_llm_task(responses),
            budget,
            tools=[get_weather],
            tool_schemas=[{"name": "get_weather"}],
        )

        assert ctx.has_succeeded, ctx.output
        assert budget.spent() == 300

    def test_preflight_gate_fails_the_agent(self):
        budget = Budget(max_tokens=100)
        responses = [
            LLMResponse(
                text="",
                tool_calls=[ToolCall(id="1", name="get_weather", arguments={"city": "Paris"})],
                usage=Usage(input_tokens=90, output_tokens=20),  # over the ceiling
            ),
            LLMResponse(text="done", usage=Usage(input_tokens=10, output_tokens=10)),
        ]
        ctx = _run_loop(
            _make_llm_task(responses),
            budget,
            tools=[get_weather],
            tool_schemas=[{"name": "get_weather"}],
        )

        assert ctx.has_failed
        assert "budget exceeded" in str(ctx.output).lower()
        # The first call went through; the second never started.
        assert budget.spent() == 110

    def test_budget_exceeded_is_catchable_in_workflow_code(self):
        from flux.tasks.ai.agent_loop import run_agent_loop

        budget = Budget(max_tokens=50)
        budget.record(Usage(input_tokens=40, output_tokens=20))

        @workflow
        async def wf(ctx: ExecutionContext):
            try:
                return await run_agent_loop(
                    llm_task=_make_llm_task([LLMResponse(text="unreachable")]),
                    formatter=MockFormatter(),
                    system_prompt="You are an agent.",
                    instruction="Do the thing.",
                    budget=budget,
                )
            except BudgetExceededError as e:
                return f"stopped at {e.spent_tokens}/{e.max_tokens}"

        ctx = wf.run()
        assert ctx.has_succeeded, ctx.output
        assert ctx.output == "stopped at 60/50"

    def test_missing_usage_tracks_nothing_but_still_works(self):
        budget = Budget(max_tokens=100)
        ctx = _run_loop(
            _make_llm_task([LLMResponse(text="done")]),  # no usage reported
            budget,
        )

        assert ctx.has_succeeded, ctx.output
        assert budget.spent() == 0

    def test_shared_budget_across_agents(self):
        budget = Budget(max_tokens=10_000)

        def _one_agent():
            return _run_loop(
                _make_llm_task(
                    [LLMResponse(text="done", usage=Usage(input_tokens=100, output_tokens=50))],
                ),
                budget,
            )

        ctx1 = _one_agent()
        ctx2 = _one_agent()
        assert ctx1.has_succeeded and ctx2.has_succeeded
        assert budget.spent() == 300

    def test_streaming_early_path_bypassed_when_budget_set(self):
        """With a budget, a tool-less streaming agent must route through the
        LLM task (which reports usage) instead of the token-stream path."""
        budget = Budget(max_tokens=10_000)
        ctx = _run_loop(
            _make_llm_task(
                [LLMResponse(text="done", usage=Usage(input_tokens=10, output_tokens=5))],
            ),
            budget,
            stream=True,
        )

        assert ctx.has_succeeded, ctx.output
        assert ctx.output == "done"
        assert budget.spent() == 15


class TestProviderUsageExtraction:
    def test_openai(self):
        from flux.tasks.ai.openai import _to_llm_response

        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="hi", tool_calls=None),
                ),
            ],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=25),
        )
        result = _to_llm_response(response)
        assert result.usage == Usage(input_tokens=100, output_tokens=25)

    def test_openai_missing_usage(self):
        from flux.tasks.ai.openai import _to_llm_response

        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="hi", tool_calls=None),
                ),
            ],
            usage=None,
        )
        assert _to_llm_response(response).usage is None

    def test_anthropic(self):
        from flux.tasks.ai.anthropic import _to_llm_response

        response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="hi")],
            usage=SimpleNamespace(input_tokens=200, output_tokens=50),
        )
        result = _to_llm_response(response)
        assert result.usage == Usage(input_tokens=200, output_tokens=50)

    def test_ollama(self):
        from flux.tasks.ai.ollama import _to_llm_response

        response = {
            "message": {"content": "hi"},
            "prompt_eval_count": 80,
            "eval_count": 40,
        }
        result = _to_llm_response(response)
        assert result.usage == Usage(input_tokens=80, output_tokens=40)

    def test_ollama_missing_counts(self):
        from flux.tasks.ai.ollama import _to_llm_response

        assert _to_llm_response({"message": {"content": "hi"}}).usage is None

    def test_gemini(self):
        from flux.tasks.ai.gemini import _to_usage

        response = SimpleNamespace(
            usage_metadata=SimpleNamespace(
                prompt_token_count=300,
                candidates_token_count=60,
                thoughts_token_count=40,
            ),
        )
        assert _to_usage(response) == Usage(input_tokens=300, output_tokens=100)

    def test_gemini_missing_metadata(self):
        from flux.tasks.ai.gemini import _to_usage

        assert _to_usage(SimpleNamespace(usage_metadata=None)) is None


class Answer(BaseModel):
    value: int


def test_budget_covers_schema_retry_calls():
    budget = Budget(max_tokens=10_000)
    responses = [
        LLMResponse(text="prose", usage=Usage(input_tokens=100, output_tokens=10)),
        LLMResponse(
            text=json.dumps({"value": 42}),
            usage=Usage(input_tokens=120, output_tokens=10),
        ),
    ]
    ctx = _run_loop(
        _make_llm_task(responses),
        budget,
        response_format=Answer,
    )

    assert ctx.has_succeeded, ctx.output
    assert ctx.output.value == 42
    assert budget.spent() == 240
