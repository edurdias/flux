"""ConsoleWriteState's cache shape (#245).

The per-token write answer is a cache keyed by a live credential, held for
the life of the process. Two properties matter beyond the answer itself:
the raw Bearer token must not be what the map holds, and the map must not
grow without bound in api mode, where every request can carry a different
token.
"""

from __future__ import annotations

import pytest

from flux.agents.console.app import ConsoleWriteState


class _StubService:
    """Stands in for ConsoleService: records probes, answers can_write."""

    def __init__(self, can_write: bool = True):
        self.can_write = can_write
        self.probes: list[str] = []

    async def stop(self, execution_id: str, namespace: str, workflow_name: str):
        self.probes.append(execution_id)
        if self.can_write:
            return None
        raise AssertionError("unreachable: read-only stub raises via _probe patch")


@pytest.mark.asyncio
async def test_the_raw_token_is_never_held_in_the_cache():
    state = ConsoleWriteState()

    state.mark_forbidden("super-secret-token", "workflow:agents:agent_chat:run")

    held = repr(state.__dict__)
    assert "super-secret-token" not in held
    assert state.missing_permission("super-secret-token") == "workflow:agents:agent_chat:run"


@pytest.mark.asyncio
async def test_the_cache_is_bounded_by_max_entries():
    state = ConsoleWriteState(max_entries=4)

    for index in range(20):
        state.mark_forbidden(f"token-{index}", f"permission-{index}")

    assert len(state) == 4
    # The oldest entries are the ones evicted: an evicted token has no
    # remembered permission, a retained one still names its own.
    assert state.missing_permission("token-19") == "permission-19"
    assert state.missing_permission("token-0") is None


@pytest.mark.asyncio
async def test_a_forbidden_answer_stands_for_the_ttl_then_expires():
    now = {"t": 0.0}
    state = ConsoleWriteState(ttl_seconds=60.0, clock=lambda: now["t"])
    service = _StubService()

    state.mark_forbidden("token", "workflow:agents:agent_chat:run")
    now["t"] = 59.0
    # Inside the TTL the cached "no" stands -- no probe, never back to True.
    assert await state.resolve("token", service) is False
    assert service.probes == []

    # Past it the answer is re-resolved rather than remembered forever, so a
    # grant handed out mid-session is picked up.
    now["t"] = 61.0
    assert await state.resolve("token", service) is True
    assert len(service.probes) == 1
    assert state.missing_permission("token") is None


@pytest.mark.asyncio
async def test_distinct_tokens_keep_distinct_answers():
    state = ConsoleWriteState()
    service = _StubService()

    state.mark_forbidden("readonly-token", "workflow:agents:agent_chat:run")

    assert await state.resolve("readonly-token", service) is False
    assert await state.resolve("writer-token", service) is True
    assert state.missing_permission("readonly-token") == "workflow:agents:agent_chat:run"
    assert state.missing_permission("writer-token") is None


@pytest.mark.asyncio
async def test_a_later_denial_without_a_permission_keeps_the_probed_one():
    state = ConsoleWriteState()

    state.mark_forbidden("token", "workflow:agents:agent_chat:run")
    state.mark_forbidden("token")

    assert state.missing_permission("token") == "workflow:agents:agent_chat:run"
