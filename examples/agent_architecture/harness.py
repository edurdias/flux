"""Harness engineering — the machinery around the model.

The harness is everything that surrounds an agent so it can act safely:
tool definitions, execution control (retries, timeouts, fallbacks),
credential injection, approval gates, and a durable audit trail. In Flux,
tools are ordinary ``@task`` functions, so all of that machinery is
declared per-tool with ``task.with_options(...)`` instead of being
hand-rolled inside the agent code.

This example builds the tool surface for a market-briefing agent:

- ``fetch_quote``      — a flaky upstream API; the harness retries with
                         backoff instead of surfacing transient failures.
- ``fetch_news``       — a dependency that is down; the harness degrades
                         to a ``fallback`` instead of failing the run.
- ``sign_report``      — needs a credential; ``secret_requests`` injects
                         it at call time and keeps it out of the event log.
- ``publish_report``   — outward-facing; ``requires_approval`` pauses the
                         execution for a human decision when the audience
                         is external.

Every call and state transition is persisted as an ``ExecutionEvent``,
so the whole run is observable and resumable. Hand the same tasks to
``flux.tasks.ai.agent(tools=[...])`` and the agent inherits this harness
unchanged — see ``full_stack_agent_ollama.py``.

Usage:
    poetry run python examples/agent_architecture/harness.py
    # When the external publish pauses for approval:
    flux execution approve <execution_id> <task_call_id> --reason "lgtm"
"""

from __future__ import annotations

from typing import Any

from flux import ExecutionContext, task, workflow

SECRET_NAME = "reporting_signing_token"

# Simulated flaky upstream: the first call for each symbol fails with a
# transient error, the retry succeeds. Keyed by symbol so runs stay independent.
_quote_calls: dict[str, int] = {}


def reset_quote_source() -> None:
    _quote_calls.clear()


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=30)
async def fetch_quote(symbol: str) -> dict[str, Any]:
    """Fetch the latest quote for a symbol from a rate-limited upstream."""
    calls = _quote_calls.get(symbol, 0) + 1
    _quote_calls[symbol] = calls
    if calls < 2:
        raise ConnectionError(f"quote service throttled request (attempt {calls})")
    return {"symbol": symbol, "price": round(40.0 + len(symbol) * 2.5, 2), "source": "live"}


async def cached_headlines(symbol: str) -> list[str]:
    """Fallback: serve yesterday's cached headlines when the live feed is down."""
    return [f"{symbol}: cached headline (news feed unavailable)"]


@task.with_options(fallback=cached_headlines)
async def fetch_news(symbol: str) -> list[str]:
    """Fetch live headlines — permanently down in this simulation."""
    raise TimeoutError("news feed is not responding")


@task.with_options(secret_requests=[SECRET_NAME])
async def sign_report(
    report: dict[str, Any],
    secrets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sign the report with an injected credential.

    The raw token is delivered only to this call and never stored in the
    event log — only the derived signature is. The ``secrets`` kwarg is
    always injected by the task runtime when ``secret_requests`` is set;
    the ``None`` default only keeps the signature honest.
    """
    token = (secrets or {})[SECRET_NAME]
    return {**report, "signature": f"hmac-sim-{len(token)}-{report['quote']['symbol']}"}


@task.with_options(requires_approval=lambda audience, **_: audience == "external")
async def publish_report(*, report: dict[str, Any], audience: str) -> dict[str, Any]:
    """Publish the briefing. External publishes pause for a human decision."""
    return {"published_to": audience, "report": report}


@workflow
async def market_briefing(ctx: ExecutionContext[dict]):
    params = ctx.input or {}
    symbol = params.get("symbol", "FLUX")
    audience = params.get("audience", "internal")

    quote = await fetch_quote(symbol)
    headlines = await fetch_news(symbol)
    report = await sign_report({"quote": quote, "headlines": headlines})
    return await publish_report(report=report, audience=audience)


if __name__ == "__main__":  # pragma: no cover
    from flux.secret_managers import SecretManager

    SecretManager.current().save(SECRET_NAME, "example-signing-token")

    ctx = market_briefing.run({"symbol": "FLUX", "audience": "external"})
    if ctx.is_paused:
        print(f"Paused for approval. execution_id={ctx.execution_id}")
        print("Approve via: flux execution approvals --execution <id> --json")
    else:
        print(ctx.to_json())
