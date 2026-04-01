"""
LLM Call Replay Demo.

Demonstrates that LLM calls replay from events on workflow resume.

The workflow:
1. Agent calls a tool (LLM decides to use get_weather, tool executes)
2. Pauses for human confirmation
3. On resume, the workflow re-executes from the top — but the LLM @task
   and tool @task replay from cached events (no API calls, no re-execution)
4. After the replayed agent call, makes a SECOND fresh agent call to prove
   the workflow continues normally after replay

This proves deterministic resume: the same LLM response is returned on
replay, the same tool call is made, and execution continues predictably.

Prerequisites:
    1. Install Ollama and pull a model: ollama pull llama3.2
    2. Start Ollama service: ollama serve

Usage:
    flux workflow register examples/ai/replay_demo.py
    flux workflow run replay_demo '{"city": "London"}'

    # Wait for pause, check status:
    flux workflow status replay_demo <execution_id>

    # Resume:
    flux execution resume <execution_id> '{"confirmed": true}'

    # Check final output — both answers present, workflow completed:
    flux workflow status replay_demo <execution_id>
"""

from __future__ import annotations

from typing import Any

from flux import ExecutionContext, task, workflow
from flux.tasks import pause
from flux.tasks.ai import agent


@task
async def get_weather(city: str) -> str:
    """Get the current weather for a city."""
    return f"Sunny, 22C in {city}"


@workflow
async def replay_demo(ctx: ExecutionContext[dict[str, Any]]):
    raw = ctx.input or {}
    city = raw.get("city", "London")

    assistant = await agent(
        "You are a weather assistant. Always use the get_weather tool to answer weather questions. Be concise.",
        model="ollama/llama3.2",
        name="weather_bot",
        tools=[get_weather],
        stream=False,
    )

    first_answer = await assistant(f"What is the weather in {city}?")

    confirmation = await pause(
        "confirm_replay",
        output={
            "message": "First agent call done. Resume to trigger replay + second call.",
            "first_answer": first_answer,
        },
    )

    second_answer = await assistant(f"Tell me the weather forecast for {city}")

    return {
        "city": city,
        "first_answer": first_answer,
        "second_answer": second_answer,
        "confirmation": confirmation,
    }


if __name__ == "__main__":  # pragma: no cover
    import json

    print("=== LLM Call Replay Demo ===\n")
    print("Step 1: Running workflow (agent calls tool, then pauses)...")

    result = replay_demo.run({"city": "London"})

    if result.is_paused:
        print(f"  Paused. Execution ID: {result.execution_id}")
        print(f"  First answer: {result.output.get('first_answer', 'N/A')}")
        print("\nStep 2: Resuming (LLM call replays from events, then second call runs)...")

        result = replay_demo.resume(result.execution_id, {"confirmed": True})

        if result.has_succeeded:
            output = result.output
            print(f"\n  First answer:  {output['first_answer']}")
            print(f"  Second answer: {output['second_answer']}")
            print(f"  Confirmation:  {output['confirmation']}")
            print("\nSUCCESS: Workflow completed after replay + fresh second call.")
        else:
            print(f"  Failed: {result.output}")
    elif result.has_succeeded:
        print(f"  Completed: {json.dumps(result.output, indent=2)}")
    else:
        print(f"  Failed: {result.output}")
