"""E2E tests — core workflow examples."""
from __future__ import annotations


def test_server_healthy(cli):
    result = cli.health()
    assert result["status"] == "healthy"
