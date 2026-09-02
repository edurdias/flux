"""A GPU with unreadable memory must survive registration (issue #284).

nvidia-smi answers [N/A] for every memory field on unified-memory parts
(GB10 / DGX Spark), so the worker sends None. The row has to accept it, and
the value has to round-trip as None rather than being coerced to zero.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

from flux.worker_registry import (
    DatabaseWorkerRegistry,
    WorkerResouceGPUInfo,
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


def _resources(gpus):
    return WorkerResourcesInfo(
        cpu_total=20,
        cpu_available=20,
        memory_total=120_000_000_000,
        memory_available=120_000_000_000,
        disk_total=1_000_000_000_000,
        disk_free=900_000_000_000,
        gpus=gpus,
    )


def _runtime():
    return WorkerRuntimeInfo(os_name="Linux", os_version="6.11", python_version="3.12.0")


def test_register_gpu_with_unknown_memory(registry):
    registry.register(
        name="spark-1",
        runtime=_runtime(),
        packages=[],
        resources=_resources(
            [WorkerResouceGPUInfo(name="NVIDIA GB10", memory_total=None, memory_available=None)],
        ),
    )

    retrieved = registry.get("spark-1")

    assert [g.name for g in retrieved.resources.gpus] == ["NVIDIA GB10"]
    gpu = retrieved.resources.gpus[0]
    assert gpu.memory_total is None
    assert gpu.memory_available is None


def test_register_gpu_with_known_memory_is_unchanged(registry):
    registry.register(
        name="rtx-1",
        runtime=_runtime(),
        packages=[],
        resources=_resources(
            [
                WorkerResouceGPUInfo(
                    name="NVIDIA GeForce RTX 3080",
                    memory_total=10_737_418_240,
                    memory_available=8_589_934_592,
                ),
            ],
        ),
    )

    gpu = registry.get("rtx-1").resources.gpus[0]
    assert gpu.memory_total == 10_737_418_240
    assert gpu.memory_available == 8_589_934_592
