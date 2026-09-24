"""FM-016: real PostgreSQL retention versus the hook retry HTTP route.

Opt in only with FLUX_FORMAL_PG_URL pointing at a disposable loopback cluster
started with ``-c flux.formal_review=retention-fm016`` and database
``flux_formal_retention``. The pending retry must survive the retention sweep.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url

from flux.config import Configuration
from flux.hooks import registry as registry_module
from flux.hooks.registry import HookRegistry
from flux.models import DatabaseRepository, HookDeliveryModel, RepositoryFactory
from flux.retention import RetentionJob
from flux.server import Server

pytestmark = pytest.mark.postgresql


@pytest.fixture
def disposable_postgresql(tmp_path):
    raw_url = os.environ.get("FLUX_FORMAL_PG_URL")
    if not raw_url:
        pytest.skip("requires explicit FLUX_FORMAL_PG_URL disposable cluster")
    url = make_url(raw_url)
    assert url.drivername == "postgresql+psycopg"
    assert url.host == "127.0.0.1" and url.port not in (None, 5432)
    assert url.database == "flux_formal_retention"
    assert not url.query, "URL options could override the loopback endpoint"
    verification_engine = create_engine(url)
    try:
        with verification_engine.connect() as connection:
            assert connection.scalar(text("SHOW flux.formal_review")) == "retention-fm016"
            assert connection.scalar(text("SELECT host(inet_server_addr())")) == "127.0.0.1"
            assert connection.scalar(text("SELECT inet_server_port()")) == url.port
            print(f"PostgreSQL version: {connection.scalar(text('SELECT version()'))}")
    finally:
        verification_engine.dispose()

    Configuration.get().override(
        home=str(tmp_path),
        database_url=raw_url,
        security={
            "auth": {"enabled": False, "allow_anonymous": True},
            "encryption": {"encryption_key": "formal-retention-test-key"},
        },
        workers={"bootstrap_token": "formal-retention-test-token"},
        retention={"enabled": True, "retention_days": 30, "batch_size": 10},
    )
    DatabaseRepository._engines.clear()
    registry_module._snapshot = None
    registry_module._snapshot_loaded_at = None
    yield RepositoryFactory.create_repository()
    for engine in DatabaseRepository._engines.values():
        engine.dispose()
    DatabaseRepository._engines.clear()
    registry_module._snapshot = None
    registry_module._snapshot_loaded_at = None
    Configuration.get().reset()


def test_retention_preserves_delivery_after_successful_pending_retry(disposable_postgresql):
    repository = disposable_postgresql
    hook_name = f"formal-retention-{uuid4().hex}"
    hook = HookRegistry.create().create_hook(
        name=hook_name,
        selectors=["execution:*"],
        workflow_ref="ops/notify",
        principal="formal-test-principal",
        owner_ref="formal-review",
    )
    delivery_id = uuid4().hex
    with repository.session() as session:
        assert session.connection().get_isolation_level() == "READ COMMITTED"
        session.add(
            HookDeliveryModel(
                id=delivery_id,
                hook_id=hook.id,
                event_key=uuid4().hex,
                payload={"hook": hook_name},
                status="dead",
                attempts=5,
                created_at=datetime.now(timezone.utc) - timedelta(days=45),
            ),
        )
        session.commit()

    selected = Event()
    resume_delete = Event()
    retention_backend = []

    def pause_before_delete(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("DELETE FROM hook_deliveries"):
            retention_backend.append(connection.connection.driver_connection.info.backend_pid)
            selected.set()
            assert resume_delete.wait(20), "retry did not finish before retention barrier timeout"

    event.listen(repository._engine, "before_cursor_execute", pause_before_delete)
    try:
        client = TestClient(Server("127.0.0.1", 0)._create_api())
        with ThreadPoolExecutor(max_workers=1) as pool:
            sweep = pool.submit(RetentionJob()._sweep)
            try:
                assert selected.wait(20), "retention did not reach DELETE after selecting dead ID"
                response = client.post(f"/hooks/{hook_name}/deliveries/{delivery_id}/retry")
                assert response.status_code == 200, response.text
                assert response.json()["status"] == "pending"
                with repository.session() as session:
                    observed_backend = session.scalar(text("SELECT pg_backend_pid()"))
                    assert observed_backend != retention_backend[0]
                    assert session.get(HookDeliveryModel, delivery_id).status == "pending"
                print("Retry HTTP 200; independent session observed committed pending delivery")
            finally:
                resume_delete.set()
            assert sweep.result(timeout=20) == 0
        with repository.session() as session:
            assert session.get(HookDeliveryModel, delivery_id).status == "pending"
        print("Retention preserved the pending delivery")
    finally:
        resume_delete.set()
        event.remove(repository._engine, "before_cursor_execute", pause_before_delete)
        HookRegistry.create().delete_hook(hook_name)
