"""Tests for observability provider setup/shutdown lifecycle."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from flux.observability.config import ObservabilityConfig


@pytest.fixture(autouse=True)
def clean_observability_state():
    """Ensure observability is shut down after each test to prevent state leakage."""
    yield
    from flux.observability import shutdown

    shutdown()


class TestProviderLifecycle:
    def test_setup_sets_enabled_flag(self):
        from flux.observability import is_enabled, setup

        assert is_enabled() is False
        config = ObservabilityConfig(enabled=True, prometheus_enabled=False)
        setup(config)
        assert is_enabled() is True

    def test_setup_disabled_is_noop(self):
        from flux.observability import is_enabled, setup

        config = ObservabilityConfig(enabled=False)
        setup(config)
        assert is_enabled() is False

    def test_setup_missing_packages_raises(self):
        from flux.observability.provider import _check_dependencies

        with patch.dict("sys.modules", {"opentelemetry": None}):
            with pytest.raises(ImportError, match="observability"):
                _check_dependencies()

    def test_shutdown_without_setup_is_safe(self):
        from flux.observability import shutdown

        shutdown()  # Should not raise

    def test_get_meter_returns_meter(self):
        from flux.observability import get_meter, setup

        config = ObservabilityConfig(enabled=True, prometheus_enabled=False)
        setup(config)
        meter = get_meter("test")
        assert meter is not None

    def test_get_tracer_returns_tracer(self):
        from flux.observability import get_tracer, setup

        config = ObservabilityConfig(enabled=True, prometheus_enabled=False)
        setup(config)
        tracer = get_tracer("test")
        assert tracer is not None

    def test_get_meter_returns_none_when_disabled(self):
        from flux.observability import get_meter

        assert get_meter("test") is None

    def test_get_tracer_returns_none_when_disabled(self):
        from flux.observability import get_tracer

        assert get_tracer("test") is None

    def test_get_metrics_returns_none_when_disabled(self):
        from flux.observability import get_metrics

        assert get_metrics() is None

    def test_shutdown_resets_enabled_flag(self):
        from flux.observability import is_enabled, setup, shutdown

        config = ObservabilityConfig(enabled=True, prometheus_enabled=False)
        setup(config)
        assert is_enabled() is True
        shutdown()
        assert is_enabled() is False
