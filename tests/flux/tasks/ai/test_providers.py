"""OpenAI-compatible provider descriptors (issue #141B): validation,
registry precedence, config-declared rows, key resolution, client caching,
and agent() fall-through."""

from __future__ import annotations

import pytest

from flux.config import Configuration
from flux.errors import ModelProviderError
from flux.tasks.ai.providers import (
    ProviderDescriptor,
    _clients,
    _registered,
    _resolve_api_key,
    build_openai_compatible_provider,
    get_provider_descriptor,
    invalidate,
    register_provider,
    registered_provider_names,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    _registered.clear()
    _clients.clear()
    yield
    _registered.clear()
    _clients.clear()
    Configuration.get().reset()


class TestDescriptor:
    def test_valid_descriptor(self):
        d = ProviderDescriptor(
            name="deepseek",
            base_url="https://api.deepseek.com/v1",
            api_key_env="DEEPSEEK_API_KEY",
            models=("deepseek-chat",),
        )
        assert d.name == "deepseek"

    def test_name_rejects_structure_characters(self):
        for bad in ("deep/seek", "deep:seek", ""):
            with pytest.raises(ValueError, match="bare prefix|name"):
                ProviderDescriptor(name=bad, base_url="https://x.example")

    def test_builtin_names_cannot_be_shadowed(self):
        for builtin in ("ollama", "openai", "anthropic", "google"):
            with pytest.raises(ValueError, match="built-in"):
                ProviderDescriptor(name=builtin, base_url="https://x.example")

    def test_base_url_must_be_http(self):
        with pytest.raises(ValueError, match="http"):
            ProviderDescriptor(name="x", base_url="ftp://nope")


class TestRegistry:
    def test_programmatic_registration_resolves(self):
        d = ProviderDescriptor(name="fireworks", base_url="https://api.fireworks.ai/v1")
        register_provider(d)
        assert get_provider_descriptor("fireworks") is d
        assert "fireworks" in registered_provider_names()

    def test_unknown_prefix_resolves_to_none(self):
        assert get_provider_descriptor("nope") is None

    def test_config_declared_descriptor_resolves(self):
        Configuration.get().override(
            ai={
                "providers": {
                    "deepseek": {
                        "base_url": "https://api.deepseek.com/v1",
                        "api_key_env": "DEEPSEEK_API_KEY",
                        "models": ["deepseek-chat"],
                    },
                },
            },
        )
        d = get_provider_descriptor("deepseek")
        assert d is not None
        assert d.base_url == "https://api.deepseek.com/v1"
        assert d.models == ("deepseek-chat",)
        assert "deepseek" in registered_provider_names()

    def test_programmatic_wins_over_config(self):
        Configuration.get().override(
            ai={"providers": {"vendor": {"base_url": "https://config.example"}}},
        )
        register_provider(ProviderDescriptor(name="vendor", base_url="https://code.example"))
        resolved = get_provider_descriptor("vendor")
        assert resolved is not None and resolved.base_url == "https://code.example"


class TestApiKeyResolution:
    async def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("VENDOR_KEY", "sk-from-env")
        d = ProviderDescriptor(name="v", base_url="https://x.example", api_key_env="VENDOR_KEY")
        assert await _resolve_api_key(d) == "sk-from-env"

    async def test_missing_key_raises_actionable_error(self, monkeypatch):
        monkeypatch.delenv("VENDOR_KEY", raising=False)
        d = ProviderDescriptor(name="v", base_url="https://x.example", api_key_env="VENDOR_KEY")
        with pytest.raises(ModelProviderError, match="VENDOR_KEY"):
            await _resolve_api_key(d)

    async def test_keyless_endpoint_gets_placeholder(self):
        d = ProviderDescriptor(name="local", base_url="http://localhost:8001/v1")
        assert await _resolve_api_key(d) == "flux-no-key"


class TestClientCache:
    async def test_client_cached_and_invalidated(self, monkeypatch):
        pytest.importorskip("openai")
        monkeypatch.setenv("VENDOR_KEY", "sk-test")
        d = ProviderDescriptor(name="v", base_url="https://x.example/v1", api_key_env="VENDOR_KEY")
        from flux.tasks.ai.providers import _get_client

        first = await _get_client(d)
        assert await _get_client(d) is first
        invalidate("v")
        assert await _get_client(d) is not first

    async def test_register_invalidates_stale_client(self, monkeypatch):
        pytest.importorskip("openai")
        monkeypatch.setenv("VENDOR_KEY", "sk-test")
        d = ProviderDescriptor(name="v", base_url="https://x.example/v1", api_key_env="VENDOR_KEY")
        from flux.tasks.ai.providers import _get_client

        first = await _get_client(d)
        register_provider(d)
        assert await _get_client(d) is not first


class TestAgentFallThrough:
    async def test_unknown_provider_error_names_registration_path(self):
        from flux.tasks.ai import agent

        with pytest.raises(ValueError, match=r"flux\.ai\.providers"):
            await agent("test", model="nope/some-model")

    async def test_unknown_provider_error_lists_registered(self):
        from flux.tasks.ai import agent

        register_provider(ProviderDescriptor(name="deepseek", base_url="https://x.example/v1"))
        with pytest.raises(ValueError, match="deepseek"):
            await agent("test", model="nope/some-model")

    async def test_registered_descriptor_builds_agent(self, monkeypatch):
        pytest.importorskip("openai")
        from flux.tasks.ai import agent

        monkeypatch.setenv("VENDOR_KEY", "sk-test")
        register_provider(
            ProviderDescriptor(
                name="myvllm",
                base_url="http://localhost:8001/v1",
                api_key_env="VENDOR_KEY",
            ),
        )
        # Colon-tagged model name survives resolution intact.
        a = await agent("test", model="myvllm/qwen2.5-coder:32b")
        assert a.name == "agent_myvllm_qwen2_5_coder_32b"

    async def test_two_compat_vendors_coexist(self, monkeypatch):
        pytest.importorskip("openai")
        from flux.tasks.ai import agent
        from flux.tasks.ai.providers import _get_client

        monkeypatch.setenv("A_KEY", "sk-a")
        monkeypatch.setenv("B_KEY", "sk-b")
        da = ProviderDescriptor(
            name="vendora",
            base_url="https://a.example/v1",
            api_key_env="A_KEY",
        )
        db = ProviderDescriptor(
            name="vendorb",
            base_url="https://b.example/v1",
            api_key_env="B_KEY",
        )
        register_provider(da)
        register_provider(db)
        agent_a = await agent("test", model="vendora/model-1")
        agent_b = await agent("test", model="vendorb/model-2")
        assert agent_a.name != agent_b.name
        client_a, client_b = await _get_client(da), await _get_client(db)
        assert client_a is not client_b
        assert str(client_a.base_url).startswith("https://a.example")
        assert str(client_b.base_url).startswith("https://b.example")

    async def test_build_provider_reuses_openai_formatter(self, monkeypatch):
        pytest.importorskip("openai")
        from flux.tasks.ai.openai import OpenAIFormatter

        monkeypatch.setenv("VENDOR_KEY", "sk-test")
        d = ProviderDescriptor(name="v", base_url="https://x.example/v1", api_key_env="VENDOR_KEY")
        llm_task, formatter = await build_openai_compatible_provider(d, "model-1")
        assert isinstance(formatter, OpenAIFormatter)
        assert llm_task.name == "v_llm"

    def test_invalid_config_row_raises_named_error(self):
        Configuration.get().override(
            ai={"providers": {"bad": {"base_url": "not-a-url"}}},
        )
        with pytest.raises(ModelProviderError, match=r"flux\.ai\.providers\.bad"):
            get_provider_descriptor("bad")
