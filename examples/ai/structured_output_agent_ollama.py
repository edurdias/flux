"""
Structured Output Agent using Flux agent() + Ollama.

This example demonstrates the agent() primitive with structured output — the agent
returns typed Pydantic models instead of raw strings, enabling reliable data extraction
and type-safe pipelines between agents.

Three use cases:
1. Data extraction — extract structured information from unstructured text
2. Classification — categorize input with typed labels and confidence scores
3. Typed pipeline — chain agents where the first returns structured data consumed by the second

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model: ollama pull llama3
    3. Start Ollama service: ollama serve

Usage:
    flux workflow run structured_output_demo '{"text": "John Smith is a 35 year old software engineer at Google in Mountain View."}'
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from flux import ExecutionContext, workflow
from flux.tasks.ai import agent


class PersonInfo(BaseModel):
    name: str
    age: int | None
    occupation: str | None
    company: str | None
    location: str | None


class SentimentResult(BaseModel):
    sentiment: str
    confidence: float
    reasoning: str


class BlogOutline(BaseModel):
    title: str
    sections: list[str]
    target_audience: str
    estimated_word_count: int


@workflow
async def structured_output_demo(ctx: ExecutionContext[dict[str, Any]]):
    """
    Demonstrates structured output with agent() in three use cases.

    Input format:
    {
        "text": "Text to analyze"
    }
    """
    input_data = ctx.input or {}
    text = input_data.get(
        "text",
        "Marie Curie was a physicist and chemist who conducted pioneering research on radioactivity.",
    )

    extractor = await agent(
        "You are a data extraction specialist. Extract structured information from text. "
        'Return a JSON object with fields: "name" (string), "age" (integer or null), '
        '"occupation" (string or null), "company" (string or null), "location" (string or null).',
        model="ollama/llama3",
        name="extractor",
        response_format=PersonInfo,
    )

    classifier = await agent(
        "You are a sentiment analysis specialist. Analyze the sentiment of the given text. "
        'Return a JSON object with fields: "sentiment" (one of "positive", "negative", "neutral"), '
        '"confidence" (float 0.0 to 1.0), "reasoning" (brief explanation).',
        model="ollama/llama3",
        name="classifier",
        response_format=SentimentResult,
    )

    planner = await agent(
        "You are a content planning specialist. Create a structured blog outline. "
        'Return a JSON object with fields: "title" (string), "sections" (list of section heading strings), '
        '"target_audience" (string), "estimated_word_count" (integer).',
        model="ollama/llama3",
        name="planner",
        response_format=BlogOutline,
    )

    writer = await agent(
        "You are a content writer. Write a blog post based on the outline provided.",
        model="ollama/llama3",
        name="writer",
    )

    # 1. Data extraction — returns a PersonInfo model
    person = await extractor(f"Extract person information from: {text}")

    # 2. Sentiment classification — returns a SentimentResult model
    sentiment = await classifier(f"Analyze the sentiment of: {text}")

    # 3. Typed pipeline — planner returns BlogOutline, writer uses it as context
    outline = await planner(
        f"Create a blog outline about: {person.name if isinstance(person, PersonInfo) else text}",
    )

    outline_text = (
        (
            f"Title: {outline.title}\n"
            f"Sections: {', '.join(outline.sections)}\n"
            f"Target audience: {outline.target_audience}\n"
            f"Estimated words: {outline.estimated_word_count}"
        )
        if isinstance(outline, BlogOutline)
        else str(outline)
    )

    blog_post = await writer("Write a blog post following this outline:", context=outline_text)

    return {
        "extraction": person.model_dump() if isinstance(person, PersonInfo) else str(person),
        "sentiment": sentiment.model_dump()
        if isinstance(sentiment, SentimentResult)
        else str(sentiment),
        "outline": outline.model_dump() if isinstance(outline, BlogOutline) else str(outline),
        "blog_post_preview": blog_post[:500] + "..." if len(blog_post) > 500 else blog_post,
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":  # pragma: no cover
    import json

    text = "John Smith is a 35 year old software engineer at Google in Mountain View. He loves his job and finds it very rewarding."

    try:
        print("=" * 80)
        print("Structured Output Agent Demo")
        print("=" * 80 + "\n")

        result = structured_output_demo.run({"text": text})

        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        output = result.output

        print("1. DATA EXTRACTION (PersonInfo)")
        print(f"   {json.dumps(output['extraction'], indent=2)}\n")

        print("2. SENTIMENT ANALYSIS (SentimentResult)")
        print(f"   {json.dumps(output['sentiment'], indent=2)}\n")

        print("3. CONTENT PLANNING (BlogOutline)")
        print(f"   {json.dumps(output['outline'], indent=2)}\n")

        print("4. TYPED PIPELINE (Outline -> Writer)")
        print(f"   {output['blog_post_preview']}\n")

        print("=" * 80)

    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure:")
        print("1. Ollama is running: ollama serve")
        print("2. Model is pulled: ollama pull llama3")
