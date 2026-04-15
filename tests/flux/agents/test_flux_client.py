"""Tests for FluxClient."""

from __future__ import annotations

from flux.agents.flux_client import FluxClient


def test_client_init():
    client = FluxClient(server_url="http://localhost:8000", token="test-token")
    assert client.server_url == "http://localhost:8000"


def test_client_build_headers():
    client = FluxClient(server_url="http://localhost:8000", token="test-token")
    headers = client._build_headers()
    assert headers["Authorization"] == "Bearer test-token"
    assert headers["Content-Type"] == "application/json"


def test_client_no_token():
    client = FluxClient(server_url="http://localhost:8000")
    headers = client._build_headers()
    assert "Authorization" not in headers


def test_start_agent_url():
    client = FluxClient(server_url="http://localhost:8000")
    url = client._start_url("agents", "agent_chat")
    assert url == "http://localhost:8000/workflows/agents/agent_chat/run/stream"


def test_resume_url():
    client = FluxClient(server_url="http://localhost:8000")
    url = client._resume_url("agents", "agent_chat", "exec_123")
    assert url == "http://localhost:8000/workflows/agents/agent_chat/resume/exec_123/stream"
