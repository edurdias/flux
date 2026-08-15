"""Presentation-boundary secret redaction (issue #147, phase 1).

Known secret values are scrubbed by value identity from execution-read
API responses. The stored event log is untouched — this is presentation
hygiene for the wide ``execution:*:read`` grant, not storage redaction.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flux.security import redaction
from flux.security.redaction import (
    REDACTED,
    collect_secret_values,
    redact_response,
    redact_values,
)


@pytest.fixture(autouse=True)
def clear_cache():
    redaction._cache = None
    yield
    redaction._cache = None


class TestRedactValues:
    def test_bare_string_value(self):
        assert redact_values("sk-live-abcdef123", ["sk-live-abcdef123"]) == REDACTED

    def test_embedded_occurrence(self):
        out = redact_values(
            "Bearer sk-live-abcdef123 granted",
            ["sk-live-abcdef123"],
        )
        assert out == f"Bearer {REDACTED} granted"

    def test_nested_structures(self):
        payload = {
            "events": [
                {"value": {"token": "sk-live-abcdef123", "count": 3}},
                {"value": ["ok", "sk-live-abcdef123"]},
            ],
        }
        out = redact_values(payload, ["sk-live-abcdef123"])
        assert out["events"][0]["value"]["token"] == REDACTED
        assert out["events"][0]["value"]["count"] == 3
        assert out["events"][1]["value"] == ["ok", REDACTED]

    def test_secret_used_as_dict_key(self):
        out = redact_values({"sk-live-abcdef123": "x"}, ["sk-live-abcdef123"])
        assert out == {REDACTED: "x"}

    def test_multiple_secrets_longest_first_leaves_no_fragments(self):
        # "inner" is a substring of "prefix-inner-suffix"; longest-first
        # replacement must not leave recognizable pieces of the longer one.
        values = sorted(
            ["inner-secret", "wrap-inner-secret-tail"],
            key=len,
            reverse=True,
        )
        out = redact_values("saw wrap-inner-secret-tail today", values)
        assert "inner-secret" not in out
        assert "tail" not in out.replace(REDACTED, "")

    def test_non_string_scalars_untouched(self):
        payload = {"n": 42, "f": 1.5, "b": True, "none": None}
        assert redact_values(payload, ["sk-live-abcdef123"]) == payload

    def test_no_values_is_identity(self):
        payload = {"token": "sk-live-abcdef123"}
        assert redact_values(payload, []) is payload


class TestCollectSecretValues:
    async def test_only_long_strings_collected_longest_first(self):
        manager = MagicMock()
        manager.all.return_value = ["a", "b", "c", "d"]
        manager.get = AsyncMock(
            return_value={
                "a": "short",  # < MIN_SECRET_LENGTH
                "b": 123456789,  # not a string
                "c": "sk-live-abcdef123",
                "d": "sk-live-abcdef123-and-more",
            },
        )
        with patch("flux.secret_managers.SecretManager.current", return_value=manager):
            values = await collect_secret_values(refresh=True)
        assert values == ["sk-live-abcdef123-and-more", "sk-live-abcdef123"]

    async def test_empty_store_collects_nothing(self):
        manager = MagicMock()
        manager.all.return_value = []
        manager.get = AsyncMock()
        with patch("flux.secret_managers.SecretManager.current", return_value=manager):
            assert await collect_secret_values(refresh=True) == []
        manager.get.assert_not_awaited()


class TestCollectSecretValuesSync:
    """The write path (a hook's delivery envelope) redacts inside an open
    transaction, so it reads the scrub list without a loop."""

    def test_reads_a_manager_that_offers_a_sync_get(self):
        manager = MagicMock()
        manager.all.return_value = ["token"]
        manager.get_sync.return_value = {"token": "sk-live-abcdef123"}
        manager.get = AsyncMock()

        with patch("flux.secret_managers.SecretManager.current", return_value=manager):
            assert redaction.collect_secret_values_sync(refresh=True) == ["sk-live-abcdef123"]

        manager.get.assert_not_awaited()

    def test_shares_the_ttl_cache_with_the_async_collector(self):
        manager = MagicMock()
        manager.all.return_value = ["token"]
        manager.get_sync.return_value = {"token": "sk-live-abcdef123"}

        with patch("flux.secret_managers.SecretManager.current", return_value=manager):
            redaction.collect_secret_values_sync(refresh=True)
            redaction.collect_secret_values_sync()
            redaction.collect_secret_values_sync()

        assert manager.get_sync.call_count == 1

    def test_falls_back_to_the_coroutine_when_there_is_no_sync_read(self):
        manager = MagicMock(spec=["all", "get"])
        manager.all.return_value = ["token"]
        manager.get = AsyncMock(return_value={"token": "sk-live-abcdef123"})

        with patch("flux.secret_managers.SecretManager.current", return_value=manager):
            assert redaction.collect_secret_values_sync(refresh=True) == ["sk-live-abcdef123"]


class TestRedactResponse:
    def _manager(self):
        manager = MagicMock()
        manager.all.return_value = ["token"]
        manager.get = AsyncMock(return_value={"token": "sk-live-abcdef123"})
        return manager

    async def test_redacts_payload(self):
        with patch(
            "flux.secret_managers.SecretManager.current",
            return_value=self._manager(),
        ):
            out = await redact_response({"output": "sk-live-abcdef123"})
        assert out == {"output": REDACTED}

    async def test_config_gate_off_is_identity(self):
        from flux.config import Configuration

        Configuration.get().override(security={"redact_secrets_in_responses": False})
        try:
            with patch(
                "flux.secret_managers.SecretManager.current",
                return_value=self._manager(),
            ):
                payload = {"output": "sk-live-abcdef123"}
                assert await redact_response(payload) is payload
        finally:
            Configuration.get().override(security={"redact_secrets_in_responses": True})

    async def test_redaction_failure_serves_unredacted(self):
        """Best-effort: a secrets-store failure must not break execution
        reads; the unredacted payload is served and the failure logged."""
        manager = MagicMock()
        manager.all.side_effect = RuntimeError("store down")
        with patch("flux.secret_managers.SecretManager.current", return_value=manager):
            payload = {"output": "sk-live-abcdef123"}
            assert await redact_response(payload) == payload
