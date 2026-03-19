from __future__ import annotations

import pytest

from flux.tasks.mcp.auth import bearer, oauth, BearerAuthConfig, OAuthConfig


def test_bearer_with_static_token():
    config = bearer("my-token")
    assert isinstance(config, BearerAuthConfig)
    assert config.token == "my-token"
    assert config.secret is None
    assert config.provider is None


def test_bearer_with_secret():
    config = bearer(secret="MCP_API_KEY")
    assert isinstance(config, BearerAuthConfig)
    assert config.token is None
    assert config.secret == "MCP_API_KEY"


def test_bearer_with_provider():
    def get_token():
        return "fresh-token"

    config = bearer(provider=get_token)
    assert isinstance(config, BearerAuthConfig)
    assert config.provider is get_token


def test_bearer_no_args_raises():
    with pytest.raises(ValueError, match="at least one"):
        bearer()


def test_oauth_with_scopes():
    config = oauth(scopes=["read", "write"])
    assert isinstance(config, OAuthConfig)
    assert config.scopes == ["read", "write"]


def test_oauth_with_all_options():
    config = oauth(
        scopes=["read"],
        client_id="my-id",
        client_secret="my-secret",
        client_name="my-app",
        callback_port=8080,
    )
    assert config.client_id == "my-id"
    assert config.client_secret == "my-secret"
    assert config.client_name == "my-app"
    assert config.callback_port == 8080
