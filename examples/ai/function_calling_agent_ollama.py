"""
Conversational AI Agent with Function Calling using Ollama (Local LLM).

This example demonstrates how to build an AI agent that can intelligently use external
tools to accomplish tasks. The agent uses Ollama for LLM inference and calls weather
APIs to answer questions about weather conditions.

Key Features:
- Function/tool calling with local LLMs (llama3.2, mistral, etc.)
- Real weather data from Open-Meteo API (no API key required)
- Multi-turn conversations with tool use
- Stateful execution with pause/resume
- Automatic tool selection by the LLM

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model that supports tools: ollama pull llama3.2
    3. Start Ollama service: ollama serve

Usage:
    # Start a conversation about weather
    flux workflow run function_calling_agent_ollama '{"message": "What'\''s the weather in San Francisco?"}'

    # Continue the conversation
    flux workflow resume function_calling_agent_ollama <execution_id> '{"message": "How about New York?"}'

    # Compare weather between cities
    flux workflow run function_calling_agent_ollama '{"message": "Compare the weather in Tokyo and London"}'
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from ollama import AsyncClient

from flux import ExecutionContext, task, workflow
from flux.tasks import pause


# =============================================================================
# Weather API Integration (Open-Meteo - No API Key Required)
# =============================================================================


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=30)
async def geocode_location(location: str) -> dict[str, Any]:
    """
    Convert a location name to latitude/longitude coordinates.

    Args:
        location: City name (e.g., "San Francisco, CA" or "London")

    Returns:
        Dictionary with latitude, longitude, and formatted name
    """
    try:
        # Use Open-Meteo geocoding API (free, no key required)
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location, "count": 1, "language": "en", "format": "json"},
            )
            response.raise_for_status()
            data = response.json()

            if not data.get("results"):
                raise ValueError(f"Location not found: {location}")

            result = data["results"][0]
            return {
                "name": result["name"],
                "country": result.get("country", ""),
                "latitude": result["latitude"],
                "longitude": result["longitude"],
            }

    except Exception as e:
        raise RuntimeError(f"Failed to geocode location '{location}': {str(e)}") from e


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=30)
async def get_current_weather(location: str) -> dict[str, Any]:
    """
    Get the current weather conditions for a specific location. Use this when the user asks about current weather, temperature, or conditions.

    Args:
        location: The city name, e.g. 'San Francisco' or 'London, UK'

    Returns:
        Dictionary with current weather conditions
    """
    try:
        # Geocode the location first
        geo_data = await geocode_location(location)

        # Fetch weather data from Open-Meteo
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": geo_data["latitude"],
                    "longitude": geo_data["longitude"],
                    "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
                    "temperature_unit": "fahrenheit",
                    "wind_speed_unit": "mph",
                },
            )
            response.raise_for_status()
            data = response.json()

            current = data["current"]

            # Map weather codes to descriptions
            weather_codes = {
                0: "Clear sky",
                1: "Mainly clear",
                2: "Partly cloudy",
                3: "Overcast",
                45: "Foggy",
                48: "Depositing rime fog",
                51: "Light drizzle",
                53: "Moderate drizzle",
                55: "Dense drizzle",
                61: "Slight rain",
                63: "Moderate rain",
                65: "Heavy rain",
                71: "Slight snow",
                73: "Moderate snow",
                75: "Heavy snow",
                95: "Thunderstorm",
            }

            weather_description = weather_codes.get(current["weather_code"], "Unknown")

            return {
                "location": f"{geo_data['name']}, {geo_data['country']}",
                "temperature": round(current["temperature_2m"], 1),
                "feels_like": round(current["apparent_temperature"], 1),
                "humidity": current["relative_humidity_2m"],
                "conditions": weather_description,
                "precipitation": current["precipitation"],
                "wind_speed": current["wind_speed_10m"],
                "temperature_unit": "°F",
                "wind_unit": "mph",
            }

    except Exception as e:
        raise RuntimeError(
            f"Failed to get weather for '{location}': {str(e)}",
        ) from e


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=30)
async def get_weather_forecast(location: str, days: int = 7) -> dict[str, Any]:
    """
    Get the weather forecast for the next several days. Use this when the user asks about future weather or forecasts.

    Args:
        location: The city name, e.g. 'San Francisco' or 'London, UK'
        days: Number of days to forecast (1-16, default: 7)

    Returns:
        Dictionary with daily forecast data
    """
    try:
        # Limit days to valid range
        days = max(1, min(days, 16))

        # Geocode the location
        geo_data = await geocode_location(location)

        # Fetch forecast from Open-Meteo
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": geo_data["latitude"],
                    "longitude": geo_data["longitude"],
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code",
                    "temperature_unit": "fahrenheit",
                    "forecast_days": days,
                },
            )
            response.raise_for_status()
            data = response.json()

            daily = data["daily"]
            forecast_days = []

            for i in range(len(daily["time"])):
                # Simplified weather code mapping
                weather_code = daily["weather_code"][i]
                if weather_code <= 3:
                    conditions = "Clear/Partly Cloudy"
                elif weather_code <= 48:
                    conditions = "Fog"
                elif weather_code <= 55:
                    conditions = "Drizzle"
                elif weather_code <= 65:
                    conditions = "Rain"
                elif weather_code <= 75:
                    conditions = "Snow"
                else:
                    conditions = "Thunderstorm"

                forecast_days.append(
                    {
                        "date": daily["time"][i],
                        "high": round(daily["temperature_2m_max"][i], 1),
                        "low": round(daily["temperature_2m_min"][i], 1),
                        "conditions": conditions,
                        "precipitation": round(daily["precipitation_sum"][i], 2),
                    },
                )

            return {
                "location": f"{geo_data['name']}, {geo_data['country']}",
                "forecast": forecast_days,
                "temperature_unit": "°F",
            }

    except Exception as e:
        raise RuntimeError(
            f"Failed to get forecast for '{location}': {str(e)}",
        ) from e


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=30)
async def compare_weather(location1: str, location2: str) -> dict[str, Any]:
    """
    Compare current weather between two locations. Use this when the user wants to compare weather in different cities.

    Args:
        location1: The first city name
        location2: The second city name

    Returns:
        Dictionary with weather data for both locations and comparison
    """
    try:
        # Get weather for both locations concurrently
        weather1 = await get_current_weather(location1)
        weather2 = await get_current_weather(location2)

        # Calculate temperature difference
        temp_diff = abs(weather1["temperature"] - weather2["temperature"])
        warmer_location = (
            weather1["location"]
            if weather1["temperature"] > weather2["temperature"]
            else weather2["location"]
        )

        comparison_text = (
            f"{warmer_location} is {temp_diff:.1f}°F warmer"
            if temp_diff > 0
            else "Both locations have the same temperature"
        )

        return {
            "location1": weather1,
            "location2": weather2,
            "comparison": comparison_text,
        }

    except Exception as e:
        raise RuntimeError(
            f"Failed to compare weather between '{location1}' and '{location2}': {str(e)}",
        ) from e


# =============================================================================
# Available Tools (Ollama automatically parses these Python functions)
# =============================================================================

# The Ollama Python SDK automatically converts these functions to tool schemas.
# We can pass them directly to the tools parameter without manual conversion.
WEATHER_TOOLS = [
    get_current_weather,
    get_weather_forecast,
    compare_weather,
]


# =============================================================================
# Tool Execution
# =============================================================================


@task
async def execute_tool_call(tool_name: str, tool_args: dict[str, Any]) -> str:
    """
    Execute a tool call and return the result as a JSON string.

    Args:
        tool_name: Name of the tool to execute
        tool_args: Arguments for the tool

    Returns:
        JSON string with tool execution result
    """
    try:
        if tool_name == "get_current_weather":
            result = await get_current_weather(tool_args["location"])
        elif tool_name == "get_weather_forecast":
            days = tool_args.get("days", 7)
            result = await get_weather_forecast(tool_args["location"], days)
        elif tool_name == "compare_weather":
            result = await compare_weather(tool_args["location1"], tool_args["location2"])
        else:
            raise ValueError(f"Unknown tool: {tool_name}")

        return json.dumps(result)

    except Exception as e:
        return json.dumps({"error": str(e)})


# =============================================================================
# LLM Integration with Tool Calling
# =============================================================================


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=60)
async def call_ollama_with_tools(
    messages: list[dict[str, Any]],
    system_prompt: str,
    model: str,
    ollama_url: str,
) -> dict[str, Any]:
    """
    Call Ollama API with tool/function calling support.

    Args:
        messages: Conversation history
        system_prompt: System prompt
        model: Ollama model name (must support tools, e.g., llama3.2, mistral)
        ollama_url: Ollama server URL

    Returns:
        Response dict with 'message' and optional 'tool_calls'
    """
    try:
        client = AsyncClient(host=ollama_url)

        # Prepare messages with system prompt
        full_messages = [{"role": "system", "content": system_prompt}]
        full_messages.extend(messages)

        # Call Ollama with tools
        response = await client.chat(
            model=model,
            messages=full_messages,
            tools=WEATHER_TOOLS,
        )

        return response

    except Exception as e:
        raise RuntimeError(
            f"Failed to call Ollama API: {str(e)}. "
            "Make sure Ollama is running (ollama serve) and the model supports tools (e.g., llama3.2, mistral).",
        ) from e


# =============================================================================
# Main Workflow
# =============================================================================


@workflow.with_options(name="function_calling_agent_ollama")
async def function_calling_agent_ollama(ctx: ExecutionContext[dict[str, Any]]):
    """
    A conversational AI agent that can use tools/functions to answer questions.

    This agent uses Ollama's function calling capabilities to intelligently decide
    when to use external tools (like weather APIs) to answer user questions.

    Initial Input format:
    {
        "message": "User's message",
        "system_prompt": "Optional system prompt",
        "model": "llama3.2",         # or "mistral", "llama3.1", etc. (must support tools)
        "ollama_url": "http://localhost:11434",
        "max_turns": 10              # Optional: maximum conversation turns
    }

    Resume Input format:
    {
        "message": "User's next message"
    }
    """
    # Get initial configuration
    initial_input = ctx.input or {}
    system_prompt = initial_input.get(
        "system_prompt",
        "You are a helpful AI assistant with access to weather tools. "
        "Use the available tools when needed to provide accurate, real-time weather information. "
        "Be concise and informative in your responses.",
    )
    model = initial_input.get("model", "llama3.2")
    ollama_url = initial_input.get("ollama_url", "http://localhost:11434")
    max_turns = initial_input.get("max_turns", 10)

    # Initialize conversation state
    messages: list[dict[str, Any]] = []

    # Process first message
    first_message = initial_input.get("message", "")
    if not first_message:
        return {
            "error": "No message provided in initial input",
            "execution_id": ctx.execution_id,
        }

    messages.append({"role": "user", "content": first_message})

    # Main conversation loop
    for turn in range(max_turns):
        # Call LLM with tools
        response = await call_ollama_with_tools(
            messages=messages,
            system_prompt=system_prompt,
            model=model,
            ollama_url=ollama_url,
        )

        message = response["message"]

        # Check if the LLM wants to use tools
        if message.get("tool_calls"):
            # Execute all tool calls
            for tool_call in message["tool_calls"]:
                tool_name = tool_call["function"]["name"]
                tool_args = tool_call["function"]["arguments"]

                # Execute the tool
                tool_result = await execute_tool_call(tool_name, tool_args)

                # Add tool result to messages
                messages.append(
                    {
                        "role": "tool",
                        "content": tool_result,
                    },
                )

            # Call LLM again to generate final response with tool results
            response = await call_ollama_with_tools(
                messages=messages,
                system_prompt=system_prompt,
                model=model,
                ollama_url=ollama_url,
            )

            message = response["message"]

        # Add assistant response to messages
        assistant_content = message.get("content", "")
        messages.append({"role": "assistant", "content": assistant_content})

        # Pause and wait for next user input
        resume_input = await pause(f"waiting_for_user_input_turn_{turn + 1}")

        # Get next message
        next_message = resume_input.get("message", "") if resume_input else ""
        if not next_message:
            return {
                "conversation_history": messages,
                "total_turns": turn + 1,
                "execution_id": ctx.execution_id,
            }

        messages.append({"role": "user", "content": next_message})

    # Max turns reached
    return {
        "conversation_history": messages,
        "total_turns": max_turns,
        "message": "Maximum conversation turns reached",
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":  # pragma: no cover
    # Quick test of the workflow
    print("=" * 80)
    print("Function Calling Agent Demo - Weather Assistant")
    print("=" * 80 + "\n")

    try:
        # Test 1: Get current weather
        print("Test 1: Get current weather for San Francisco\n")
        result = function_calling_agent_ollama.run(
            {
                "message": "What's the weather like in San Francisco right now?",
                "model": "llama3.2",
            },
        )

        if result.has_failed:
            raise Exception(f"Test 1 failed: {result.output}")

        print(
            f"Response: {result.output.get('conversation_history', [])[-1].get('content', 'No response')}\n",
        )
        print("=" * 80 + "\n")

        # Test 2: Resume and compare weather
        print("Test 2: Resume and compare weather with another city\n")
        result = function_calling_agent_ollama.resume(
            result.execution_id,
            {"message": "How does that compare to New York?"},
        )

        if result.has_failed:
            raise Exception(f"Test 2 failed: {result.output}")

        print(
            f"Response: {result.output.get('conversation_history', [])[-1].get('content', 'No response')}\n",
        )

        print("=" * 80)
        print("✓ Function calling agent working successfully!")
        print("✓ Tools executed and results integrated into responses!")
        print("=" * 80)

    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure:")
        print("1. Ollama is running: ollama serve")
        print("2. Model with tool support is pulled:")
        print("   ollama pull llama3.2")
