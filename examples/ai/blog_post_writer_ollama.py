"""
Blog Post Writer using Flux agent() + Ollama (Local LLM).

This example demonstrates Flux's agent() task primitive — three agents collaborate
in a sequential pipeline, each building on the previous output:
1. Research Analyst — researches the topic and produces structured findings
2. Content Writer — transforms research into an engaging blog post draft
3. Content Editor — reviews and polishes the draft for publication

Each agent is a Flux @task with independent retry boundaries, full observability,
and crash-durable execution.

Compare with:
- examples/ai/crewai/blog_post_writer.py (CrewAI + Flux variant)

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model: ollama pull llama3
    3. Start Ollama service: ollama serve

Usage:
    # Write a blog post
    flux workflow run blog_post_writer_ollama '{"topic": "The Future of AI Agents"}'

    # Use a different model
    flux workflow run blog_post_writer_ollama '{
        "topic": "Quantum Computing Explained",
        "model": "qwen2.5:0.5b"
    }'
"""

from __future__ import annotations

from typing import Any

from flux import ExecutionContext, workflow
from flux.tasks.ai import agent


@workflow
async def blog_post_writer_ollama(ctx: ExecutionContext[dict[str, Any]]):
    """
    A blog post writer using Flux's agent() task primitive.

    Three agents run in sequence, each building on the previous output:
    1. researcher — produces structured research findings
    2. writer — transforms research into a blog post draft
    3. editor — polishes the draft for publication

    Input format:
    {
        "topic": "The Future of AI Agents",
        "model": "llama3",
        "ollama_url": "http://localhost:11434"
    }

    Returns:
        Dictionary with topic, blog post content, word count, and execution ID
    """
    input_data = ctx.input or {}

    topic = input_data.get("topic")
    if not topic:
        return {
            "error": "Missing required parameter 'topic'",
            "execution_id": ctx.execution_id,
        }

    researcher = await agent(
        "You are an experienced research analyst. Research topics thoroughly and produce "
        "structured summaries with key findings, trends, perspectives, examples, and future outlook. "
        "Organize your findings with clear headings and bullet points.",
        model="ollama/llama3",
        name="researcher",
    )

    writer = await agent(
        "You are a skilled content writer. Transform research into compelling blog posts "
        "with a title (as a markdown heading), an engaging introduction, well-organized body "
        "sections with clear headings, and a strong conclusion. Target 800-1200 words.",
        model="ollama/llama3",
        name="writer",
    )

    editor = await agent(
        "You are an experienced content editor. Polish drafts for clarity, grammar, flow, "
        "tone consistency, and argument strength. Return only the final polished post — "
        "no editorial notes.",
        model="ollama/llama3",
        name="editor",
    )

    research = await researcher(f"Research this topic: {topic}")
    draft = await writer(f"Write a blog post about: {topic}", context=research)
    final_post = await editor(f"Edit this blog post about '{topic}'", context=draft)

    lines = final_post.strip().splitlines()
    title = lines[0].strip().lstrip("#").strip() if lines else topic
    word_count = len(final_post.split())

    return {
        "topic": topic,
        "title": title,
        "blog_post": final_post.strip(),
        "word_count": word_count,
        "model": input_data.get("model", "llama3"),
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":  # pragma: no cover
    topic = "The Future of AI Agents in Software Development"

    try:
        print("=" * 80)
        print("Blog Post Writer Demo (Flux agent() + Ollama)")
        print(f"Topic: {topic}")
        print("=" * 80 + "\n")

        print("Running pipeline (Research -> Write -> Edit)...\n")
        result = blog_post_writer_ollama.run({"topic": topic})

        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        output = result.output
        print(f"Title: {output.get('title')}")
        print(f"Word count: {output.get('word_count')}")
        print(f"Model: {output.get('model')}")
        print(f"Execution ID: {output.get('execution_id')}\n")
        print("-" * 80)
        print(output.get("blog_post", ""))
        print("-" * 80)

    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure:")
        print("1. Ollama is running: ollama serve")
        print("2. Model is pulled: ollama pull llama3")
