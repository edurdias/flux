"""
SSE Client for Consuming Flux Streaming Events.

This example demonstrates how to consume real-time streaming events from
a Flux workflow using Server-Sent Events (SSE). It connects to the Flux
HTTP API and displays streaming LLM tokens as they're generated.

Use Case:
When running streaming_with_task_events_ollama, each token batch generates
Flux task events (TASK_STARTED + TASK_COMPLETED). This client consumes those
events in real-time via SSE and reconstructs the streaming response.

Prerequisites:
    1. Flux server running: flux start server
    2. Flux worker running: flux start worker
    3. Ollama running: ollama serve
    4. httpx-sse library: pip install httpx httpx-sse

Usage:
    python examples/ai/stream_event_client.py

    Or with custom prompt:
    python examples/ai/stream_event_client.py "Explain neural networks briefly"
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx
from httpx_sse import aconnect_sse


async def stream_workflow_execution(
    workflow_name: str,
    input_data: dict,
    flux_url: str = "http://localhost:8000",
):
    """
    Stream workflow execution events via SSE.

    This connects to Flux's SSE endpoint and receives real-time events
    as the workflow executes. For streaming_with_task_events_ollama,
    you'll see TASK_COMPLETED events containing token batches.

    Args:
        workflow_name: Name of the workflow to execute
        input_data: Input data for the workflow
        flux_url: Flux server URL
    """
    url = f"{flux_url}/workflows/{workflow_name}/run/stream"
    params = {"detailed": "true"}  # Get full event details

    print("=" * 60)
    print("FLUX STREAMING EVENT CLIENT")
    print("=" * 60)
    print(f"Workflow: {workflow_name}")
    print(f"Endpoint: {url}")
    print(f"Prompt: {input_data.get('prompt', 'N/A')}")
    print("=" * 60)
    print("\nConnecting to SSE stream...\n")

    try:
        async with httpx.AsyncClient() as client:
            async with aconnect_sse(
                client,
                "POST",
                url,
                json=input_data,
                params=params,
                timeout=120.0,
            ) as event_source:
                complete_response = ""
                token_count = 0
                batch_count = 0

                async for sse_event in event_source.aiter_sse():
                    try:
                        # Parse SSE event data
                        data = json.loads(sse_event.data)
                        state = data.get("state", "")
                        events = data.get("events", [])

                        # Process events from this update
                        for event in events:
                            event_type = event.get("type")
                            event_name = event.get("name")
                            event_value = event.get("value", {})

                            # Look for task completion events with token data
                            if (
                                event_type == "TASK_COMPLETED"
                                and event_name == "process_token_batch"
                            ):
                                # Extract token batch from event
                                if isinstance(event_value, dict):
                                    tokens = event_value.get("tokens", "")
                                    batch_num = event_value.get("batch_number", 0)
                                    total = event_value.get("total_tokens", 0)

                                    # Display tokens in real-time
                                    print(tokens, end="", flush=True)

                                    complete_response += tokens
                                    token_count = total
                                    batch_count = batch_num + 1

                        # Check if workflow completed
                        if state in ["COMPLETED", "FAILED", "CANCELLED"]:
                            print("\n\n" + "=" * 60)
                            print(f"WORKFLOW {state}")
                            print("=" * 60)

                            if state == "COMPLETED":
                                # Get final output
                                output = data.get("output", {})
                                metrics = output.get("streaming_metrics", {})

                                print("\nStreaming Metrics:")
                                print(f"  Total tokens: {metrics.get('total_tokens', token_count)}")
                                print(
                                    f"  Total batches: {metrics.get('total_batches', batch_count)}",
                                )
                                print(
                                    f"  Batch size: {metrics.get('batch_size', 'N/A')} tokens",
                                )
                                print(
                                    f"  Generation time: {metrics.get('generation_time_seconds', 'N/A')}s",
                                )
                                print(
                                    f"  Tokens/second: {metrics.get('tokens_per_second', 'N/A')}",
                                )

                                event_info = output.get("event_info", {})
                                print("\nEvent Info:")
                                print(
                                    f"  Task events generated: {event_info.get('task_events_generated', 'N/A')}",
                                )
                                print(
                                    f"  Message: {event_info.get('message', 'N/A')}",
                                )

                            break

                    except json.JSONDecodeError:
                        print("\nWarning: Failed to parse SSE event data")
                        continue
                    except Exception as e:
                        print(f"\nError processing event: {e}")
                        continue

    except httpx.ConnectError:
        print(
            f"\nError: Could not connect to Flux server at {flux_url}",
        )
        print("Make sure the Flux server is running: flux start server")
    except Exception as e:
        print(f"\nError: {e}")


async def main():
    """Run the streaming client example."""
    # Get prompt from command line or use default
    default_prompt = "Explain how neural networks learn from data in 2 paragraphs."
    prompt = sys.argv[1] if len(sys.argv) > 1 else default_prompt

    # Workflow configuration
    workflow_name = "streaming_with_task_events_ollama"
    input_data = {
        "prompt": prompt,
        "model": "llama3.2",
        "batch_size": 5,  # Smaller batches = more frequent events
    }

    print("\nThis client demonstrates:")
    print("  1. Connecting to Flux SSE endpoint")
    print("  2. Receiving real-time task events")
    print("  3. Extracting token batches from TASK_COMPLETED events")
    print("  4. Reconstructing the streaming response\n")

    await stream_workflow_execution(workflow_name, input_data)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        print(f"\nError: {e}")
        print("\nMake sure:")
        print("  1. Flux server is running: flux start server")
        print("  2. Flux worker is running: flux start worker")
        print("  3. Ollama is running: ollama serve")
        print(
            "  4. Workflow is registered: flux workflow register examples/ai/streaming_with_task_events_ollama.py",
        )
