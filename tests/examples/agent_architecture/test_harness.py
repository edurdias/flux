from __future__ import annotations

import pytest

from examples.agent_architecture.harness import (
    SECRET_NAME,
    _quote_calls,
    market_briefing,
    reset_quote_source,
)
from flux.secret_managers import SecretManager


@pytest.fixture(autouse=True)
def seed_harness():
    SecretManager.current().save(SECRET_NAME, "test-signing-token")
    reset_quote_source()


def test_internal_briefing_runs_unattended():
    """Retry absorbs the transient failure, fallback covers the dead feed,
    the secret is injected, and no approval gate fires for internal."""
    ctx = market_briefing.run({"symbol": "FLUX", "audience": "internal"})

    assert ctx.has_finished and ctx.has_succeeded
    assert ctx.output["published_to"] == "internal"

    report = ctx.output["report"]
    # First call failed, retry succeeded — the tool surface hid the flake.
    assert _quote_calls["FLUX"] == 2
    assert report["quote"]["source"] == "live"
    # Live news is down; the fallback served cached headlines.
    assert report["headlines"] == ["FLUX: cached headline (news feed unavailable)"]
    # The secret was injected and only a derived signature was stored.
    assert report["signature"] == "hmac-sim-18-FLUX"
    assert "test-signing-token" not in str(ctx.events)


def test_external_briefing_pauses_for_approval():
    ctx = market_briefing.run({"symbol": "ACME", "audience": "external"})
    assert ctx.is_paused
    assert not ctx.has_finished


def test_external_briefing_completes_after_approval():
    from flux.approvals import ApprovalManager
    from flux.unit_of_work import UnitOfWork

    ctx = market_briefing.run({"symbol": "WIDG", "audience": "external"})
    assert ctx.is_paused

    mgr = ApprovalManager()
    pending = mgr.list(execution_id=ctx.execution_id)
    assert len(pending) == 1
    row = pending[0]

    with UnitOfWork() as uow:
        mgr.decide(
            row.execution_id,
            row.task_call_id,
            approver_subject="alice",
            approver_provider="oidc",
            approved=True,
            reason="lgtm",
            uow=uow,
        )
        uow.commit()

    resumed = market_briefing.run(execution_id=ctx.execution_id)
    assert resumed.has_succeeded
    assert resumed.output["published_to"] == "external"
