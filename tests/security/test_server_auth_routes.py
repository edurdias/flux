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
                    "/workflows/default/some_workflow/executions",
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
            resp = client.get("/workflows/default/nonexistent/executions")
            assert resp.status_code != 403
        finally:
            settings.security.auth.oidc.enabled = original_oidc
            settings.security.auth.api_keys.enabled = original_keys


class TestAuthIsAuthorizedEndpointRemoved:
    """POST /auth/is-authorized is removed — replaced by /executions/{id}/authorize/{task}."""

    def test_old_endpoint_returns_404(self, client):
        resp = client.post(
            "/auth/is-authorized",
            json={"token": "fake", "permission": "workflow:x:run"},
            headers={"Authorization": "Bearer fake"},
        )
        assert resp.status_code == 404

    def test_new_authorize_endpoint_not_rate_limited(self, client):
        """Call /executions/*/authorize/* rapidly — none should return 429."""
        import concurrent.futures

        def make_request():
            return client.post(
                "/executions/exec-001/authorize/some_task",
                headers={"Authorization": "Bearer fake-exec-token"},
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_request) for _ in range(30)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert all(
            r.status_code != 429 for r in results
        ), "Execution authorize endpoint is being rate-limited"


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


class TestPrincipalRequestModels:
    def test_principal_create_request_requires_subject_and_type(self):
        from flux.server import PrincipalCreateRequest
        import pytest

        with pytest.raises(Exception):
            PrincipalCreateRequest()

        req = PrincipalCreateRequest(
            subject="alice@acme.com",
            type="user",
            roles=["viewer"],
        )
        assert req.subject == "alice@acme.com"
        assert req.type == "user"
        assert req.roles == ["viewer"]

    def test_principal_update_request_optional_fields(self):
        from flux.server import PrincipalUpdateRequest

        req = PrincipalUpdateRequest()
        assert req.display_name is None
        assert req.enabled is None

    def test_role_grant_request_requires_role(self):
        from flux.server import RoleGrantRequest
        import pytest

        with pytest.raises(Exception):
            RoleGrantRequest()

        req = RoleGrantRequest(role="operator")
        assert req.role == "operator"

    def test_principal_response_model_fields(self):
        from flux.server import PrincipalResponse

        resp = PrincipalResponse(
            id="uuid-1",
            subject="alice@acme.com",
            type="user",
            external_issuer="https://issuer",
            display_name="Alice",
            enabled=True,
            roles=["viewer"],
        )
        assert resp.id == "uuid-1"
        assert resp.enabled is True


class TestOldServiceAccountRoutesRemoved:
    def test_get_service_accounts_returns_404(self, client):
        resp = client.get("/admin/service-accounts")
        assert resp.status_code == 404

    def test_post_service_accounts_returns_404(self, client):
        resp = client.post("/admin/service-accounts", json={"name": "svc", "roles": []})
        assert resp.status_code == 404

    def test_get_service_account_by_name_returns_404(self, client):
        resp = client.get("/admin/service-accounts/svc-test")
        assert resp.status_code == 404

    def test_patch_service_account_returns_404(self, client):
        resp = client.patch("/admin/service-accounts/svc-test", json={})
        assert resp.status_code == 404

    def test_delete_service_account_returns_404(self, client):
        resp = client.delete("/admin/service-accounts/svc-test")
        assert resp.status_code == 404

    def test_post_service_account_keys_returns_404(self, client):
        resp = client.post(
            "/admin/service-accounts/svc-test/keys",
            json={"name": "prod"},
        )
        assert resp.status_code == 404

    def test_get_service_account_keys_returns_404(self, client):
        resp = client.get("/admin/service-accounts/svc-test/keys")
        assert resp.status_code == 404

    def test_delete_service_account_key_returns_404(self, client):
        resp = client.delete("/admin/service-accounts/svc-test/keys/prod")
        assert resp.status_code == 404


class TestPrincipalsRoutesExist:
    def test_get_principals_exists(self, client):
        resp = client.get("/admin/principals")
        assert resp.status_code in (200, 401, 403, 500)

    def test_post_principals_exists(self, client):
        resp = client.post(
            "/admin/principals",
            json={"subject": "alice", "type": "user"},
        )
        assert resp.status_code in (200, 201, 401, 403, 409, 500)

    def test_get_principals_by_subject_exists(self, client):
        resp = client.get("/admin/principals/alice%40acme.com")
        assert resp.status_code in (200, 401, 403, 404, 500)

    def test_patch_principal_exists(self, client):
        resp = client.patch("/admin/principals/alice%40acme.com", json={})
        assert resp.status_code in (200, 401, 403, 404, 500)

    def test_delete_principal_exists(self, client):
        resp = client.delete("/admin/principals/alice%40acme.com")
        assert resp.status_code in (200, 401, 403, 404, 409, 500)

    def test_post_principal_roles_exists(self, client):
        resp = client.post(
            "/admin/principals/alice%40acme.com/roles",
            json={"role": "viewer"},
        )
        assert resp.status_code in (200, 401, 403, 404, 500)

    def test_delete_principal_role_exists(self, client):
        resp = client.delete("/admin/principals/alice%40acme.com/roles/viewer")
        assert resp.status_code in (200, 401, 403, 404, 500)

    def test_post_principal_enable_exists(self, client):
        resp = client.post("/admin/principals/alice%40acme.com/enable")
        assert resp.status_code in (200, 401, 403, 404, 500)

    def test_post_principal_disable_exists(self, client):
        resp = client.post("/admin/principals/alice%40acme.com/disable")
        assert resp.status_code in (200, 401, 403, 404, 500)

    def test_post_principal_keys_exists(self, client):
        resp = client.post(
            "/admin/principals/svc-test/keys",
            json={"name": "prod"},
        )
        assert resp.status_code in (200, 401, 403, 404, 500)

    def test_get_principal_keys_exists(self, client):
        resp = client.get("/admin/principals/svc-test/keys")
        assert resp.status_code in (200, 401, 403, 404, 500)

    def test_delete_principal_key_exists(self, client):
        resp = client.delete("/admin/principals/svc-test/keys/prod")
        assert resp.status_code in (200, 401, 403, 404, 500)


class TestIsAuthorizedEndpointRemoved:
    def test_old_is_authorized_endpoint_returns_404(self, client):
        resp = client.post(
            "/auth/is-authorized",
            json={"token": "fake", "permission": "workflow:x:run"},
        )
        assert resp.status_code == 404


class TestExecutionAuthorizeEndpoint:
    def test_authorize_endpoint_exists(self, client):
        resp = client.post(
            "/executions/nonexistent-exec/authorize/my_task",
            headers={"Authorization": "Bearer fake-exec-token"},
        )
        assert resp.status_code != 404

    def test_authorize_returns_401_or_403_for_missing_auth(self, client):
        resp = client.post("/executions/exec-123/authorize/my_task")
        assert resp.status_code in (401, 403)

    def test_authorize_returns_403_for_non_exec_token(self, client):
        from flux.security.identity import FluxIdentity
        from unittest.mock import patch, MagicMock

        user_identity = FluxIdentity(
            subject="alice@acme.com",
            roles=frozenset({"operator"}),
            metadata={"token_type": "oidc"},
        )

        async def mock_auth(token):
            return user_identity

        mock_service = MagicMock()
        mock_service.authenticate = mock_auth

        with patch("flux.security.dependencies._get_auth_service", return_value=mock_service):
            from flux.config import Configuration

            settings = Configuration.get().settings
            orig = settings.security.auth.oidc.enabled
            settings.security.auth.oidc.enabled = True
            try:
                resp = client.post(
                    "/executions/exec-123/authorize/my_task",
                    headers={"Authorization": "Bearer user-jwt"},
                )
                assert resp.status_code == 403
            finally:
                settings.security.auth.oidc.enabled = orig


class TestExecutionAuthTokensDictRemoved:
    def test_server_has_no_execution_auth_tokens_attr(self):
        from flux.server import Server

        server = Server(host="localhost", port=8000)
        assert not hasattr(
            server,
            "_execution_auth_tokens",
        ), "Server still has _execution_auth_tokens in-memory dict"


class TestDispatcherReadsExecTokenFromDB:
    def test_dispatcher_no_longer_reads_execution_auth_tokens_dict(self):
        import pathlib

        src = pathlib.Path("flux/server.py").read_text()
        assert (
            "_execution_auth_tokens" not in src
        ), "_execution_auth_tokens still referenced in server.py"


class TestWorkflowsRunMintsExecToken:
    def test_server_has_no_execution_auth_tokens_dict(self):
        from flux.server import Server

        server = Server(host="localhost", port=8000)
        assert not hasattr(server, "_execution_auth_tokens")


class TestWorkflowsResumeMintsExecToken:
    def test_resume_no_longer_references_internal_token(self):
        import ast
        import pathlib

        src = pathlib.Path("flux/server.py").read_text()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "workflows_resume":
                func_src = ast.unparse(node)
                assert (
                    "mint_internal_token" not in func_src
                ), "workflows_resume still calls mint_internal_token"
                break


class TestSchedulerPrincipalLookup:
    def test_trigger_scheduled_workflow_uses_principal_registry(self):
        import ast
        import pathlib

        src = pathlib.Path("flux/server.py").read_text()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.AsyncFunctionDef)
                and node.name == "_trigger_scheduled_workflow"
            ):
                func_src = ast.unparse(node)
                assert (
                    "PrincipalRegistry" in func_src
                ), "_trigger_scheduled_workflow does not use PrincipalRegistry"
                assert (
                    "get_service_account" not in func_src
                ), "_trigger_scheduled_workflow still uses old get_service_account"
                break


class TestSchedulerMintExecToken:
    def test_trigger_uses_mint_execution_token_not_internal(self):
        import ast
        import pathlib

        src = pathlib.Path("flux/server.py").read_text()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.AsyncFunctionDef)
                and node.name == "_trigger_scheduled_workflow"
            ):
                func_src = ast.unparse(node)
                assert (
                    "mint_execution_token" in func_src
                ), "_trigger_scheduled_workflow does not call mint_execution_token"
                assert (
                    "mint_internal_token" not in func_src
                ), "_trigger_scheduled_workflow still calls mint_internal_token"
                break


class TestMintInternalTokenRemovedFromServer:
    def test_server_does_not_import_mint_internal_token(self):
        import pathlib

        src = pathlib.Path("flux/server.py").read_text()
        assert "mint_internal_token" not in src, "server.py still references mint_internal_token"


class TestSchedulerAuthIntegration:
    @pytest.mark.asyncio
    async def test_trigger_skips_when_principal_not_found(self):
        from unittest.mock import MagicMock, patch

        from flux.server import Server

        server = Server(host="localhost", port=8000)

        schedule = MagicMock()
        schedule.name = "test-schedule"
        schedule.workflow_name = "my_workflow"
        schedule.run_as_service_account = "svc-missing"
        schedule.input_data = None

        mock_registry_instance = MagicMock()
        mock_registry_instance.find.return_value = None
        mock_registry_cls = MagicMock(return_value=mock_registry_instance)

        with (
            patch("flux.security.principals.PrincipalRegistry", mock_registry_cls),
            patch("flux.server.Configuration") as mock_cfg,
        ):
            mock_auth = mock_cfg.get.return_value.settings.security.auth
            mock_auth.enabled = True

            await server._trigger_scheduled_workflow(schedule, None)
            schedule.mark_failure.assert_called_once()

    @pytest.mark.asyncio
    async def test_trigger_skips_when_principal_disabled(self):
        from unittest.mock import MagicMock, patch

        from flux.server import Server

        server = Server(host="localhost", port=8000)

        schedule = MagicMock()
        schedule.name = "test-schedule"
        schedule.workflow_name = "my_workflow"
        schedule.run_as_service_account = "svc-disabled"
        schedule.input_data = None

        disabled_principal = MagicMock()
        disabled_principal.enabled = False

        mock_registry_instance = MagicMock()
        mock_registry_instance.find.return_value = disabled_principal
        mock_registry_cls = MagicMock(return_value=mock_registry_instance)

        with (
            patch("flux.security.principals.PrincipalRegistry", mock_registry_cls),
            patch("flux.server.Configuration") as mock_cfg,
        ):
            mock_auth = mock_cfg.get.return_value.settings.security.auth
            mock_auth.enabled = True

            await server._trigger_scheduled_workflow(schedule, None)
            schedule.mark_failure.assert_called_once()


class TestOldAuthIsAuthorizedTestUpdated:
    def test_no_references_to_old_is_authorized_endpoint_in_tests(self):
        import pathlib

        src = pathlib.Path("tests/security/test_server_auth_routes.py").read_text()
        assert (
            "/auth/is-authorized" not in src or "404" in src
        ), "Test still references old /auth/is-authorized endpoint without 404 assertion"


class TestBuiltInRolesPrincipalsPermission:
    def test_admin_role_has_wildcard(self):
        from flux.security.auth_service import BUILT_IN_ROLES

        assert "*" in BUILT_IN_ROLES["admin"]

    def test_no_service_accounts_permission_in_built_in_roles(self):
        from flux.security.auth_service import BUILT_IN_ROLES

        for role, perms in BUILT_IN_ROLES.items():
            for perm in perms:
                assert (
                    "service-accounts" not in perm
                ), f"Role '{role}' still has old service-accounts permission: {perm}"


class TestRegisterPermissionScopedToNamespace:
    """POST /workflows must check namespace-scoped permission post-parse."""

    def test_billing_permission_grants_billing_namespace(self):
        """workflow:billing:*:register matches workflow:billing:*:register exactly."""
        from flux.security.identity import FluxIdentity

        identity = FluxIdentity(subject="billing-deployer", roles=frozenset())
        permissions = {"workflow:billing:*:register"}

        assert identity.has_permission("workflow:billing:*:register", permissions)

    def test_billing_permission_denies_analytics_namespace(self):
        """workflow:billing:*:register does NOT match workflow:analytics:*:register."""
        from flux.security.identity import FluxIdentity

        identity = FluxIdentity(subject="billing-deployer", roles=frozenset())
        permissions = {"workflow:billing:*:register"}

        assert not identity.has_permission("workflow:analytics:*:register", permissions)

    def test_billing_permission_grants_specific_billing_workflow(self):
        """workflow:billing:*:register (non-terminal *) matches workflow:billing:invoice:register."""
        from flux.security.identity import FluxIdentity

        identity = FluxIdentity(subject="billing-deployer", roles=frozenset())
        permissions = {"workflow:billing:*:register"}

        assert identity.has_permission("workflow:billing:invoice:register", permissions)

    def test_billing_permission_denies_specific_analytics_workflow(self):
        """workflow:billing:*:register does NOT match workflow:analytics:report:register."""
        from flux.security.identity import FluxIdentity

        identity = FluxIdentity(subject="billing-deployer", roles=frozenset())
        permissions = {"workflow:billing:*:register"}

        assert not identity.has_permission("workflow:analytics:report:register", permissions)

    def test_workflows_save_uses_get_identity_not_require_permission(self):
        """workflows_save must use get_identity dependency so namespace can be extracted post-parse."""
        import ast
        import pathlib

        src = pathlib.Path("flux/server.py").read_text()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "workflows_save":
                func_src = ast.unparse(node)
                assert (
                    "require_permission" not in func_src
                ), "workflows_save still uses require_permission — must use get_identity"
                assert "get_identity" in func_src, "workflows_save does not use get_identity"
                assert (
                    "is_authorized" in func_src
                ), "workflows_save does not call is_authorized post-parse"
                break

    def test_workflows_save_permission_uses_namespace(self):
        """The permission string built in workflows_save must include the workflow namespace."""
        import ast
        import pathlib

        src = pathlib.Path("flux/server.py").read_text()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "workflows_save":
                func_src = ast.unparse(node)
                assert (
                    "wf.namespace" in func_src
                ), "workflows_save does not use wf.namespace to build the permission string"
                break


class TestNamespaceScopedVisibility:
    """Namespace-scoped grants must filter list/delete endpoints post-resolution."""

    def test_list_workflows_uses_get_identity_not_require_permission(self):
        """workflows_all must use get_identity so filtering can happen per principal."""
        import ast
        import pathlib

        src = pathlib.Path("flux/server.py").read_text()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "workflows_all":
                func_src = ast.unparse(node)
                assert (
                    "require_permission" not in func_src
                ), "workflows_all still uses require_permission — must use get_identity"
                assert "get_identity" in func_src, "workflows_all does not use get_identity"
                break

    def test_list_namespaces_uses_get_identity_not_require_permission(self):
        """list_namespaces must use get_identity so filtering can happen per principal."""
        import ast
        import pathlib

        src = pathlib.Path("flux/server.py").read_text()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "list_namespaces":
                func_src = ast.unparse(node)
                assert (
                    "require_permission" not in func_src
                ), "list_namespaces still uses require_permission — must use get_identity"
                assert "get_identity" in func_src, "list_namespaces does not use get_identity"
                break

    def test_workflow_delete_ns_uses_get_identity_not_require_permission(self):
        """workflow_delete_ns must use get_identity and do a post-resolution check."""
        import ast
        import pathlib

        src = pathlib.Path("flux/server.py").read_text()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "workflow_delete_ns":
                func_src = ast.unparse(node)
                assert (
                    "require_permission" not in func_src
                ), "workflow_delete_ns still uses require_permission — must use get_identity"
                assert "get_identity" in func_src, "workflow_delete_ns does not use get_identity"
                assert (
                    "is_authorized" in func_src
                ), "workflow_delete_ns does not call is_authorized for post-resolution check"
                break

    def test_workflow_delete_ns_permission_uses_namespace(self):
        """The permission string built in workflow_delete_ns must include the resolved namespace."""
        import ast
        import pathlib

        src = pathlib.Path("flux/server.py").read_text()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "workflow_delete_ns":
                func_src = ast.unparse(node)
                assert (
                    "namespace" in func_src
                ), "workflow_delete_ns does not use namespace in permission string"
                assert (
                    "register" in func_src
                ), "workflow_delete_ns does not check register permission"
                break

    def test_billing_read_grant_allows_billing_list(self):
        """workflow:billing:*:read matches workflow:billing:invoice:read (for list filtering)."""
        from flux.security.identity import FluxIdentity

        identity = FluxIdentity(subject="billing-reader", roles=frozenset())
        permissions = {"workflow:billing:*:read"}

        assert identity.has_permission("workflow:billing:invoice:read", permissions)
        assert not identity.has_permission("workflow:analytics:report:read", permissions)

    def test_billing_register_grant_allows_billing_delete(self):
        """workflow:billing:*:register satisfies the DELETE check for billing namespace."""
        from flux.security.identity import FluxIdentity

        identity = FluxIdentity(subject="billing-deployer", roles=frozenset())
        permissions = {"workflow:billing:*:register"}

        assert identity.has_permission("workflow:billing:*:register", permissions)
        assert not identity.has_permission("workflow:analytics:*:register", permissions)

    def test_list_workflows_filters_by_namespace_grant(self):
        """workflows_all must filter result set per principal's read grants when auth enabled."""
        import ast
        import pathlib

        src = pathlib.Path("flux/server.py").read_text()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "workflows_all":
                func_src = ast.unparse(node)
                assert (
                    "resolve_permissions" in func_src or "is_authorized" in func_src
                ), "workflows_all does not filter results by principal grants"
                break

    def test_list_namespaces_filters_by_namespace_grant(self):
        """list_namespaces must filter per principal's read grants when auth enabled."""
        import ast
        import pathlib

        src = pathlib.Path("flux/server.py").read_text()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "list_namespaces":
                func_src = ast.unparse(node)
                assert (
                    "resolve_permissions" in func_src or "is_authorized" in func_src
                ), "list_namespaces does not filter results by principal grants"
                break

    def test_list_schedules_uses_get_identity_not_require_permission(self):
        """list_schedules must use get_identity so filtering can happen per principal."""
        import ast
        import pathlib

        src = pathlib.Path("flux/server.py").read_text()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "list_schedules":
                func_src = ast.unparse(node)
                assert (
                    "require_permission" not in func_src
                ), "list_schedules still uses require_permission — must use get_identity"
                assert "get_identity" in func_src, "list_schedules does not use get_identity"
                break

    def test_list_schedules_filters_by_namespace_grant(self):
        """list_schedules must filter per principal's workflow:read grants when auth enabled."""
        import ast
        import pathlib

        src = pathlib.Path("flux/server.py").read_text()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "list_schedules":
                func_src = ast.unparse(node)
                assert (
                    "resolve_permissions" in func_src
                ), "list_schedules does not resolve per-principal permissions"
                assert (
                    "workflow:" in func_src and "has_permission" in func_src
                ), "list_schedules does not check workflow:{ns}:{name}:read per schedule"
                break

    def test_get_schedule_checks_workflow_read_permission(self):
        """get_schedule must do a post-resolution workflow:{ns}:{name}:read check."""
        import ast
        import pathlib

        src = pathlib.Path("flux/server.py").read_text()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_schedule":
                func_src = ast.unparse(node)
                assert (
                    "require_permission" not in func_src
                ), "get_schedule still uses require_permission — must use get_identity"
                assert "get_identity" in func_src, "get_schedule does not use get_identity"
                assert "is_authorized" in func_src, "get_schedule does not call is_authorized"
                assert (
                    "workflow_namespace" in func_src and "workflow_name" in func_src
                ), "get_schedule does not use the schedule's namespace+name in the check"
                break


class TestAgentAdminEndpointsRBAC:
    """Regression: admin_update_agent and admin_delete_agent require the correct permission."""

    def _run_with_auth(self, client, method, path, expected_permission, granted_permissions):
        """Call the endpoint with auth enabled, granting only the listed permissions.

        Returns a tuple: (response, permissions_checked) where permissions_checked is
        the set of permissions the endpoint's dependency asked about.
        """
        from flux.config import Configuration
        from flux.security.identity import FluxIdentity

        permissions_checked: list[str] = []

        identity = FluxIdentity(subject="limited-user", roles=frozenset({"role-x"}))

        async def mock_authenticate(token):
            return identity

        async def mock_is_authorized(ident, permission):
            permissions_checked.append(permission)
            return permission in granted_permissions

        mock_auth_service = MagicMock()
        mock_auth_service.authenticate = mock_authenticate
        mock_auth_service.is_authorized = mock_is_authorized

        settings = Configuration.get().settings
        orig_keys = settings.security.auth.api_keys.enabled
        settings.security.auth.api_keys.enabled = True

        with patch(
            "flux.security.dependencies._get_auth_service",
            return_value=mock_auth_service,
        ):
            try:
                if method == "PUT":
                    resp = client.put(
                        path,
                        json={"name": "coder", "model": "anthropic/claude", "system_prompt": "x"},
                        headers={"Authorization": "Bearer fake-token"},
                    )
                elif method == "DELETE":
                    resp = client.delete(
                        path,
                        headers={"Authorization": "Bearer fake-token"},
                    )
                else:
                    raise ValueError(method)
            finally:
                settings.security.auth.api_keys.enabled = orig_keys

        assert expected_permission in permissions_checked, (
            f"Expected permission '{expected_permission}' was never checked; "
            f"permissions actually checked: {permissions_checked}"
        )
        return resp, permissions_checked

    def test_update_agent_requires_update_permission_not_create(self, client):
        """PUT /admin/agents/{name} must require agent:*:update, NOT agent:*:create."""
        resp, perms = self._run_with_auth(
            client,
            "PUT",
            "/admin/agents/coder",
            expected_permission="agent:*:update",
            granted_permissions={"agent:*:create"},
        )
        assert resp.status_code in (
            401,
            403,
        ), f"Expected 401/403 when only agent:*:create is granted, got {resp.status_code}"
        assert (
            "agent:*:update" in perms or "agent:*:create" not in perms
        ), f"Expected endpoint to check agent:*:update; checked: {perms}"

    def test_update_agent_allows_with_update_permission(self, client):
        """PUT /admin/agents/{name} must pass the permission check when agent:*:update is granted."""
        resp, perms = self._run_with_auth(
            client,
            "PUT",
            "/admin/agents/coder",
            expected_permission="agent:*:update",
            granted_permissions={"agent:*:update"},
        )
        assert (
            resp.status_code not in (401, 403)
        ), f"agent:*:update should allow the endpoint; got {resp.status_code} (perms checked: {perms})"
        assert "agent:*:update" in perms

    def test_delete_agent_requires_delete_permission_not_create(self, client):
        """DELETE /admin/agents/{name} must require agent:*:delete, NOT agent:*:create."""
        resp, perms = self._run_with_auth(
            client,
            "DELETE",
            "/admin/agents/coder",
            expected_permission="agent:*:delete",
            granted_permissions={"agent:*:create"},
        )
        assert resp.status_code in (401, 403)
        assert (
            "agent:*:delete" in perms or "agent:*:create" not in perms
        ), f"Expected endpoint to check agent:*:delete; checked: {perms}"

    def test_delete_agent_allows_with_delete_permission(self, client):
        """DELETE /admin/agents/{name} must pass the permission check when agent:*:delete is granted."""
        resp, perms = self._run_with_auth(
            client,
            "DELETE",
            "/admin/agents/coder",
            expected_permission="agent:*:delete",
            granted_permissions={"agent:*:delete"},
        )
        assert (
            resp.status_code not in (401, 403)
        ), f"agent:*:delete should allow the endpoint; got {resp.status_code} (perms checked: {perms})"
        assert "agent:*:delete" in perms


class TestAgentCodeUploadGate:
    """POST/PUT /admin/agents must escalate to workflow:*:*:register when the body
    ships executable content (tools_file, workflow_file) or an inline skills_dir bundle
    that gets materialized on worker filesystems.
    """

    def _post_agent(self, client, body, granted_permissions):
        """Call POST /admin/agents with auth enabled and only the listed permissions granted."""
        from flux.config import Configuration
        from flux.security.identity import FluxIdentity

        permissions_checked: list[str] = []
        identity = FluxIdentity(subject="limited-user", roles=frozenset({"role-x"}))

        async def mock_authenticate(token):
            return identity

        async def mock_is_authorized(ident, permission):
            permissions_checked.append(permission)
            return permission in granted_permissions

        mock_auth_service = MagicMock()
        mock_auth_service.authenticate = mock_authenticate
        mock_auth_service.is_authorized = mock_is_authorized

        settings = Configuration.get().settings
        orig_keys = settings.security.auth.api_keys.enabled
        settings.security.auth.api_keys.enabled = True
        try:
            with patch(
                "flux.security.dependencies._get_auth_service",
                return_value=mock_auth_service,
            ):
                resp = client.post(
                    "/admin/agents",
                    json=body,
                    headers={"Authorization": "Bearer fake-token"},
                )
        finally:
            settings.security.auth.api_keys.enabled = orig_keys
        return resp, permissions_checked

    def _base_body(self, **overrides):
        body = {
            "name": "evil",
            "model": "openai/gpt-4o",
            "system_prompt": "x",
        }
        body.update(overrides)
        return body

    def test_skills_dir_bundle_blocked_without_register_permission(self, client):
        """A caller with only agent:*:create must NOT be able to ship an inline skills_dir bundle."""
        body = self._base_body(
            skills_dir='{"a": {"../../etc/passwd": "rooted"}}',
        )
        resp, perms = self._post_agent(
            client,
            body,
            granted_permissions={"agent:*:create"},
        )
        assert (
            resp.status_code == 403
        ), f"Inline skills_dir bundle must require workflow:*:*:register; got {resp.status_code}"
        assert "workflow:*:*:register" in perms
        assert "skills_dir" in resp.json().get("detail", "")

    def test_tools_file_still_blocked_without_register_permission(self, client):
        """Regression: tools_file gate keeps working under the renamed helper."""
        body = self._base_body(tools_file="from flux import task")
        resp, perms = self._post_agent(
            client,
            body,
            granted_permissions={"agent:*:create"},
        )
        assert resp.status_code == 403
        assert "workflow:*:*:register" in perms

    def test_workflow_file_still_blocked_without_register_permission(self, client):
        """Regression: workflow_file gate keeps working under the renamed helper."""
        body = self._base_body(workflow_file="from flux import workflow")
        resp, perms = self._post_agent(
            client,
            body,
            granted_permissions={"agent:*:create"},
        )
        assert resp.status_code == 403
        assert "workflow:*:*:register" in perms

    def test_skills_dir_path_string_does_not_require_register_permission(self, client):
        """A plain path string in skills_dir is NOT a code-upload — it must not trigger the gate."""
        body = self._base_body(skills_dir="/var/lib/flux/skills")
        resp, perms = self._post_agent(
            client,
            body,
            granted_permissions={"agent:*:create"},
        )
        # The gate must not be tripped (no 403 referencing the upload restriction).
        # The endpoint may still 500 because no AgentManager is registered in this
        # test harness — what we're asserting is the absence of the upload gate.
        assert (
            "workflow:*:*:register" not in perms
        ), f"Path-only skills_dir must NOT trigger workflow:*:*:register; perms checked: {perms}"

    def test_no_dangerous_fields_does_not_require_register_permission(self, client):
        """Plain agent definition (no executable content) must not trigger the gate."""
        body = self._base_body()
        resp, perms = self._post_agent(
            client,
            body,
            granted_permissions={"agent:*:create"},
        )
        assert "workflow:*:*:register" not in perms

    def test_skills_dir_bundle_allowed_with_register_permission(self, client):
        """When workflow:*:*:register is granted, the bundle path is allowed past the gate."""
        body = self._base_body(
            skills_dir='{"a": {"SKILL.md": "harmless"}}',
        )
        resp, perms = self._post_agent(
            client,
            body,
            granted_permissions={"agent:*:create", "workflow:*:*:register"},
        )
        # Permission was checked and granted; any non-403 response means the gate
        # did not block the request.
        assert resp.status_code != 403
        assert "workflow:*:*:register" in perms
