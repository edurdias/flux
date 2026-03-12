"""Tests for observability configuration."""

from flux.observability.config import ObservabilityConfig


class TestObservabilityConfig:
    def test_defaults(self):
        config = ObservabilityConfig()
        assert config.enabled is False
        assert config.service_name == "flux"
        assert config.otlp_endpoint is None
        assert config.otlp_protocol == "grpc"
        assert config.prometheus_enabled is True
        assert config.trace_sample_rate == 1.0
        assert config.metric_export_interval == 60
        assert config.resource_attributes == {}

    def test_custom_values(self):
        config = ObservabilityConfig(
            enabled=True,
            service_name="flux-prod",
            otlp_endpoint="http://collector:4317",
            otlp_protocol="http/protobuf",
            prometheus_enabled=False,
            trace_sample_rate=0.5,
            metric_export_interval=30,
            resource_attributes={"env": "production"},
        )
        assert config.enabled is True
        assert config.service_name == "flux-prod"
        assert config.otlp_endpoint == "http://collector:4317"
        assert config.otlp_protocol == "http/protobuf"
        assert config.prometheus_enabled is False
        assert config.trace_sample_rate == 0.5
        assert config.metric_export_interval == 30
        assert config.resource_attributes == {"env": "production"}

    def test_flux_config_includes_observability(self):
        from flux.config import FluxConfig

        config = FluxConfig()
        assert hasattr(config, "observability")
        assert config.observability.enabled is False
