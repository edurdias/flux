"""E2E tests — Ollama-based AI examples (auto-skipped without Ollama)."""
from __future__ import annotations

import pytest


@pytest.mark.ollama
def test_blog_post_writer(cli):
    cli.register("examples/ai/blog_post_writer_ollama.py")
    r = cli.run("blog_post_writer_ollama", '"Write about Python"', timeout=120)
    assert r["state"] == "COMPLETED"
    assert r["output"] is not None


@pytest.mark.ollama
def test_reasoning_agent(cli):
    cli.register("examples/ai/reasoning_agent_ollama.py")
    r = cli.run("reasoning_agent_ollama", '"What is 2+2?"', timeout=120)
    assert r["state"] == "COMPLETED"


@pytest.mark.ollama
def test_streaming_agent(cli):
    cli.register("examples/ai/streaming_agent_ollama.py")
    r = cli.run("streaming_agent_ollama", '"Tell me a joke"', timeout=120)
    assert r["state"] == "COMPLETED"


@pytest.mark.ollama
def test_function_calling_agent(cli):
    cli.register("examples/ai/function_calling_agent_ollama.py")
    r = cli.run("function_calling_agent_ollama", '"What time is it?"', timeout=120)
    assert r["state"] == "COMPLETED"


@pytest.mark.ollama
def test_conversational_agent(cli):
    cli.register("examples/ai/conversational_agent_ollama.py")
    r = cli.run("conversational_agent_ollama", '"Hello!"', timeout=120)
    assert r["state"] == "COMPLETED"
