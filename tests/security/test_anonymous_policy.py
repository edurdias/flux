"""Tests for the secure-default anonymous-mutation policy.

When authentication is disabled, state-changing requests (POST/PUT/PATCH/DELETE)
must be refused unless the operator explicitly opts in via
``security.auth.allow_anonymous``. Read-only requests stay open.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flux.config import Configuration
from flux.server import Server


def _client() -> TestClient:
    server = Server(host="localhost", port=8000)
    return TestClient(server._create_api(), raise_server_exceptions=False)


def test_anonymous_mutation_denied_by_default():
    Configuration.get().override(
        security={"auth": {"enabled": False, "allow_anonymous": False}},
    )
    resp = _client().post("/workflows", files={"file": ("wf.py", b"x")})
    assert resp.status_code == 401
    assert "anonymous" in resp.json()["detail"].lower()


def test_anonymous_read_allowed_when_denying_mutations():
    Configuration.get().override(
        security={"auth": {"enabled": False, "allow_anonymous": False}},
    )
    # GET is read-only and must not be blocked by the policy.
    resp = _client().get("/health")
    assert resp.status_code != 401


def test_anonymous_mutation_allowed_with_optout():
    Configuration.get().override(
        security={"auth": {"enabled": False, "allow_anonymous": True}},
    )
    # Invalid body — the request should pass the anonymous gate and fail later
    # in parsing, proving the middleware let it through (status is not 401).
    resp = _client().post("/workflows", files={"file": ("wf.py", b"not valid (")})
    assert resp.status_code != 401
