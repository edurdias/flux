"""
Blog Post Writer using CrewAI + Ollama (Local LLM).

This example demonstrates CrewAI's signature pattern — role-based sequential multi-agent
collaboration — integrated with Flux workflow orchestration.

Three specialized CrewAI agents collaborate in a sequential pipeline:
1. Research Analyst — researches the topic and produces structured findings
2. Content Writer — transforms research into an engaging blog post draft
3. Content Editor — reviews and polishes the draft for publication

CrewAI handles:
- Role-based agents with specialized roles, goals, and backstories
- Sequential process where each task builds on the prior agent's output
- Input interpolation via {topic} placeholders in task descriptions

Flux handles:
- Wrapping the crew execution as a task with retry and timeout
- Workflow scheduling and durability
- Worker distribution for heavy LLM workloads

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model: ollama pull llama3
    3. Start Ollama service: ollama serve
    4. Install dependencies: pip install crewai litellm

Usage:
    # Write a blog post about a topic
    flux workflow run blog_post_writer_crewai '{"topic": "The Future of AI Agents"}'

    # Use a different model
    flux workflow run blog_post_writer_crewai '{
        "topic": "Quantum Computing Explained",
        "model": "llama3",
        "ollama_url": "http://localhost:11434"
    }'
"""

from __future__ import annotations

from typing import Any

from crewai import Agent, Crew, LLM, Process, Task

from flux import ExecutionContext, task, workflow


@task.with_options(retry_max_attempts=3, retry_delay=2, retry_backoff=2, timeout=300)
async def run_blog_crew(
    topic: str,
    model: str,
    ollama_url: str,
) -> str:
    """Execute the CrewAI blog post pipeline and return the raw output."""
    try:
        llm = LLM(model=f"ollama/{model}", base_url=ollama_url)

        researcher = Agent(
            role="Research Analyst",
            goal=(
                "Research the given topic thoroughly and identify key points, "
                "trends, and insights that will form the foundation of a blog post"
            ),
            backstory=(
                "You are an experienced research analyst with a talent for finding "
                "relevant information and identifying the most important aspects of "
                "any topic. You excel at organizing findings into clear, structured "
                "summaries that others can build upon."
            ),
            llm=llm,
            verbose=False,
        )

        writer = Agent(
            role="Content Writer",
            goal=(
                "Write engaging, informative blog posts based on research findings "
                "that captivate readers and clearly communicate complex ideas"
            ),
            backstory=(
                "You are a skilled content writer who transforms research into "
                "compelling narratives. You have a knack for making technical topics "
                "accessible and engaging, always maintaining a clear structure with "
                "strong introductions and memorable conclusions."
            ),
            llm=llm,
            verbose=False,
        )

        editor = Agent(
            role="Content Editor",
            goal=(
                "Review and refine blog posts for clarity, accuracy, and engagement, "
                "ensuring the final piece is polished and ready for publication"
            ),
            backstory=(
                "You are an experienced editor with an eye for detail. You improve "
                "flow, fix grammatical issues, strengthen weak arguments, and ensure "
                "consistency in tone and style. Your edits elevate good writing into "
                "great writing."
            ),
            llm=llm,
            verbose=False,
        )

        research_task = Task(
            description=(
                "Research the topic: {topic}\n\n"
                "Provide:\n"
                "- Key findings and important facts\n"
                "- Current trends and developments\n"
                "- Different perspectives and viewpoints\n"
                "- Notable examples or case studies\n"
                "- Potential implications and future outlook"
            ),
            expected_output=(
                "A structured research summary with key findings, supporting evidence, "
                "and organized sections that a writer can use to craft a blog post"
            ),
            agent=researcher,
        )

        writing_task = Task(
            description=(
                "Write a blog post about: {topic}\n\n"
                "Using the research findings provided, write an engaging and informative "
                "blog post that includes:\n"
                "- A compelling title\n"
                "- An attention-grabbing introduction\n"
                "- Well-organized body sections with clear headings\n"
                "- A strong conclusion with key takeaways"
            ),
            expected_output=(
                "A complete blog post with title, introduction, body sections with "
                "headings, and a conclusion — approximately 800 to 1200 words"
            ),
            agent=writer,
            context=[research_task],
        )

        editing_task = Task(
            description=(
                "Review and edit the blog post about: {topic}\n\n"
                "Refine the draft for:\n"
                "- Clarity and readability\n"
                "- Grammar, spelling, and punctuation\n"
                "- Logical flow between sections\n"
                "- Consistent tone and style\n"
                "- Strength of arguments and supporting evidence\n\n"
                "Return the final polished version of the blog post."
            ),
            expected_output=(
                "The final polished blog post ready for publication, with all edits "
                "applied and improvements incorporated"
            ),
            agent=editor,
            context=[writing_task],
        )

        crew = Crew(
            agents=[researcher, writer, editor],
            tasks=[research_task, writing_task, editing_task],
            process=Process.sequential,
            verbose=False,
        )

        result = crew.kickoff(inputs={"topic": topic})
        return result.raw

    except Exception as e:
        raise RuntimeError(
            f"CrewAI blog pipeline failed: {str(e)}. "
            "Make sure Ollama is running (ollama serve) and the model is available.",
        ) from e


@task
async def format_blog_output(topic: str, raw_output: str) -> dict[str, Any]:
    """Parse the crew output into a structured blog post result."""
    lines = raw_output.strip().splitlines()
    title = lines[0].strip().lstrip("#").strip() if lines else topic
    word_count = len(raw_output.split())

    return {
        "title": title,
        "content": raw_output.strip(),
        "word_count": word_count,
    }


@workflow.with_options(name="blog_post_writer_crewai")
async def blog_post_writer_crewai(ctx: ExecutionContext[dict[str, Any]]):
    """
    A blog post writer using CrewAI's sequential multi-agent pipeline.

    Three role-based agents (researcher, writer, editor) collaborate in sequence,
    each building on the previous agent's output to produce a polished blog post.

    Input format:
    {
        "topic": "The Future of AI Agents",              # Required: blog topic
        "model": "llama3",                               # Optional: Ollama model
        "ollama_url": "http://localhost:11434"            # Optional: Ollama server URL
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

    raw_output = await run_blog_crew(topic, model, ollama_url)
    blog_post = await format_blog_output(topic, raw_output)

    return {
        "topic": topic,
        "blog_post": blog_post["content"],
        "title": blog_post["title"],
        "word_count": blog_post["word_count"],
        "model": model,
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":  # pragma: no cover
    topic = "The Future of AI Agents in Software Development"

    try:
        print("=" * 80)
        print("CrewAI Blog Post Writer Demo")
        print(f"Topic: {topic}")
        print("=" * 80 + "\n")

        print("Running CrewAI pipeline (Researcher -> Writer -> Editor)...\n")
        result = blog_post_writer_crewai.run({"topic": topic})

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
        print("3. Dependencies are installed: pip install crewai")
