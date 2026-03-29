"""Integration tests for Gemini provider: full workflow execution with mocked SDK."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import BaseModel

from flux import ExecutionContext, task, workflow
from flux.tasks.ai import agent


class WeatherReport(BaseModel):
    city: str
    temperature: float
    condition: str


class TestGeminiWorkflowIntegration:
    def test_gemini_agent_workflow_basic(self):
        mock_response = MagicMock()
        mock_response.text = "The sky is blue because of Rayleigh scattering."
        mock_response.function_calls = None

        with patch("flux.tasks.ai.gemini.genai") as mock_genai:
            mock_client = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(
                return_value=mock_response,
            )
            mock_genai.Client.return_value = mock_client

            @workflow
            async def gemini_basic_workflow(ctx: ExecutionContext):
                assistant = await agent(
                    system_prompt="You are a helpful assistant.",
                    model="google/gemini-2.5-flash",
                    stream=False,
                )
                return await assistant(ctx.input["message"])

            result = gemini_basic_workflow.run({"message": "Why is the sky blue?"})

            assert result.has_succeeded
            assert result.output == "The sky is blue because of Rayleigh scattering."

    def test_gemini_agent_workflow_with_tools(self):
        mock_fc = MagicMock()
        mock_fc.name = "get_weather"
        mock_fc.args = {"city": "London"}

        mock_response_1 = MagicMock()
        mock_response_1.function_calls = [mock_fc]
        mock_response_1.candidates = [MagicMock()]
        mock_response_1.candidates[0].content = MagicMock()

        mock_response_2 = MagicMock()
        mock_response_2.text = "The weather in London is 15°C and cloudy."
        mock_response_2.function_calls = None
        mock_response_2.candidates = [MagicMock()]
        mock_response_2.candidates[0].content = MagicMock()

        with patch("flux.tasks.ai.gemini.genai") as mock_genai:
            mock_client = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(
                side_effect=[mock_response_1, mock_response_2],
            )
            mock_genai.Client.return_value = mock_client

            @task
            async def get_weather(city: str) -> str:
                """Get weather for a city."""
                return "15°C, cloudy"

            @workflow
            async def gemini_tools_workflow(ctx: ExecutionContext):
                assistant = await agent(
                    system_prompt="You are a weather assistant.",
                    model="google/gemini-2.5-flash",
                    tools=[get_weather],
                    stream=False,
                )
                return await assistant(ctx.input["message"])

            result = gemini_tools_workflow.run(
                {"message": "What's the weather in London?"},
            )

            assert result.has_succeeded
            assert "15°C" in result.output

    def test_gemini_agent_workflow_structured_output(self):
        mock_response = MagicMock()
        mock_response.text = '{"city": "London", "temperature": 15.0, "condition": "cloudy"}'
        mock_response.function_calls = None

        with patch("flux.tasks.ai.gemini.genai") as mock_genai:
            mock_client = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(
                return_value=mock_response,
            )
            mock_genai.Client.return_value = mock_client

            @workflow
            async def gemini_structured_workflow(ctx: ExecutionContext):
                assistant = await agent(
                    system_prompt="Return weather data as JSON.",
                    model="google/gemini-2.5-flash",
                    response_format=WeatherReport,
                    stream=False,
                )
                return await assistant(ctx.input["message"])

            result = gemini_structured_workflow.run(
                {"message": "Weather in London?"},
            )

            assert result.has_succeeded
            assert isinstance(result.output, WeatherReport)
            assert result.output.city == "London"
            assert result.output.temperature == 15.0
