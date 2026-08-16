"""The approval_mode split: autonomy ceiling × approval routing (#146).

The invariant under test: routing never changes the ceiling, the ceiling
never changes routing, and every legacy ``approval_mode`` caller (and
stored agent row) behaves exactly as before.
"""

from __future__ import annotations

import warnings

import pytest

from flux.tasks.ai.approval_policy import resolve_approval_policy
from flux.tasks.ai.tool_executor import build_tools_preamble
from flux.task import task as task_decorator


class TestResolution:
    def test_unset_everything_is_todays_behavior(self):
        assert resolve_approval_policy() == ("default", "inline")

    def test_explicit_values_win(self):
        assert resolve_approval_policy(None, "strict", "inline") == ("strict", "inline")

    def test_legacy_autonomous_maps(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            assert resolve_approval_policy("autonomous") == ("autonomous", "inline")

    def test_legacy_default_maps(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            assert resolve_approval_policy("default") == ("default", "inline")

    def test_legacy_typo_still_means_default(self):
        """The historic silent behavior: any unknown approval_mode meant
        "default". Preserved (with a log warning) for compatibility."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            assert resolve_approval_policy("autonmous") == ("default", "inline")

    def test_legacy_use_warns_deprecation(self):
        with pytest.warns(DeprecationWarning, match="approval_mode is deprecated"):
            resolve_approval_policy("autonomous")

    def test_explicit_autonomy_beats_legacy(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            assert resolve_approval_policy("autonomous", "strict", None) == (
                "strict",
                "inline",
            )

    def test_invalid_autonomy_rejected(self):
        with pytest.raises(ValueError, match="autonomy must be"):
            resolve_approval_policy(None, "yolo", None)

    def test_invalid_routing_rejected(self):
        with pytest.raises(ValueError, match="approval_routing must be"):
            resolve_approval_policy(None, None, "carrier-pigeon")

    def test_notify_was_removed_not_left_as_a_silent_no_op(self):
        """notify was declared for #144, never wired to a delivery mechanism,
        and #144 closed as not-planned. It must now be rejected like any
        other invalid value rather than silently behaving like inline."""
        with pytest.raises(ValueError, match="approval_routing must be"):
            resolve_approval_policy(None, None, "notify")

    def test_routing_never_changes_ceiling(self):
        """The bug #146 exists to prevent: routing choice must not widen
        (or narrow) what the agent may do. inline is routing's only value,
        but the independence is still worth pinning explicitly."""
        for autonomy in ("strict", "default", "autonomous"):
            resolved_autonomy, resolved_routing = resolve_approval_policy(
                None,
                autonomy,
                "inline",
            )
            assert resolved_autonomy == autonomy
            assert resolved_routing == "inline"


def _tools():
    @task_decorator.with_options(requires_approval=True)
    async def gated(x: str) -> str:
        return x

    @task_decorator
    async def free(x: str) -> str:
        return x

    return [gated, free]


class TestPreambleDescribesCeilingOnly:
    def test_default_lists_declared_gates(self):
        preamble = build_tools_preamble(_tools(), autonomy="default")
        assert "Tools requiring approval: gated" in preamble

    def test_strict_lists_every_tool(self):
        preamble = build_tools_preamble(_tools(), autonomy="strict")
        assert "gated" in preamble.split("Tools requiring approval:")[1]
        assert "free" in preamble.split("Tools requiring approval:")[1]

    def test_autonomous_lists_none(self):
        preamble = build_tools_preamble(_tools(), autonomy="autonomous")
        assert "Tool Approval" not in preamble

    def test_routing_never_appears(self):
        """The system prompt must not leak routing configuration."""
        for autonomy in ("strict", "default", "autonomous"):
            preamble = build_tools_preamble(_tools(), autonomy=autonomy)
            assert "notify" not in preamble
            assert "inline" not in preamble
            assert "routing" not in preamble.lower()

    def test_legacy_approval_mode_still_accepted(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            preamble = build_tools_preamble(_tools(), approval_mode="autonomous")
        assert "Tool Approval" not in preamble


class TestAgentDefinitionSurface:
    def test_new_fields_validated(self):
        from flux.agents.types import AgentDefinition

        definition = AgentDefinition(
            name="a",
            model="openai/gpt-4o",
            system_prompt="hi",
            autonomy="strict",
            approval_routing="inline",
        )
        assert definition.autonomy == "strict"
        assert definition.approval_routing == "inline"

    def test_invalid_values_rejected(self):
        from flux.agents.types import AgentDefinition

        with pytest.raises(ValueError):
            AgentDefinition(name="a", model="openai/gpt-4o", system_prompt="hi", autonomy="yolo")
        with pytest.raises(ValueError):
            AgentDefinition(
                name="a",
                model="openai/gpt-4o",
                system_prompt="hi",
                approval_routing="pigeon",
            )

    def test_notify_definitions_are_rejected(self):
        """A definition file authored before notify was removed must fail
        loudly at load rather than silently keep 'notify'."""
        from flux.agents.types import AgentDefinition

        with pytest.raises(ValueError, match="approval_routing must be"):
            AgentDefinition(
                name="a",
                model="openai/gpt-4o",
                system_prompt="hi",
                approval_routing="notify",
            )

    def test_pre_split_definition_loads_unchanged(self):
        """A row written before #146 has approval_mode only; it must load
        with the new fields unset (deferring to the legacy mapping)."""
        from flux.agents.types import AgentDefinition

        definition = AgentDefinition(
            name="old",
            model="openai/gpt-4o",
            system_prompt="hi",
            approval_mode="autonomous",
        )
        assert definition.autonomy is None
        assert definition.approval_routing is None
        assert definition.approval_mode == "autonomous"
