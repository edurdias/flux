from __future__ import annotations

import pytest

from flux.errors import HookNotFoundError
from flux.hooks.registry import HookRegistry
from flux.hooks.selectors import HookEvent


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

    def test_crud_invalidates_the_snapshot(self, isolated_db):
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
