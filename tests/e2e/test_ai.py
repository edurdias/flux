"""E2E tests — Ollama-based AI examples (auto-skipped without Ollama)."""
from __future__ import annotations

import pytest


@pytest.mark.ollama
def test_blog_post_writer(cli):
    cli.register("examples/ai/blog_post_writer_ollama.py")
    r = cli.run_async_and_wait(
        "blog_post_writer_ollama",
        '{"topic": "Write about Python"}',
        timeout=300,
    )
    assert r["state"] == "COMPLETED", f"output={r.get('output')}"
    assert r["output"] is not None


@pytest.mark.ollama
def test_reasoning_agent(cli):
    cli.register("examples/ai/reasoning_agent_ollama.py")
    r = cli.run_async_and_wait(
        "reasoning_agent",
        '{"question": "What is 2+2?"}',
        timeout=300,
    )
    assert r["state"] == "COMPLETED", f"output={r.get('output')}"


@pytest.mark.ollama
def test_streaming_agent(cli):
    cli.register("examples/ai/streaming_agent_ollama.py")
    r = cli.run_async_and_wait(
        "streaming_agent_ollama",
        '{"prompt": "Tell me a joke"}',
        timeout=300,
    )
    assert r["state"] == "COMPLETED", f"output={r.get('output')}"


@pytest.mark.ollama
def test_function_calling_agent(cli):
    cli.register("examples/ai/function_calling_agent_ollama.py")
    r = cli.run_async_and_wait(
        "function_calling_agent_ollama",
        '{"message": "What time is it?"}',
        target="PAUSED",
        timeout=300,
    )
    assert r["state"] == "PAUSED"


@pytest.mark.ollama
def test_conversational_agent(cli):
    cli.register("examples/ai/conversational_agent_ollama.py")
    r = cli.run_async_and_wait(
        "conversational_agent_ollama",
        '{"message": "Hello!"}',
        target="PAUSED",
        timeout=300,
    )
    assert r["state"] == "PAUSED"
