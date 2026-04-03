from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from flux.tasks.call import call

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
    if count is not None and int(count) >= max_failures:
        logger.warning(
            "Dream skipped: %d consecutive failures (max: %d)",
            count,
            max_failures,
        )
        return False
    return True


async def increment_failure_counter(memory: LongTermMemory) -> None:
    count = await memory.recall("_dream:failures")
    await memory.memorize("_dream:failures", int(count or 0) + 1)


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
