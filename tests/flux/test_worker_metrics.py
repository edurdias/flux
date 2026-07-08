"""Tests for the worker-side metrics provider (collection + pong payload)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from flux.worker import Worker
from tests.flux.test_worker_checkpoint import make_worker


def sample_provider() -> dict[str, float]:
    """Referenced by dotted path in the loading tests."""
    return {"fitness": 0.5}


class TestProviderLoading:
    def test_loads_callable_by_dotted_path(self):
        provider = Worker._load_metrics_provider(
            "tests.flux.test_worker_metrics:sample_provider",
        )
        assert provider is sample_provider

    @pytest.mark.parametrize(
        "spec",
        [
            "not.a.real.module:collect",
            "tests.flux.test_worker_metrics:missing",
            "tests.flux.test_worker_metrics",  # no callable part
            "tests.flux.test_worker_metrics:pytest",  # module attr, not ours but callable... use non-callable
        ],
    )
    def test_bad_specs_disable_the_provider(self, spec):
        if spec.endswith(":pytest"):
            spec = "tests.flux.test_worker_metrics:__doc__"  # non-callable attribute
        assert Worker._load_metrics_provider(spec) is None

    def test_none_spec_is_disabled(self):
        assert Worker._load_metrics_provider(None) is None


class TestCollection:
    @pytest.mark.asyncio
    async def test_sync_provider_collected_and_validated(self):
        worker = make_worker()
        worker._metrics_provider = lambda: {"queue": 3}
        worker._metrics_interval = 60.0

        assert await worker._collect_metrics() == {"queue": 3.0}

    @pytest.mark.asyncio
    async def test_async_provider_supported(self):
        worker = make_worker()

        async def provider():
            return {"latency": 1.5}

        worker._metrics_provider = provider
        worker._metrics_interval = 60.0

        assert await worker._collect_metrics() == {"latency": 1.5}

    @pytest.mark.asyncio
    async def test_snapshot_cached_within_interval(self):
        worker = make_worker()
        provider = MagicMock(return_value={"queue": 1.0})
        worker._metrics_provider = provider
        worker._metrics_interval = 60.0

        await worker._collect_metrics()
        await worker._collect_metrics()

        provider.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_payload_keeps_previous_snapshot(self):
        worker = make_worker()
        worker._metrics_provider = lambda: {"queue": 1.0}
        worker._metrics_interval = 0.0001

        first = await worker._collect_metrics()
        assert first == {"queue": 1.0}

        worker._metrics_provider = lambda: {"queue": "busted"}
        await asyncio.sleep(0.001)
        assert await worker._collect_metrics() == {"queue": 1.0}

    @pytest.mark.asyncio
    async def test_provider_exception_keeps_previous_snapshot(self):
        worker = make_worker()
        worker._metrics_provider = lambda: {"queue": 2.0}
        worker._metrics_interval = 0.0001

        assert await worker._collect_metrics() == {"queue": 2.0}

        def boom():
            raise RuntimeError("collector down")

        worker._metrics_provider = boom
        await asyncio.sleep(0.001)
        assert await worker._collect_metrics() == {"queue": 2.0}

    @pytest.mark.asyncio
    async def test_no_provider_returns_none(self):
        worker = make_worker()
        worker._metrics_provider = None

        assert await worker._collect_metrics() is None


class TestPongPayload:
    @pytest.mark.asyncio
    async def test_pong_carries_metrics_when_available(self):
        worker = make_worker()
        worker._metrics_provider = lambda: {"queue": 4.0}
        worker._metrics_interval = 60.0
        captured = {}

        async def capture_post(url, **kwargs):
            captured["json"] = kwargs.get("json")
            response = MagicMock()
            response.status_code = 200
            return response

        worker._authorized_post = capture_post

        await worker._send_pong()

        assert captured["json"] == {"healthy": True, "metrics": {"queue": 4.0}}

    @pytest.mark.asyncio
    async def test_pong_omits_metrics_without_provider(self):
        worker = make_worker()
        worker._metrics_provider = None
        captured = {}

        async def capture_post(url, **kwargs):
            captured["json"] = kwargs.get("json")
            response = MagicMock()
            response.status_code = 200
            return response

        worker._authorized_post = capture_post

        await worker._send_pong()

        assert captured["json"] == {"healthy": True}
