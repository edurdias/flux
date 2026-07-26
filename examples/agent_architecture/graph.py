"""Graph engineering — explicit workflow topology.

Graph engineering makes control flow a declared artifact instead of an
emergent property of agent behavior: nodes, edges, fan-out, joins, and
gates are all visible, validated up front, and deterministic.

This example routes a document through an explicit pipeline:

    START ─ ingest ─┬─ extract_keywords ─┐
                    │                    ├─ combine ─[quality gate]─ publish ─ END
                    └─ summarize ────────┘

- **Fan-out**: ``extract_keywords`` and ``summarize`` both depend only on
  ``ingest``, so the topology declares them as parallel branches.
- **Join**: ``combine`` runs only after *both* branches complete and
  receives both outputs.
- **Conditional edge**: the ``combine → publish`` edge carries a quality
  gate. When the gate rejects, ``publish`` never runs and the graph
  returns ``None`` — the workflow turns that into an explicit
  "not published" outcome instead of letting a low-quality document out.

``Graph.validate()`` (called on execution) rejects disconnected nodes and
cycles before anything runs. Each node action is a ``@task``, so every
node execution is checkpointed — a crash mid-graph resumes with completed
nodes replayed from the event log. For simple fan-out without declared
topology, see also ``flux.tasks.parallel`` and ``flux.tasks.pipeline``.

Usage:
    poetry run python examples/agent_architecture/graph.py
"""

from __future__ import annotations

from typing import Any

from flux import ExecutionContext, task, workflow
from flux.tasks import Graph

QUALITY_THRESHOLD = 0.8


@task
async def ingest(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize the raw input into the shape the branches expect."""
    text = raw.get("document", "")
    return {"text": text, "words": text.split()}


@task
async def extract_keywords(ingested: dict[str, Any]) -> list[str]:
    """Branch 1: pick the longest words as (simulated) keywords."""
    unique = {word.strip(".,:").lower() for word in ingested["words"]}
    return sorted(unique, key=len, reverse=True)[:3]


@task
async def summarize(ingested: dict[str, Any]) -> dict[str, Any]:
    """Branch 2: first-sentence summary plus a citation check."""
    text = ingested["text"]
    return {
        "summary": text.split(".")[0][:80],
        "cites_sources": "sources:" in text.lower(),
    }


@task
async def combine(keywords: list[str], summarized: dict[str, Any]) -> dict[str, Any]:
    """Join node: merges both branches and scores the result."""
    quality = 0.9 if summarized["cites_sources"] else 0.4
    return {
        "keywords": keywords,
        "summary": summarized["summary"],
        "quality": quality,
    }


async def quality_gate(combined: dict[str, Any]) -> bool:
    """Edge condition: only well-sourced documents may reach ``publish``."""
    return combined["quality"] >= QUALITY_THRESHOLD


@task
async def publish(combined: dict[str, Any]) -> dict[str, Any]:
    return {
        "digest": f"{combined['summary']} [keywords: {', '.join(combined['keywords'])}]",
        "quality": combined["quality"],
    }


@workflow
async def document_graph(ctx: ExecutionContext[dict]):
    pipeline = (
        Graph("document_pipeline")
        .add_node("ingest", ingest)
        .add_node("extract_keywords", extract_keywords)
        .add_node("summarize", summarize)
        .add_node("combine", combine)
        .add_node("publish", publish)
        .start_with("ingest")
        .add_edge("ingest", "extract_keywords")
        .add_edge("ingest", "summarize")
        .add_edge("extract_keywords", "combine")
        .add_edge("summarize", "combine")
        .add_edge("combine", "publish", condition=quality_gate)
        .end_with("publish")
    )

    result = await pipeline(ctx.input or {})
    if result is None:
        return {"published": False, "reason": "quality gate rejected the document"}
    return {"published": True, **result}


if __name__ == "__main__":  # pragma: no cover
    good = (
        "Flux persists every state transition as an execution event. "
        "Sources: flux/domain/events.py."
    )
    ctx = document_graph.run({"document": good})
    print(f"well-sourced document: {ctx.output}")

    ctx = document_graph.run({"document": "Trust me, this is fine."})
    print(f"unsourced document: {ctx.output}")
