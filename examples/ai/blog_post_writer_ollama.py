"""
Blog Post Writer using Ollama (Local LLM).

This example demonstrates a sequential multi-agent writing pipeline using Flux tasks
with direct Ollama SDK calls. Three specialized tasks collaborate in sequence:
1. Research — researches the topic and produces structured findings
2. Write — transforms research into an engaging blog post draft
3. Edit — reviews and polishes the draft for publication

Each stage is a separate Flux task with independent retry boundaries.
No external agent framework is needed — Flux orchestrates the pipeline directly.

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

from ollama import AsyncClient

from flux import ExecutionContext, task, workflow


RESEARCHER_PROMPT = """You are an experienced research analyst. Your task is to research a topic thoroughly and produce a structured summary of key findings that a writer can use to craft a blog post.

Provide:
- Key findings and important facts
- Current trends and developments
- Different perspectives and viewpoints
- Notable examples or case studies
- Potential implications and future outlook

Organize your findings with clear headings and bullet points."""

WRITER_PROMPT = """You are a skilled content writer who transforms research into compelling blog posts. You make technical topics accessible and engaging, with clear structure.

Write a blog post that includes:
- A compelling title (as a markdown heading)
- An attention-grabbing introduction
- Well-organized body sections with clear headings
- A strong conclusion with key takeaways

Target approximately 800 to 1200 words. Use the research findings provided as your source material."""

EDITOR_PROMPT = """You are an experienced content editor. Review and refine the blog post for:
- Clarity and readability
- Grammar, spelling, and punctuation
- Logical flow between sections
- Consistent tone and style
- Strength of arguments and supporting evidence

Return the final polished version of the blog post, with all edits applied. Do not include editorial notes — return only the finished post."""


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=120)
async def research_topic(
    topic: str,
    model: str,
    ollama_url: str,
) -> str:
    """Research a topic and produce structured findings."""
    try:
        client = AsyncClient(host=ollama_url)
        response = await client.chat(
            model=model,
            messages=[
                {"role": "system", "content": RESEARCHER_PROMPT},
                {"role": "user", "content": f"Research this topic: {topic}"},
            ],
        )
        return response["message"]["content"]
    except Exception as e:
        raise RuntimeError(
            f"Research failed: {str(e)}. "
            "Make sure Ollama is running (ollama serve) and the model is available.",
        ) from e


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=120)
async def write_blog_post(
    topic: str,
    research: str,
    model: str,
    ollama_url: str,
) -> str:
    """Write a blog post based on research findings."""
    try:
        client = AsyncClient(host=ollama_url)
        response = await client.chat(
            model=model,
            messages=[
                {"role": "system", "content": WRITER_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Write a blog post about: {topic}\n\n"
                        f"Use these research findings:\n\n{research}"
                    ),
                },
            ],
        )
        return response["message"]["content"]
    except Exception as e:
        raise RuntimeError(
            f"Writing failed: {str(e)}. "
            "Make sure Ollama is running (ollama serve) and the model is available.",
        ) from e


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=120)
async def edit_blog_post(
    topic: str,
    draft: str,
    model: str,
    ollama_url: str,
) -> str:
    """Edit and polish a blog post draft."""
    try:
        client = AsyncClient(host=ollama_url)
        response = await client.chat(
            model=model,
            messages=[
                {"role": "system", "content": EDITOR_PROMPT},
                {
                    "role": "user",
                    "content": (f"Edit and polish this blog post about '{topic}':\n\n{draft}"),
                },
            ],
        )
        return response["message"]["content"]
    except Exception as e:
        raise RuntimeError(
            f"Editing failed: {str(e)}. "
            "Make sure Ollama is running (ollama serve) and the model is available.",
        ) from e


@workflow.with_options(name="blog_post_writer_ollama")
async def blog_post_writer_ollama(ctx: ExecutionContext[dict[str, Any]]):
    """
    A blog post writer using a sequential Flux task pipeline with direct Ollama calls.

    Three tasks run in sequence, each building on the previous output:
    1. research_topic — produces structured research findings
    2. write_blog_post — transforms research into a blog post draft
    3. edit_blog_post — polishes the draft for publication

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

    model = input_data.get("model", "llama3")
    ollama_url = input_data.get("ollama_url", "http://localhost:11434")

    research = await research_topic(topic, model, ollama_url)
    draft = await write_blog_post(topic, research, model, ollama_url)
    final_post = await edit_blog_post(topic, draft, model, ollama_url)

    lines = final_post.strip().splitlines()
    title = lines[0].strip().lstrip("#").strip() if lines else topic
    word_count = len(final_post.split())

    return {
        "topic": topic,
        "title": title,
        "blog_post": final_post.strip(),
        "word_count": word_count,
        "model": model,
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":  # pragma: no cover
    topic = "The Future of AI Agents in Software Development"

    try:
        print("=" * 80)
        print("Blog Post Writer Demo (Pure Flux + Ollama)")
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
