"""Structured output composing with tools + validation retry."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel

from flux import ExecutionContext, workflow
from flux.task import task
from flux.tasks.ai.models import LLMResponse, ToolCall

from tests.flux.tasks.ai.test_agent_loop import MockFormatter, _make_llm_task


class WeatherReport(BaseModel):
    temperature: float
    city: str


@task
async def get_weather(city: str) -> str:
    """Look up the weather."""
    return f"22.5 degrees in {city}"


class RecordingFormatter(MockFormatter):
    """MockFormatter that records structured-output and tool-stripping calls."""

    def __init__(self) -> None:
        self.apply_structured_output_calls = 0
        self.remove_tools_calls = 0
        self.captured_messages: list[Any] = []

    def apply_structured_output(self, response_format: Any, call_kwargs: dict) -> None:
        self.apply_structured_output_calls += 1

    def remove_tools_from_kwargs(self, call_kwargs: dict) -> dict:
        self.remove_tools_calls += 1
        return super().remove_tools_from_kwargs(call_kwargs)

    def format_user_message(self, text: str) -> dict:
        self.captured_messages.append(text)
        return super().format_user_message(text)


def _run_loop(formatter, llm_task, **kwargs):
    from flux.tasks.ai.agent_loop import run_agent_loop

    @workflow
    async def wf(ctx: ExecutionContext):
        return await run_agent_loop(
            llm_task=llm_task,
            formatter=formatter,
            system_prompt="You are a weather agent.",
            instruction="What is the weather in Paris?",
            **kwargs,
        )

    return wf.run()


def test_schema_with_tools_returns_validated_model():
    responses = [
        LLMResponse(
            text="",
            tool_calls=[ToolCall(id="1", name="get_weather", arguments={"city": "Paris"})],
        ),
        LLMResponse(text=json.dumps({"temperature": 22.5, "city": "Paris"})),
    ]
    formatter = RecordingFormatter()
    ctx = _run_loop(
        formatter,
        _make_llm_task(responses),
        tools=[get_weather],
        tool_schemas=[{"name": "get_weather"}],
        response_format=WeatherReport,
    )

    assert ctx.has_succeeded, ctx.output
    assert isinstance(ctx.output, WeatherReport)
    assert ctx.output.city == "Paris"
    # Native enforcement is tool-less only; with tools the schema rides the
    # system prompt and the final answer is validated post-hoc.
    assert formatter.apply_structured_output_calls == 0


def test_schema_appended_to_system_prompt_with_tools():
    captured: dict[str, str] = {}

    class PromptCapturingFormatter(MockFormatter):
        def build_messages(self, system_prompt, user_content, working_memory=None):
            captured["system_prompt"] = system_prompt
            return super().build_messages(system_prompt, user_content, working_memory)

    responses = [LLMResponse(text=json.dumps({"temperature": 1.0, "city": "Oslo"}))]
    ctx = _run_loop(
        PromptCapturingFormatter(),
        _make_llm_task(responses),
        tools=[get_weather],
        tool_schemas=[{"name": "get_weather"}],
        response_format=WeatherReport,
    )

    assert ctx.has_succeeded, ctx.output
    assert "Respond with JSON matching this schema" in captured["system_prompt"]
    assert "temperature" in captured["system_prompt"]


def test_native_enforcement_still_applied_without_tools():
    responses = [LLMResponse(text=json.dumps({"temperature": 1.0, "city": "Oslo"}))]
    formatter = RecordingFormatter()
    ctx = _run_loop(
        formatter,
        _make_llm_task(responses),
        response_format=WeatherReport,
    )

    assert ctx.has_succeeded, ctx.output
    assert formatter.apply_structured_output_calls == 1


def test_malformed_then_corrected_retry():
    responses = [
        LLMResponse(text="It's 22.5 degrees in Paris!"),  # prose, fails validation
        LLMResponse(text=json.dumps({"temperature": 22.5, "city": "Paris"})),
    ]
    formatter = RecordingFormatter()
    ctx = _run_loop(
        formatter,
        _make_llm_task(responses),
        response_format=WeatherReport,
    )

    assert ctx.has_succeeded, ctx.output
    assert isinstance(ctx.output, WeatherReport)
    assert ctx.output.temperature == 22.5
    # The corrective turn strips tools and feeds the validation errors back.
    assert formatter.remove_tools_calls == 1
    correction = formatter.captured_messages[-1]
    assert "did not match the required schema" in correction
    assert "Respond again with ONLY valid JSON" in correction


def test_retry_exhaustion_fails_with_validation_error():
    responses = [
        LLMResponse(text="still prose"),
        LLMResponse(text="more prose"),
    ]
    ctx = _run_loop(
        RecordingFormatter(),
        _make_llm_task(responses),
        response_format=WeatherReport,
        max_schema_retries=1,
    )

    assert ctx.has_failed
    assert "validation error" in str(ctx.output).lower()


def test_zero_retries_fails_fast():
    responses = [
        LLMResponse(text="prose"),
        LLMResponse(text=json.dumps({"temperature": 1.0, "city": "Oslo"})),
    ]
    formatter = RecordingFormatter()
    ctx = _run_loop(
        formatter,
        _make_llm_task(responses),
        response_format=WeatherReport,
        max_schema_retries=0,
    )

    assert ctx.has_failed
    assert formatter.remove_tools_calls == 0


def test_fenced_json_accepted():
    fenced = f"```json\n{json.dumps({'temperature': 22.5, 'city': 'Paris'})}\n```"
    ctx = _run_loop(
        RecordingFormatter(),
        _make_llm_task([LLMResponse(text=fenced)]),
        response_format=WeatherReport,
    )

    assert ctx.has_succeeded, ctx.output
    assert isinstance(ctx.output, WeatherReport)


def test_retry_after_tool_loop():
    responses = [
        LLMResponse(
            text="",
            tool_calls=[ToolCall(id="1", name="get_weather", arguments={"city": "Paris"})],
        ),
        LLMResponse(text="The weather is nice."),  # fails validation
        LLMResponse(text=json.dumps({"temperature": 22.5, "city": "Paris"})),
    ]
    formatter = RecordingFormatter()
    ctx = _run_loop(
        formatter,
        _make_llm_task(responses),
        tools=[get_weather],
        tool_schemas=[{"name": "get_weather"}],
        response_format=WeatherReport,
    )

    assert ctx.has_succeeded, ctx.output
    assert isinstance(ctx.output, WeatherReport)


def test_plain_text_agents_unchanged():
    ctx = _run_loop(
        RecordingFormatter(),
        _make_llm_task([LLMResponse(text="Just prose.")]),
    )

    assert ctx.has_succeeded, ctx.output
    assert ctx.output == "Just prose."


class TestJsonCandidate:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ('{"a": 1}', '{"a": 1}'),
            ('```json\n{"a": 1}\n```', '{"a": 1}'),
            ('```\n{"a": 1}\n```', '{"a": 1}'),
            ("  prose  ", "  prose  "),
        ],
    )
    def test_fence_stripping(self, raw: str, expected: str):
        from flux.tasks.ai.agent_loop import _json_candidate

        assert _json_candidate(raw) == expected


class TestWorkflowAgentContract:
    @staticmethod
    def _mock_client(response: dict):
        from unittest.mock import AsyncMock, MagicMock

        client = MagicMock()
        client.run_workflow_sync = AsyncMock(return_value=response)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        return client

    def _run(self, response: dict):
        from unittest.mock import patch

        from flux.tasks.ai.delegation import workflow_agent

        wa = workflow_agent(
            name="reporter",
            description="Reports weather.",
            workflow="weather_wf",
            response_format=WeatherReport,
        )

        mock_client = self._mock_client(response)

        @workflow
        async def wf(ctx: ExecutionContext):
            with patch("flux.tasks.ai.delegation._get_client", return_value=mock_client):
                return await wa("weather please")

        return wf.run()

    def test_valid_output_is_validated_and_surfaced(self):
        ctx = self._run(
            {
                "execution_id": "exec-1",
                "state": "COMPLETED",
                "output": {"temperature": 22.5, "city": "Paris"},
            },
        )
        assert ctx.has_succeeded, ctx.output
        assert ctx.output.status == "completed"
        assert ctx.output.output == {"temperature": 22.5, "city": "Paris"}

    def test_contract_violation_becomes_failure(self):
        ctx = self._run(
            {
                "execution_id": "exec-1",
                "state": "COMPLETED",
                "output": {"wrong": "shape"},
            },
        )
        assert ctx.has_succeeded, ctx.output
        assert ctx.output.status == "failed"
        assert "did not match the expected schema" in ctx.output.output
        assert "WeatherReport" in ctx.output.output

    def test_failed_workflow_output_not_validated(self):
        ctx = self._run(
            {
                "execution_id": "exec-1",
                "state": "FAILED",
                "output": "traceback...",
            },
        )
        assert ctx.has_succeeded, ctx.output
        assert ctx.output.status == "failed"
        assert ctx.output.output == "traceback..."


def test_delegate_surfaces_validated_model_as_data():
    from flux.tasks.ai.delegation import build_delegate

    @task.with_options(name="reporter")
    async def reporter(instruction: str, *, context: str = "") -> WeatherReport:
        return WeatherReport(temperature=22.5, city="Paris")

    reporter.description = "Reports the weather."

    delegate = build_delegate([reporter])

    @workflow
    async def wf(ctx: ExecutionContext):
        return await delegate(agent="reporter", instruction="weather please")

    ctx = wf.run()
    assert ctx.has_succeeded, ctx.output
    assert ctx.output["status"] == "completed"
    assert ctx.output["output"] == {"temperature": 22.5, "city": "Paris"}
