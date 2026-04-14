from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

from flux.worker_registry import (
    DatabaseWorkerRegistry,
    WorkerInfo,
    WorkerResourcesInfo,
    WorkerRuntimeInfo,
)


@pytest.fixture
def clean_db():
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


def _make_runtime():
    return WorkerRuntimeInfo(os_name="Linux", os_version="6.0", python_version="3.12.0")


def _make_resources():
    return WorkerResourcesInfo(
        cpu_total=4,
        cpu_available=4,
        memory_total=8_000_000_000,
        memory_available=8_000_000_000,
        disk_total=100_000_000_000,
        disk_free=100_000_000_000,
        gpus=[],
    )


def test_worker_info_default_labels():
    info = WorkerInfo(name="w1")
    assert info.labels == {}


def test_worker_info_with_labels():
    labels = {"gpu": "true", "region": "us-east"}
    info = WorkerInfo(name="w1", labels=labels)
    assert info.labels == labels


def test_register_worker_with_labels(clean_db):
    labels = {"gpu": "true", "region": "us-east"}
    result = clean_db.register(
        name="w1",
        runtime=_make_runtime(),
        packages=[],
        resources=_make_resources(),
        labels=labels,
    )
    assert result.labels == labels


def test_register_worker_without_labels(clean_db):
    result = clean_db.register(
        name="w1",
        runtime=_make_runtime(),
        packages=[],
        resources=_make_resources(),
    )
    assert result.labels == {}


def test_get_worker_preserves_labels(clean_db):
    labels = {"tier": "premium", "zone": "a"}
    clean_db.register(
        name="w1",
        runtime=_make_runtime(),
        packages=[],
        resources=_make_resources(),
        labels=labels,
    )
    retrieved = clean_db.get("w1")
    assert retrieved.labels == labels


def test_list_workers_preserves_labels(clean_db):
    labels_a = {"role": "gpu-worker"}
    labels_b = {"role": "cpu-worker", "zone": "b"}
    clean_db.register(
        name="w1",
        runtime=_make_runtime(),
        packages=[],
        resources=_make_resources(),
        labels=labels_a,
    )
    clean_db.register(
        name="w2",
        runtime=_make_runtime(),
        packages=[],
        resources=_make_resources(),
        labels=labels_b,
    )
    workers = {w.name: w for w in clean_db.list()}
    assert workers["w1"].labels == labels_a
    assert workers["w2"].labels == labels_b


def test_reregister_worker_updates_labels(clean_db):
    labels_v1 = {"version": "1"}
    labels_v2 = {"version": "2", "extra": "yes"}
    clean_db.register(
        name="w1",
        runtime=_make_runtime(),
        packages=[],
        resources=_make_resources(),
        labels=labels_v1,
    )
    clean_db.register(
        name="w1",
        runtime=_make_runtime(),
        packages=[],
        resources=_make_resources(),
        labels=labels_v2,
    )
    retrieved = clean_db.get("w1")
    assert retrieved.labels == labels_v2
