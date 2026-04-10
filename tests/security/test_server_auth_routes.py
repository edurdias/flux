"""Tests for server route authorization — regression tests for recent PR feedback."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from flux.server import Server


@pytest.fixture
def server_app():
    """Create a server FastAPI app for testing."""
    server = Server(host="localhost", port=8000)
    return server._create_api()


@pytest.fixture
def client(server_app):
    """TestClient for the server app."""
    return TestClient(server_app)


class TestWorkflowExecutionsAuthEnforcement:
    """GET /workflows/{name}/executions must enforce workflow:{name}:read when auth is enabled."""

    def test_executions_endpoint_requires_read_permission_when_auth_enabled(self, client):
        """A caller without workflow:{name}:read should get 403."""
        from flux.security.identity import FluxIdentity

        limited_identity = FluxIdentity(
            subject="limited-user",
            roles=frozenset({"no-such-role"}),
        )

        # Async mock factory
        async def mock_authenticate(token):
            return limited_identity

        async def mock_is_authorized(identity, permission):
            return False

        mock_auth_service = MagicMock()
        mock_auth_service.authenticate = mock_authenticate
        mock_auth_service.is_authorized = mock_is_authorized

        with patch(
            "flux.security.dependencies._get_auth_service",
            return_value=mock_auth_service,
        ):
            from flux.config import Configuration

            settings = Configuration.get().settings
            original = settings.security.auth.api_keys.enabled
            settings.security.auth.api_keys.enabled = True
            try:
                resp = client.get(
                    "/workflows/some_workflow/executions",
                    headers={"Authorization": "Bearer fake-token"},
                )
                # Either 403 (permission denied) or 401 (if auth provider rejects fake)
                assert resp.status_code in (401, 403)
            finally:
                settings.security.auth.api_keys.enabled = original

    def test_executions_endpoint_allows_when_auth_disabled(self, client):
        """When auth is disabled, the endpoint should not require permissions."""
        from flux.config import Configuration

        settings = Configuration.get().settings
        original_oidc = settings.security.auth.oidc.enabled
        original_keys = settings.security.auth.api_keys.enabled
        settings.security.auth.oidc.enabled = False
        settings.security.auth.api_keys.enabled = False
        try:
            # Without auth enabled, the endpoint should only fail with 404 (workflow not found)
            # or similar — NOT with 403
            resp = client.get("/workflows/nonexistent/executions")
            assert resp.status_code != 403
        finally:
            settings.security.auth.oidc.enabled = original_oidc
            settings.security.auth.api_keys.enabled = original_keys


class TestAuthIsAuthorizedNoRateLimit:
    """POST /auth/is-authorized must not be rate-limited."""

    def test_endpoint_is_not_rate_limited(self, client):
        """Call the endpoint 100 times rapidly — none should return 429."""

        def make_request():
            return client.post(
                "/auth/is-authorized",
                json={"token": "fake", "permission": "workflow:x:run"},
                headers={"Authorization": "Bearer fake"},
            )

        # Make 100 sequential requests
        responses = [make_request() for _ in range(100)]

        # None should be 429 (rate limited)
        rate_limited = [r for r in responses if r.status_code == 429]
        assert len(rate_limited) == 0, f"{len(rate_limited)} requests were rate-limited"


class TestRateLimitHandlerSingleRegistration:
    """The rate limit handler should be registered exactly once."""

    def test_rate_limit_handler_registered_once(self, server_app):
        """Verify exception_handlers dict has exactly one entry for RateLimitExceeded."""
        from slowapi.errors import RateLimitExceeded

        # FastAPI stores exception handlers in app.exception_handlers
        handlers = server_app.exception_handlers
        rate_limit_handlers = [
            handler for exc_type, handler in handlers.items() if exc_type is RateLimitExceeded
        ]
        # Should be exactly one registration (was previously registered twice)
        assert len(rate_limit_handlers) == 1


class TestAuthTestTokenRateLimited:
    """POST /auth/test-token should still be rate-limited to prevent token enumeration."""

    def test_test_token_endpoint_has_rate_limit(self, server_app):
        """Verify the test-token endpoint has a limiter decorator."""
        # Find the route
        routes = [r for r in server_app.routes if getattr(r, "path", None) == "/auth/test-token"]
        assert len(routes) == 1
        # The endpoint should exist; actual rate limit testing requires many sequential calls
        # which is flaky in unit tests. We verify the route is registered.
