"""All three layers nested: harness + loop + graph with a real LLM.

The other examples in this directory demonstrate each practice in
isolation, deterministically. This capstone shows how they nest in a real
agent, the way production systems compose them:

- **Harness** — the agent's tools are hardened ``@task`` functions:
  ``run_test_suite`` retries transient failures, and ``tag_release`` is
  gated with ``requires_approval=True`` so the outward-facing action
  always pauses for a human, no matter how confident the model is.
- **Loop** — ``agent()`` runs the call-model → run-tools → observe cycle
  with two explicit bounds: ``max_tool_calls`` caps tool iterations and a
  shared ``Budget`` caps token spend (checked before every LLM call,
  raising ``BudgetExceededError`` at the ceiling).
- **Graph / durability** — the agent runs *inside* a durable workflow:
  every LLM call and tool execution is a checkpointed task, so the
  execution can pause on the approval gate for days and resume by
  deterministic replay without re-spending tokens.

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model that supports tools: ollama pull llama3.2
    3. Start Ollama service: ollama serve

Usage:
    flux workflow run release_manager_agent '{"version": "2.1.0"}'
    # When the agent calls tag_release, the execution pauses:
    flux execution approve <execution_id> <task_call_id> --reason "ship it"
"""

from __future__ import annotations

from typing import Any

from flux import ExecutionContext, task, workflow
from flux.tasks.ai import Budget, agent


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=60)
async def run_test_suite(suite: str = "unit") -> str:
    """Run a test suite and report the results. Use before recommending any release."""
    results = {
        "unit": "412 passed, 0 failed, coverage 87%",
        "integration": "96 passed, 0 failed",
        "e2e": "31 passed, 1 flaky (retried green)",
    }
    return results.get(suite, f"unknown suite '{suite}' — available: {sorted(results)}")


@task
async def get_changelog(version: str) -> str:
    """Get the changelog entries staged for a version."""
    return (
        f"Changelog for {version}:\n"
        "- feat: dynamic routing policies for dispatch\n"
        "- fix: module cache collision across workflow versions\n"
        "- docs: agent architecture examples"
    )


@task.with_options(requires_approval=True)
async def tag_release(version: str, summary: str) -> dict[str, Any]:
    """Tag and publish the release. ALWAYS requires human approval before running."""
    return {"tag": f"v{version}", "summary": summary, "status": "published"}


@workflow
async def release_manager_agent(ctx: ExecutionContext[dict[str, Any]]):
    params = ctx.input or {}
    version = params.get("version", "0.1.0")
    model = params.get("model", "ollama/llama3.2")

    # Loop bounds: token ceiling shared by every LLM call in this run,
    # plus a cap on tool-call iterations before a final answer is forced.
    budget = Budget(max_tokens=32_000)

    release_manager = await agent(
        "You are a release manager. Verify the release is safe by running the "
        "unit and integration test suites and reviewing the changelog. Only if "
        "the evidence supports it, tag the release with a one-line summary. "
        "Report what you verified and what you shipped.",
        model=model,
        name="release_manager",
        tools=[run_test_suite, get_changelog, tag_release],
        max_tool_calls=6,
        budget=budget,
    )

    answer = await release_manager(f"Prepare and publish release {version}.")

    return {
        "version": version,
        "report": answer,
        "tokens_spent": budget.spent(),
    }


if __name__ == "__main__":  # pragma: no cover
    ctx = release_manager_agent.run({"version": "2.1.0"})
    if ctx.is_paused:
        print(f"Paused for release approval. execution_id={ctx.execution_id}")
        print("Approve via: flux execution approvals --execution <id> --json")
    elif ctx.has_failed:
        print(f"Failed: {ctx.output}")
        print("Make sure Ollama is running (ollama serve) and llama3.2 is pulled.")
    else:
        print(ctx.output)
