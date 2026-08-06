"""Out-of-band approval notification and reply correlation (issue #144).

Outbound: a webhook notifier fired on approval creation and retracted on
decide/cancel, best-effort — a dead endpoint never fails the execution.
Inbound: a correlation token + intent parsed from a free-form reply drive
the existing decide path.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from flux.approval_notifier import (
    WebhookApprovalNotifier,
    extract_token,
    fire_notify,
    format_token,
    get_notifier,
    parse_intent,
)
from flux.config import Configuration


@pytest.fixture
def webhook_config():
    Configuration.get().override(
        approvals={"webhook_url": "https://hooks.example.com/flux", "webhook_secret": "s3cr3t"},
    )
    yield
    Configuration.get().override(approvals={"webhook_url": None, "webhook_secret": None})


class TestTokens:
    def test_round_trip(self):
        token = format_token("a" * 32)
        assert extract_token(f"please approve {token} thanks") == "a" * 32

    def test_no_token_is_none(self):
        assert extract_token("just chatting about approvals") is None
        assert extract_token("") is None

    def test_malformed_token_is_none(self):
        assert extract_token("[flux-approval:not-hex]") is None
        assert extract_token("[flux-approval:abc]") is None

    def test_case_normalized(self):
        assert extract_token(f"[flux-approval:{'A' * 32}]") == "a" * 32


class TestIntent:
    @pytest.mark.parametrize(
        "text",
        ["approve", "Approved!", "yes, go ahead", "LGTM", "ok", "allow it", "👍"],
    )
    def test_approve_forms(self, text):
        assert parse_intent(text) is True

    @pytest.mark.parametrize(
        "text",
        ["reject", "deny", "no", "Denied.", "👎", "block this"],
    )
    def test_reject_forms(self, text):
        assert parse_intent(text) is False

    def test_no_intent(self):
        assert parse_intent("what is this about?") is None
        assert parse_intent("") is None

    def test_ambiguous_is_no_intent(self):
        """Both intents present must never be misread into either decision."""
        assert parse_intent("no, approve it") is None


class TestWebhookNotifier:
    def test_posts_signed_payload(self):
        notifier = WebhookApprovalNotifier(
            "https://hooks.example.com/flux",
            secret="s3cr3t",
        )
        payload = {"event": "approval.requested", "token": format_token("a" * 32)}

        with patch("httpx.post") as post:
            post.return_value = MagicMock(raise_for_status=MagicMock())
            notifier.notify(payload)

        (url,), kwargs = post.call_args
        assert url == "https://hooks.example.com/flux"
        body = kwargs["content"]
        assert json.loads(body) == payload
        expected = hmac.new(b"s3cr3t", body, hashlib.sha256).hexdigest()
        assert kwargs["headers"]["X-Flux-Signature"] == f"sha256={expected}"

    def test_no_secret_no_signature(self):
        notifier = WebhookApprovalNotifier("https://hooks.example.com/flux")
        with patch("httpx.post") as post:
            post.return_value = MagicMock(raise_for_status=MagicMock())
            notifier.notify({"event": "x"})
        assert "X-Flux-Signature" not in post.call_args.kwargs["headers"]


class TestConfigResolution:
    def test_unset_url_means_no_notifier(self):
        assert get_notifier() is None

    def test_configured_url_builds_webhook(self, webhook_config):
        notifier = get_notifier()
        assert isinstance(notifier, WebhookApprovalNotifier)


def _row():
    row = MagicMock()
    row.id = "b" * 32
    row.execution_id = "exec-1"
    row.task_call_id = "call-1"
    row.workflow_namespace = "default"
    row.workflow_name = "wf"
    row.task_name = "send_email"
    row.requested_at = None
    row.status = MagicMock(value="pending")
    row.approver_subject = None
    row.approver_provider = None
    row.reason = None
    row.scope = None
    row.target_value = None
    return row


class TestFireIsBestEffort:
    def test_fire_with_no_notifier_is_a_noop(self):
        fire_notify(_row())  # must not raise

    def test_dead_endpoint_never_raises_into_caller(self, webhook_config):
        delivered = threading.Event()

        def _boom(*args, **kwargs):
            delivered.set()
            raise RuntimeError("endpoint down")

        with patch("httpx.post", side_effect=_boom):
            fire_notify(_row())  # spawns a daemon thread; must not raise
            assert delivered.wait(timeout=5), "delivery attempt must happen"

    def test_payload_carries_token_and_snapshot(self, webhook_config):
        captured = {}
        done = threading.Event()

        def _capture(url, content=None, headers=None, timeout=None):
            captured["payload"] = json.loads(content)
            done.set()
            return MagicMock(raise_for_status=MagicMock())

        with patch("httpx.post", side_effect=_capture):
            fire_notify(_row())
            assert done.wait(timeout=5)

        payload = captured["payload"]
        assert payload["event"] == "approval.requested"
        assert payload["token"] == format_token("b" * 32)
        assert payload["execution_id"] == "exec-1"
        assert payload["approval"]["task_name"] == "send_email"
