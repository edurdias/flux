from __future__ import annotations

import time

import pytest

from flux.config import Configuration
from flux.errors import HookNotFoundError
from flux.hooks.registry import HookRegistry
from flux.hooks.selectors import HookEvent
from flux.models import HookModel, RepositoryFactory


def _event(key: str) -> HookEvent:
    domain = key.split(":", 1)[0]
    return HookEvent(
        domain=domain,
        key=key,
        execution_id="exec-1",
        workflow_namespace="release",
        workflow_name="pipeline",
        event_id="ev-1",
        type=key.rsplit(":", 1)[-1],
        task_name=None,
        task_call_id=None,
        value=None,
        occurred_at="2024-01-01T00:00:00+00:00",
    )


class TestRegistry:
    def test_matches_returns_only_hooks_whose_selector_fires(self, isolated_db):
        registry = HookRegistry.create()
        registry.create_hook(
            name="on-fail",
            selectors=["execution:*:*:failed"],
            workflow_ref="ops/incident",
            principal_id="p",
            owner_ref="admin",
        )
        registry.create_hook(
            name="on-pause",
            selectors=["execution:*:*:paused"],
            workflow_ref="ops/nudge",
            principal_id="p",
            owner_ref="admin",
        )

        matched = registry.matches(_event("execution:release:pipeline:failed"))

        assert [entry.name for entry in matched] == ["on-fail"]

    def test_a_hook_matches_once_even_when_two_selectors_fire(self, isolated_db):
        """Two selectors on one row are an OR, not a fan-out: one hook, one
        delivery."""
        registry = HookRegistry.create()
        registry.create_hook(
            name="broad",
            selectors=["execution:*", "execution:*:*:failed"],
            workflow_ref="ops/incident",
            principal_id="p",
            owner_ref="admin",
        )

        matched = registry.matches(_event("execution:release:pipeline:failed"))

        assert len(matched) == 1

    def test_disabled_hooks_never_match(self, isolated_db):
        registry = HookRegistry.create()
        registry.create_hook(
            name="off",
            selectors=["execution:*"],
            workflow_ref="ops/x",
            principal_id="p",
            owner_ref="admin",
        )
        registry.update_hook("off", enabled=False)

        assert registry.matches(_event("execution:a:b:failed")) == []

    def test_stale_snapshot_does_not_leak_into_the_next_test(self, isolated_db):
        """Regression for a real bug: the snapshot cache is module-level
        (every HookRegistry() accessor shares it), but isolated_db swaps in a
        fresh database per test without touching it. Deliberately leaves the
        cache warm and *non-empty* here so the next test -- which asserts
        has_any() is False against its own, genuinely empty database --
        would fail loudly if tests/conftest.py's autouse
        `_reset_hook_registry_cache` fixture didn't clear it in between."""
        registry = HookRegistry.create()
        registry.create_hook(
            name="leftover",
            selectors=["execution:*"],
            workflow_ref="ops/x",
            principal_id="p",
            owner_ref="admin",
        )

        assert registry.has_any() is True  # cache now warm and non-empty

    def test_crud_invalidates_the_snapshot(self, isolated_db):
        # The preceding test leaves a warm, non-empty cache on purpose (see
        # its docstring) -- this first assertion only holds if the autouse
        # reset fixture actually cleared it, so it reads this test's own
        # (genuinely empty) database rather than the previous test's cache.
        registry = HookRegistry.create()
        assert registry.has_any() is False

        registry.create_hook(
            name="h",
            selectors=["execution:*"],
            workflow_ref="ops/x",
            principal_id="p",
            owner_ref="admin",
        )

        assert registry.has_any() is True
        registry.delete_hook("h")
        assert registry.has_any() is False

    def test_snapshot_rebuilds_once_the_ttl_expires(self, isolated_db):
        """Bounds cross-replica staleness: invalidate() only ever runs in the
        process that performed the write, so a peer replica's CRUD is
        invisible to this cache until the TTL elapses. Simulate that peer
        write by inserting through the repository directly, bypassing
        HookRegistry (and therefore its invalidate() call) entirely."""
        Configuration.get().settings.hooks.snapshot_ttl_seconds = 0.05

        registry = HookRegistry.create()
        assert registry.has_any() is False  # cold read warms the cache

        repo = RepositoryFactory.create_repository()
        with repo.session() as session:
            session.add(
                HookModel(
                    name="peer-write",
                    selectors=["execution:*"],
                    workflow_ref="ops/x",
                    principal_id="p",
                    owner_type="user",
                    owner_ref="admin",
                ),
            )
            session.commit()

        # Still within the TTL: the cache hasn't been told about the write
        # above (no invalidate() call), so it keeps serving the stale answer.
        assert registry.has_any() is False

        time.sleep(0.1)  # past the 0.05s TTL

        assert registry.has_any() is True  # rebuilt, and sees the peer write

    def test_create_rejects_an_invalid_selector(self, isolated_db):
        with pytest.raises(ValueError):
            HookRegistry.create().create_hook(
                name="bad",
                selectors=["nope:*"],
                workflow_ref="ops/x",
                principal_id="p",
                owner_ref="admin",
            )

    def test_get_missing_hook_raises(self, isolated_db):
        with pytest.raises(HookNotFoundError):
            HookRegistry.create().get_hook("absent")
