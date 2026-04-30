"""E2E tests — config and agent management."""

from __future__ import annotations


def test_config_lifecycle(cli):
    cli.config_set("e2e_config_key", "test_value")

    v = cli.config_get("e2e_config_key")
    assert v.get("value") == "test_value" or "e2e_config_key" in str(v)

    listing = cli.config_list()
    names = listing.get("configs", listing) if isinstance(listing, dict) else listing
    assert "e2e_config_key" in str(names)

    cli.config_remove("e2e_config_key")


def test_config_update(cli):
    cli.config_set("e2e_config_update", "original")
    v = cli.config_get("e2e_config_update")
    assert "original" in str(v)

    cli.config_set("e2e_config_update", "updated")
    v = cli.config_get("e2e_config_update")
    assert "updated" in str(v)

    cli.config_remove("e2e_config_update")


def test_agent_lifecycle(cli):
    cli.agent_create(
        "e2e_test_agent",
        model="openai/gpt-4o",
        system_prompt="You are a test agent.",
    )

    listing = cli.agent_list()
    agent_names = str(listing)
    assert "e2e_test_agent" in agent_names

    detail = cli.agent_show("e2e_test_agent")
    assert detail.get("name") == "e2e_test_agent"
    assert detail.get("model") == "openai/gpt-4o"
    assert "test agent" in detail.get("system_prompt", "").lower()

    cli.agent_update("e2e_test_agent", description="Updated description")
    detail = cli.agent_show("e2e_test_agent")
    assert detail.get("description") == "Updated description"

    cli.agent_delete("e2e_test_agent")

    listing = cli.agent_list()
    assert "e2e_test_agent" not in str(listing)


def test_agent_with_options(cli):
    cli.agent_create(
        "e2e_options_agent",
        model="anthropic/claude-sonnet-4-20250514",
        system_prompt="You are a coding assistant.",
        description="Coding helper",
        planning=True,
        max_tool_calls=20,
        reasoning_effort="high",
    )

    detail = cli.agent_show("e2e_options_agent")
    assert detail.get("planning") is True
    assert detail.get("max_tool_calls") == 20
    assert detail.get("reasoning_effort") == "high"
    assert detail.get("description") == "Coding helper"

    cli.agent_delete("e2e_options_agent")


def test_agent_config_storage(cli):
    """Agent definitions can be stored and retrieved as config entries."""
    import json

    agent_def = {
        "model": "openai/gpt-4o",
        "system_prompt": "You are helpful.",
        "planning": False,
    }
    cli.config_set("agent:e2e_config_agent", json.dumps(agent_def))

    v = cli.config_get("agent:e2e_config_agent")
    value = v.get("value", v)
    parsed = json.loads(value) if isinstance(value, str) else value
    assert parsed["model"] == "openai/gpt-4o"

    cli.config_remove("agent:e2e_config_agent")
