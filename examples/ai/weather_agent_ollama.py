"""
Weather Agent using Flux agent() with Tool Use.

This example demonstrates the agent() primitive with tool calling — the agent
autonomously decides when to call weather tools to answer user questions.

Compare with:
- examples/ai/function_calling_agent_ollama.py (manual tool loop, ~400 lines)

The agent() version replaces the manual tool-use loop, message management, and
LLM interaction code with a single agent() call. The tools are existing Flux
@task functions — their signature and docstring are used to generate tool schemas
automatically.

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model that supports tools: ollama pull llama3.2
    3. Start Ollama service: ollama serve

Usage:
    flux workflow run weather_agent_ollama '{"question": "What is the weather in San Francisco?"}'
    flux workflow run weather_agent_ollama '{"question": "Compare weather in Tokyo and London"}'
"""

from __future__ import annotations

from typing import Any

import httpx

from flux import ExecutionContext, task, workflow
from flux.tasks.ai import agent


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=30)
async def get_current_weather(location: str) -> str:
    """Get the current weather for a city. Use this when the user asks about current weather, temperature, or conditions."""
    try:
        async with httpx.AsyncClient() as client:
            geo = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location, "count": 1, "language": "en", "format": "json"},
            )
            geo.raise_for_status()
            geo_data = geo.json()

            if not geo_data.get("results"):
                return f"Location not found: {location}"

            result = geo_data["results"][0]
            lat, lon = result["latitude"], result["longitude"]
            name = f"{result['name']}, {result.get('country', '')}"

            weather = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,apparent_temperature,wind_speed_10m,weather_code",
                    "temperature_unit": "fahrenheit",
                    "wind_speed_unit": "mph",
                },
            )
            weather.raise_for_status()
            current = weather.json()["current"]

            codes = {
                0: "Clear",
                1: "Mainly clear",
                2: "Partly cloudy",
                3: "Overcast",
                45: "Foggy",
                51: "Light drizzle",
                61: "Light rain",
                63: "Rain",
                65: "Heavy rain",
                71: "Light snow",
                73: "Snow",
                95: "Thunderstorm",
            }

            return (
                f"Weather in {name}: {current['temperature_2m']}°F "
                f"(feels like {current['apparent_temperature']}°F), "
                f"{codes.get(current['weather_code'], 'Unknown conditions')}, "
                f"humidity {current['relative_humidity_2m']}%, "
                f"wind {current['wind_speed_10m']} mph"
            )
    except Exception as e:
        return f"Failed to get weather for '{location}': {e}"


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=30)
async def get_weather_forecast(location: str, days: int = 3) -> str:
    """Get the weather forecast for the next several days. Use this when the user asks about future weather or forecasts."""
    try:
        days = max(1, min(days, 7))
        async with httpx.AsyncClient() as client:
            geo = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location, "count": 1, "language": "en", "format": "json"},
            )
            geo.raise_for_status()
            geo_data = geo.json()

            if not geo_data.get("results"):
                return f"Location not found: {location}"

            result = geo_data["results"][0]
            name = f"{result['name']}, {result.get('country', '')}"

            weather = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": result["latitude"],
                    "longitude": result["longitude"],
                    "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                    "temperature_unit": "fahrenheit",
                    "forecast_days": days,
                },
            )
            weather.raise_for_status()
            daily = weather.json()["daily"]

            lines = [f"Forecast for {name}:"]
            for i in range(len(daily["time"])):
                lines.append(
                    f"  {daily['time'][i]}: {daily['temperature_2m_min'][i]}°F - {daily['temperature_2m_max'][i]}°F",
                )
            return "\n".join(lines)
    except Exception as e:
        return f"Failed to get forecast for '{location}': {e}"


weather_assistant = agent(
    "You are a helpful weather assistant. Use the available tools to look up "
    "current weather conditions and forecasts. Always provide clear, concise answers.",
    model="ollama/llama3.2",
    name="weather_assistant",
    tools=[get_current_weather, get_weather_forecast],
).with_instance_options(retry_max_attempts=2, timeout=120)


@workflow.with_options(name="weather_agent_ollama")
async def weather_agent_ollama(ctx: ExecutionContext[dict[str, Any]]):
    """
    Weather agent using Flux agent() with tool calling.

    The agent autonomously decides which weather tools to call based on the question.

    Input format:
    {
        "question": "What's the weather in San Francisco?"
    }
    """
    input_data = ctx.input or {}
    question = input_data.get("question")
    if not question:
        return {"error": "Missing required parameter 'question'", "execution_id": ctx.execution_id}

    answer = await weather_assistant(question)

    return {
        "question": question,
        "answer": answer,
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":  # pragma: no cover
    questions = [
        "What's the current weather in San Francisco?",
        "Give me the 3-day forecast for Tokyo",
    ]

    for question in questions:
        try:
            print("=" * 80)
            print(f"Q: {question}")
            print("=" * 80)

            result = weather_agent_ollama.run({"question": question})

            if result.has_failed:
                raise Exception(f"Workflow failed: {result.output}")

            print(f"\nA: {result.output['answer']}\n")

        except Exception as e:
            print(f"Error: {e}")
            print("Make sure Ollama is running: ollama serve")
            print("And model is pulled: ollama pull llama3.2\n")
