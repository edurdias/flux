"""Tests for startup security validation and the /workers/register rate limit."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flux.config import Configuration
from flux.server import Server


@pytest.fixture(autouse=True)
def reset_config():
    yield
    Configuration.get().reset()


def _settings():
    return Configuration.get().settings


class TestSecurityConfigValidation:
    def test_auth_enabled_without_secrets_refuses_startup(self):
        Configuration.get().override(
            security={
                "auth": {"enabled": True, "api_keys": {"enabled": True}},
                "execution_token_secret": None,
                "encryption": {"encryption_key": None},
            },
        )
        with pytest.raises(RuntimeError) as err:
            Server._validate_security_config(_settings())
        assert "execution_token_secret" in str(err.value)
        assert "encryption_key" in str(err.value)

    def test_auth_enabled_with_secrets_passes(self):
        Configuration.get().override(
            security={
                "auth": {"enabled": True, "api_keys": {"enabled": True}},
                "execution_token_secret": "s3cret",
                "encryption": {"encryption_key": "k3y"},
            },
        )
        Server._validate_security_config(_settings())

    def test_debug_mode_is_exempt(self):
        Configuration.get().override(
            debug=True,
            security={
                "auth": {"enabled": True, "api_keys": {"enabled": True}},
                "execution_token_secret": None,
                "encryption": {"encryption_key": None},
            },
        )
        Server._validate_security_config(_settings())

    def test_auth_disabled_only_warns(self, caplog):
        Configuration.get().override(
            security={
                "auth": {"enabled": False},
                "encryption": {"encryption_key": None},
            },
        )
        Server._validate_security_config(_settings())  # must not raise
        assert any("encryption key" in r.message.lower() for r in caplog.records)


class TestRegisterRateLimit:
    def test_register_is_rate_limited(self):
        Configuration.get().override(
            workers={"bootstrap_token": "tok", "register_rate_limit": "3/minute"},
        )
        server = Server(host="localhost", port=8000)
        client = TestClient(server._create_api(), raise_server_exceptions=False)

        payload = {
            "name": "rl-worker",
            "runtime": {"os_name": "linux", "os_version": "1", "python_version": "3.12"},
            "packages": [],
            "resources": {
                "cpu_total": 1,
                "cpu_available": 1,
                "memory_total": 1,
                "memory_available": 1,
                "disk_total": 1,
                "disk_free": 1,
                "gpus": [],
            },
        }
        statuses = [
            client.post(
                "/workers/register",
                json=payload,
                headers={"Authorization": "Bearer wrong-token"},
            ).status_code
            for _ in range(4)
        ]
        assert statuses[:3] == [403, 403, 403]
        assert statuses[3] == 429

    def test_register_rate_limit_disabled_by_empty_string(self):
        Configuration.get().override(
            workers={"bootstrap_token": "tok", "register_rate_limit": ""},
        )
        server = Server(host="localhost", port=8000)
        client = TestClient(server._create_api(), raise_server_exceptions=False)

        payload = {
            "name": "rl-worker-2",
            "runtime": {"os_name": "linux", "os_version": "1", "python_version": "3.12"},
            "packages": [],
            "resources": {
                "cpu_total": 1,
                "cpu_available": 1,
                "memory_total": 1,
                "memory_available": 1,
                "disk_total": 1,
                "disk_free": 1,
                "gpus": [],
            },
        }
        statuses = [
            client.post(
                "/workers/register",
                json=payload,
                headers={"Authorization": "Bearer wrong-token"},
            ).status_code
            for _ in range(5)
        ]
        assert all(s == 403 for s in statuses)
