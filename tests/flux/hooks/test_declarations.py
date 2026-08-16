"""Unit tests for the ``hook.run(...)`` declarative factory and the
workflow scope-confinement check it shares with AST extraction."""

from __future__ import annotations

import pytest

from flux.hooks.declarations import hook, validate_workflow_scope


class TestHookRun:
    def test_returns_a_plain_json_shaped_dict(self):
        spec = hook.run(
            on="task:release:*:promote_prod:awaiting_approval",
            workflow="ops/notify_slack",
            principal="notifier",
        )

        assert spec == {
            "on": "task:release:*:promote_prod:awaiting_approval",
            "workflow": "ops/notify_slack",
            "principal": "notifier",
            "name": None,
            "max_attempts": 5,
        }

    def test_carries_an_explicit_name_and_max_attempts(self):
        spec = hook.run(
            on="execution:*:*:failed",
            workflow="ops/incident",
            principal="notifier",
            name="my-hook",
            max_attempts=3,
        )

        assert spec["name"] == "my-hook"
        assert spec["max_attempts"] == 3

    def test_principal_is_required(self):
        with pytest.raises(TypeError):
            hook.run(on="execution:*:*:failed", workflow="ops/incident")  # type: ignore[call-arg]

    def test_rejects_a_malformed_selector(self):
        with pytest.raises(ValueError, match="selector"):
            hook.run(on="not-a-selector", workflow="ops/incident", principal="notifier")

    def test_rejects_a_non_positive_max_attempts(self):
        with pytest.raises(ValueError, match="max_attempts"):
            hook.run(
                on="execution:*:*:failed",
                workflow="ops/incident",
                principal="notifier",
                max_attempts=0,
            )


class TestValidateWorkflowScope:
    def test_a_selector_naming_the_declaring_workflow_is_fine(self):
        validate_workflow_scope("execution:release:pipeline:failed", "release", "pipeline")
        validate_workflow_scope(
            "task:release:pipeline:promote_prod:awaiting_approval",
            "release",
            "pipeline",
        )

    def test_a_selector_naming_a_different_workflow_is_rejected(self):
        with pytest.raises(ValueError, match="release/pipeline"):
            validate_workflow_scope("execution:release:other_workflow:failed", "release", "pipeline")

    def test_a_selector_naming_a_different_namespace_is_rejected(self):
        with pytest.raises(ValueError, match="release/pipeline"):
            validate_workflow_scope("execution:ops:pipeline:failed", "release", "pipeline")

    def test_a_wildcard_namespace_is_rejected(self):
        """A workflow may observe itself, never the fleet -- a wildcard in
        the namespace/name position is exactly the fleet-wide subscription
        path 1 exists for."""
        with pytest.raises(ValueError):
            validate_workflow_scope("execution:*:pipeline:failed", "release", "pipeline")

    def test_a_too_short_selector_is_rejected(self):
        with pytest.raises(ValueError):
            validate_workflow_scope("execution:*", "release", "pipeline")
