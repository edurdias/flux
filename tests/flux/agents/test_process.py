"""Tests for AgentProcess."""

from __future__ import annotations

import pytest

from flux.agents.process import AgentProcess


def test_process_init():
    process = AgentProcess(
        agent_name="coder",
        server_url="http://localhost:8000",
        mode="terminal",
    )
    assert process.agent_name == "coder"
    assert process.mode == "terminal"


def test_process_invalid_mode():
    with pytest.raises(ValueError, match="mode"):
        AgentProcess(
            agent_name="coder",
            server_url="http://localhost:8000",
            mode="invalid",
        )


def test_process_with_session():
    process = AgentProcess(
        agent_name="coder",
        server_url="http://localhost:8000",
        mode="terminal",
        session_id="exec_123",
    )
    assert process.session_id == "exec_123"
