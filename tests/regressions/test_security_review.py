from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def security_env(tmp_path, monkeypatch):
    from flux.config import Configuration
    from flux.models import DatabaseRepository
    from flux.security import dependencies, execution_token

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FLUX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FLUX_DATABASE_URL", f"sqlite:///{tmp_path / 'security.db'}")
    monkeypatch.setenv("FLUX_WORKERS__BOOTSTRAP_TOKEN", "isolated-test-bootstrap")
    monkeypatch.setenv("FLUX_SECURITY__ENCRYPTION__ENCRYPTION_KEY", "isolated-test-key")
    monkeypatch.setenv("FLUX_EXECUTION_TOKEN_SECRET", "isolated-test-signing-key-32-bytes")
    monkeypatch.setenv("FLUX_SECURITY__AUTH__ENABLED", "true")
    monkeypatch.setenv("FLUX_SECURITY__AUTH__API_KEYS__ENABLED", "true")
    Configuration._instance = None
    Configuration._config = None
    DatabaseRepository._engines.clear()
    execution_token._EXECUTION_STATE_CACHE.clear()

    from flux.server import Server

    server = Server("127.0.0.1", 0)
    app = server._create_api()
    service = dependencies._get_auth_service()
    yield server, TestClient(app), service
    dependencies.init_auth_service(None)
    execution_token._EXECUTION_STATE_CACHE.clear()
    for engine in DatabaseRepository._engines.values():
        engine.dispose()
    DatabaseRepository._engines.clear()
    Configuration._instance = None
    Configuration._config = None


def seed_workflow(
    *,
    exempt: bool = False,
    source: bytes = b"async def review_workflow(ctx): pass",
):
    from flux.catalogs import WorkflowCatalog, WorkflowInfo

    wf = WorkflowInfo(
        id="",
        name="review_workflow",
        imports=[],
        source=source,
        metadata={"task_names": ["deploy"], "auth_exempt_tasks": ["deploy"] if exempt else []},
    )
    WorkflowCatalog.create().save([wf])
    return wf


def seed_execution(wf, *, worker: str = "worker-b", execution_id: str = "review-execution"):
    from flux.context_managers import ContextManager
    from flux.domain import ExecutionContext, ExecutionState

    ctx: ExecutionContext = ExecutionContext(
        workflow_id=wf.id,
        workflow_namespace=wf.namespace,
        workflow_name=wf.name,
        execution_id=execution_id,
        current_worker=worker,
        state=ExecutionState.RUNNING,
    )
    ContextManager.create().save(ctx)
    return ctx


@pytest.mark.asyncio
async def test_cached_api_key_is_rejected_after_expiration(security_env, monkeypatch):
    import flux.utils

    from flux.security.providers import api_key

    _, _, auth = security_env
    principal = await auth.create_principal(
        type="service_account",
        subject="worker-a",
        external_issuer="flux",
        roles=["worker"],
    )
    key = await auth.create_api_key(principal.id, "short", expires=timedelta(seconds=5))
    await auth.authenticate(key)
    future = datetime.now(timezone.utc) + timedelta(seconds=10)
    future_monotonic = flux.utils.monotonic() + 10

    class FutureDatetime:
        @staticmethod
        def now(tz):
            return future

    monkeypatch.setattr(api_key, "datetime", FutureDatetime)
    monkeypatch.setattr(flux.utils, "monotonic", lambda: future_monotonic)
    direct_provider = api_key.APIKeyProvider(auth._session_factory, auth.principal_registry)
    assert await direct_provider.authenticate(key) is None
    from flux.security.errors import AuthenticationError

    with pytest.raises(AuthenticationError):
        await auth.authenticate(key)
    auth.invalidate_resolution_caches()
    from flux.security.errors import AuthenticationError

    with pytest.raises(AuthenticationError):
        await auth.authenticate(key)


@pytest.mark.asyncio
async def test_worker_cannot_inject_progress_into_another_workers_execution(security_env):
    server, client, auth = security_env
    principal = await auth.create_principal(
        type="service_account",
        subject="worker-a",
        external_issuer="flux",
        roles=["worker"],
    )
    key = await auth.create_api_key(principal.id, "session")
    ctx = seed_execution(seed_workflow(), worker="worker-b")
    buffer = server.signals.open_progress_buffer(ctx.execution_id)
    response = client.post(
        f"/workers/worker-a/progress/{ctx.execution_id}",
        headers={"Authorization": f"Bearer {key}"},
        json=[{"task_id": "forged", "task_name": "deploy", "value": "injected"}],
    )
    assert response.status_code == 403, response.text
    assert buffer.empty()
    control = client.get(
        f"/workers/worker-a/approvals/{ctx.execution_id}/forged",
        headers={"Authorization": f"Bearer {key}"},
    )
    assert control.status_code == 403, control.text


@pytest.mark.asyncio
async def test_new_workflow_version_preserves_old_execution_authorization(security_env):
    from flux.catalogs import WorkflowCatalog
    from flux.context_managers import ContextManager
    from flux.security.execution_token import mint_execution_token

    _, client, auth = security_env
    await auth.create_principal(type="service_account", subject="caller", external_issuer="flux")
    first_version = seed_workflow(exempt=False)
    ctx = seed_execution(first_version)
    token = mint_execution_token("caller", "flux", ctx.execution_id, "caller")
    headers = {"Authorization": f"Bearer {token}"}
    endpoint = f"/executions/{ctx.execution_id}/authorize/deploy"
    assert client.post(endpoint, headers=headers).status_code == 403
    second_version = seed_workflow(exempt=True)
    assert first_version.id != second_version.id
    assert (
        WorkflowCatalog.create().get("default", "review_workflow", 1).metadata["auth_exempt_tasks"]
        == []
    )
    assert ContextManager.create().get(ctx.execution_id).workflow_id == first_version.id
    response = client.post(endpoint, headers=headers)
    assert response.status_code == 403, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("different_execution", [False, True])
async def test_execution_token_cannot_grant_standing_human_approval(
    security_env,
    different_execution,
):
    from flux.approvals import ApprovalManager
    from flux.security.execution_token import mint_execution_token
    from flux.unit_of_work import UnitOfWork

    _, client, auth = security_env
    await auth.create_principal(
        type="service_account",
        subject="operator-caller",
        external_issuer="flux",
        roles=["operator"],
    )
    ctx = seed_execution(seed_workflow())
    with UnitOfWork() as uow:
        ApprovalManager().create(
            ctx.execution_id,
            "deploy-1",
            "default",
            "review_workflow",
            "deploy",
            uow=uow,
        )
        uow.commit()
    token = mint_execution_token("operator-caller", "flux", ctx.execution_id, "operator-caller")
    if different_execution:
        origin = seed_execution(seed_workflow(), execution_id="token-origin")
        token = mint_execution_token(
            "operator-caller",
            "flux",
            origin.execution_id,
            "operator-caller",
        )
    identity = await auth.authenticate(token)
    assert identity.metadata["token_type"] == "execution"
    assert not await auth.is_authorized(identity, "admin:secrets:read")
    response = client.post(
        f"/executions/{ctx.execution_id}/approvals/deploy-1/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={"always": True, "reason": "issued by workflow callback credential"},
    )
    assert response.status_code == 403, response.text
    row = ApprovalManager().get_by_call(ctx.execution_id, "deploy-1")
    assert row.status.value == "pending"


def test_dispatch_payload_uses_persisted_workflow_id(security_env):
    import base64

    from flux.context_managers import ContextManager

    server, _, _ = security_env
    first = seed_workflow(source=b"async def review_workflow(ctx): return 'version-one'")
    ctx = seed_execution(first)
    before = server._build_dispatch_payload(ctx)
    assert before["workflow"].id == first.id
    assert base64.b64decode(before["workflow"].source) == first.source
    second = seed_workflow(source=b"async def review_workflow(ctx): return 'version-two'")
    after = server._build_dispatch_payload(ctx)
    assert ContextManager.create().get(ctx.execution_id).workflow_id == first.id
    assert after["context"].workflow_id == first.id
    assert after["workflow"].id == first.id
    assert after["workflow"].version == 1
    assert base64.b64decode(after["workflow"].source) == first.source
    assert base64.b64decode(after["workflow"].source) != second.source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "owner,generation,buffer_open,status",
    [
        ("worker-a", 3, True, 200),
        ("worker-a", 2, True, 409),
        ("worker-a", None, True, 409),
        ("worker-b", 3, True, 403),
        (None, 3, True, 403),
        ("worker-a", 3, False, 200),
        ("worker-b", 3, False, 403),
    ],
)
async def test_progress_requires_current_owner_and_generation(
    security_env,
    owner,
    generation,
    buffer_open,
    status,
):
    from flux.models import ExecutionContextModel

    server, client, auth = security_env
    principal = await auth.create_principal(
        type="service_account",
        subject="worker-a",
        external_issuer="flux",
        roles=["worker"],
    )
    key = await auth.create_api_key(principal.id, "session")
    ctx = seed_execution(seed_workflow())
    with server._get_db_session() as session:
        row = session.get(ExecutionContextModel, ctx.execution_id)
        row.worker_name = owner
        row.claim_generation = 3
        session.commit()
    buffer = server.signals.open_progress_buffer(ctx.execution_id) if buffer_open else None
    headers = {"Authorization": f"Bearer {key}"}
    if generation is not None:
        headers["X-Flux-Claim-Generation"] = str(generation)
    response = client.post(
        f"/workers/worker-a/progress/{ctx.execution_id}",
        headers=headers,
        json=[{"task_id": "task", "task_name": "deploy", "value": "progress"}],
    )
    assert response.status_code == status, response.text
    if buffer is not None:
        assert buffer.qsize() == (1 if status == 200 else 0)
