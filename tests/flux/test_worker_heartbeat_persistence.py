"""Tests for persisted worker heartbeats (cross-replica liveness)."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from flux.models import WorkerModel
from flux.worker_registry import (
    DatabaseWorkerRegistry,
    WorkerResourcesInfo,
    WorkerRuntimeInfo,
)


@pytest.fixture
def registry():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name
    db_url = f"sqlite:///{db_path}"
    with patch("flux.config.Configuration.get") as mock_config:
        mock_config.return_value.settings.database_url = db_url
        mock_config.return_value.settings.database_type = "sqlite"
        mock_config.return_value.settings.security.auth.enabled = False
        yield DatabaseWorkerRegistry()
    if os.path.exists(db_path):
        os.unlink(db_path)


def _runtime():
    return WorkerRuntimeInfo(os_name="Linux", os_version="6.0", python_version="3.12.0")


def _resources():
    return WorkerResourcesInfo(
        cpu_total=4,
        cpu_available=4,
        memory_total=8_000_000_000,
        memory_available=8_000_000_000,
        disk_total=100_000_000_000,
        disk_free=100_000_000_000,
        gpus=[],
    )


def _register(registry, name):
    registry.register(name=name, runtime=_runtime(), packages=[], resources=_resources())


def _last_seen_at(registry, name):
    with registry.session() as session:
        return session.query(WorkerModel.last_seen_at).filter(WorkerModel.name == name).scalar()


def _set_last_seen_at(registry, name, value):
    with registry.session() as session:
        session.query(WorkerModel).filter(WorkerModel.name == name).update(
            {WorkerModel.last_seen_at: value},
            synchronize_session=False,
        )
        session.commit()


def test_fresh_worker_has_null_last_seen_at(registry):
    _register(registry, "w1")
    assert _last_seen_at(registry, "w1") is None


def test_record_heartbeat_persists_timestamp(registry):
    _register(registry, "w1")
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    registry.record_heartbeat("w1")
    seen = _last_seen_at(registry, "w1")
    assert seen is not None
    # SQLite stores naive datetimes; allow a small clock window.
    assert seen >= before - timedelta(seconds=1)


def test_record_heartbeat_unknown_worker_is_noop(registry):
    # No row matches; the UPDATE simply affects zero rows and does not raise.
    registry.record_heartbeat("does-not-exist")


def test_find_stale_returns_workers_past_threshold(registry):
    _register(registry, "old")
    _register(registry, "fresh")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _set_last_seen_at(registry, "old", now - timedelta(seconds=120))
    _set_last_seen_at(registry, "fresh", now)

    stale = registry.find_stale(now - timedelta(seconds=60))
    assert stale == ["old"]


def test_find_stale_excludes_never_seen_workers(registry):
    # A registered-but-never-connected worker (NULL last_seen_at) is not stale.
    _register(registry, "never")
    stale = registry.find_stale(datetime.now(timezone.utc).replace(tzinfo=None))
    assert "never" not in stale
