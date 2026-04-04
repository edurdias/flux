from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from flux.task import task
from flux.tasks.call import call
from flux.workflow import workflow

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from flux.tasks.ai.memory.long_term_memory import LongTermMemory

logger = logging.getLogger("flux.dreaming")


def dream(
    memory: LongTermMemory,
    execution_id: str,
    *,
    workflow: str = "agent_dream",
) -> Callable[[str, Any], Awaitable[None]]:
    """Return an async hook that fires a dream workflow."""
    scope = memory.scope

    async def _hook(agent_id: str, value: Any) -> None:
        try:
            await call(
                workflow,
                {
                    "execution_id": execution_id,
                    "agent": agent_id,
                    "scope": scope,
                },
                mode="async",
            )
        except Exception:
            logger.warning("Dream workflow submission failed", exc_info=True)

    return _hook


async def check_failure_gate(memory: LongTermMemory, max_failures: int = 3) -> bool:
    count = await memory.recall("_dream:failures")
    try:
        if count is not None and int(count) >= max_failures:
            logger.warning(
                "Dream skipped: %d consecutive failures (max: %d)",
                int(count),
                max_failures,
            )
            return False
    except (ValueError, TypeError):
        await memory.memorize("_dream:failures", 0)
    return True


async def increment_failure_counter(memory: LongTermMemory) -> None:
    count = await memory.recall("_dream:failures")
    try:
        await memory.memorize("_dream:failures", int(count or 0) + 1)
    except (ValueError, TypeError):
        await memory.memorize("_dream:failures", 1)


async def reset_failure_counter(memory: LongTermMemory) -> None:
    await memory.memorize("_dream:failures", 0)


ORIENT_PROMPT = (
    "You are performing memory consolidation. Your task is to understand the current "
    "state of the agent's long-term memory.\n\n"
    "Use `list_memory_keys` to see all stored keys, then use `recall_memory` to read "
    "the contents of each key. Build a mental map of what facts are stored, how they "
    "are organized, and identify any obvious issues (duplicates, contradictions, stale entries).\n\n"
    "Produce a brief orientation report summarizing:\n"
    "- Total number of memory entries\n"
    "- Key topics/categories covered\n"
    "- Any obvious issues you notice"
)

GATHER_SIGNAL_PROMPT = (
    "You are scanning execution events for high-value signals worth persisting to memory.\n\n"
    "Focus on these signal types:\n"
    "- **Corrections**: Where the user or agent reversed or amended a prior statement\n"
    "- **Decisions**: Explicit choices (technology selections, configuration changes, approach pivots)\n"
    "- **Repeated patterns**: Facts or entities referenced across 3+ distinct events\n"
    "- **Staleness indicators**: Tool calls that returned errors for entities that may exist in memory\n\n"
    "Do NOT read every event in detail. Scan for patterns and extract only high-value signals.\n\n"
    "Produce a signal report listing each signal with its type and a brief description."
)

CONSOLIDATE_PROMPT = (
    "You are consolidating the agent's long-term memory using signals from a recent execution.\n\n"
    "Rules:\n"
    "1. **Merge duplicates** — if multiple facts express the same information, use `store_memory` "
    "with a combined version and `forget_memory` on redundant keys.\n"
    "2. **Resolve contradictions** — when two facts conflict, keep the one consistent with the most "
    "recent execution events. `forget_memory` the outdated fact.\n"
    "3. **Convert temporal references** — replace relative time expressions (yesterday, last week) "
    "with absolute dates.\n"
    "4. **Enrich with signals** — corrections and decisions from the signal report should be stored "
    "as new facts via `store_memory`.\n"
    "5. **Preserve provenance** — when storing or updating a fact, include the execution_id in the "
    "value so the origin is traceable.\n\n"
    "Use `recall_memory`, `store_memory`, `forget_memory`, and `list_memory_keys` to read and "
    "modify memory."
)

PRUNE_PROMPT = (
    "You are pruning and indexing the agent's long-term memory after consolidation.\n\n"
    "Rules:\n"
    "1. **Remove stale entries** — facts referencing deleted files, removed endpoints, or changed "
    "APIs that are no longer valid.\n"
    "2. **Demote verbose entries** — if a memory value is excessively long, summarize it.\n"
    "3. **Cap total entries** — if the total number of memory keys exceeds 100, remove the least "
    "important entries to bring it under the cap.\n"
    "4. **Verify consistency** — ensure no remaining contradictions exist.\n\n"
    "Use `list_memory_keys`, `recall_memory`, `forget_memory`, and `store_memory` as needed.\n\n"
    "Produce a brief summary of what changed: entries before, entries after, what was pruned."
)


@task
async def load_execution_events(execution_id: str) -> str:
    """Fetch execution events via the Flux client."""

    from flux.client import FluxClient
    from flux.config import Configuration

    settings = Configuration.get().settings
    server_url = settings.workers.server_url

    async with FluxClient(server_url) as client:
        data = await client.get_execution(execution_id, detailed=True)

    events = data.get("events", [])
    summary_lines = []
    for event in events:
        name = event.get("name", "unknown")
        event_type = event.get("type", "unknown")
        value = event.get("value")
        value_preview = str(value)[:200] if value is not None else ""
        summary_lines.append(f"[{event_type}] {name}: {value_preview}")

    return "\n".join(summary_lines)


@workflow
async def agent_dream(ctx):
    """Memory consolidation workflow — four-phase dream pipeline."""
    from flux.tasks.ai import agent
    from flux.tasks.ai.memory import long_term_memory, sqlite

    input_data = ctx.input or {}
    execution_id = input_data.get("execution_id")
    agent_id = input_data.get("agent")
    scope = input_data.get("scope")

    if not execution_id or not agent_id or not scope:
        return {
            "status": "failed",
            "error": "Missing required input: execution_id, agent, and scope are all required",
        }

    model = input_data.get("model", "ollama/llama3.2")

    provider = sqlite("memory.db")
    memory = long_term_memory(provider=provider, agent=agent_id, scope=scope)

    if not await check_failure_gate(memory):
        return {"status": "skipped", "reason": "max consecutive failures reached"}

    try:
        events_summary = await load_execution_events(execution_id)

        ltm_tools = memory.as_tools()

        orient_agent = await agent(
            ORIENT_PROMPT,
            model=model,
            name="dream_orient",
            tools=ltm_tools,
            long_term_memory=memory,
            max_tool_calls=20,
            stream=False,
        )
        orientation = await orient_agent("Review the current memory state.")

        signal_agent = await agent(
            GATHER_SIGNAL_PROMPT,
            model=model,
            name="dream_gather_signal",
            tools=ltm_tools,
            max_tool_calls=10,
            stream=False,
        )
        signal_report = await signal_agent(
            f"Scan these execution events for signals:\n\n{events_summary}"
            f"\n\nOrientation report:\n{orientation}",
        )

        consolidate_agent = await agent(
            CONSOLIDATE_PROMPT,
            model=model,
            name="dream_consolidate",
            tools=ltm_tools,
            long_term_memory=memory,
            max_tool_calls=30,
            stream=False,
        )
        await consolidate_agent(
            f"Signal report:\n{signal_report}\n\nOrientation:\n{orientation}"
            f"\n\nExecution ID for provenance: {execution_id}",
        )

        prune_agent = await agent(
            PRUNE_PROMPT,
            model=model,
            name="dream_prune",
            tools=ltm_tools,
            long_term_memory=memory,
            max_tool_calls=20,
            stream=False,
        )
        summary = await prune_agent("Review and prune the memory. Report what changed.")

        await reset_failure_counter(memory)
        return {"status": "completed", "summary": summary}

    except Exception as e:
        await increment_failure_counter(memory)
        logger.error("Dream workflow failed: %s", e, exc_info=True)
        return {"status": "failed", "error": str(e)}
