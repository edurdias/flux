"""Out-of-band approval notification and reply correlation (issue #144).

The approval store is already a complete attention queue — pending rows,
decide-exactly-once, first-responder-wins. What was missing is delivery:
nothing told a human an approval was waiting, so polling the CLI was the
only mechanism, on exactly the unattended path approvals exist for.

Two halves:

- **Outbound** — an ``ApprovalNotifier`` fired when a pending approval row
  is created (``notify``) and again when it is decided or cancelled
  (``retract``, so stale prompts can be withdrawn). The shipped
  implementation is a generic webhook: POST the approval payload to a
  configured URL, optionally HMAC-signed. That alone unlocks Slack, Teams,
  PagerDuty, and anything else via the operator's own glue with no vendor
  dependency in core.

- **Inbound** — the delivered payload embeds a correlation token
  (``[flux-approval:<id>]``). ``POST /approvals/resolve`` extracts the
  token from a free-form reply, parses intent, and drives the existing
  decide path — inheriting its authentication, per-task authorization,
  and the decide-once CAS, so duplicate replies and racing operators
  resolve exactly once with a clean "already decided" for everyone else.

Notifier failures never fail the execution or block the pause: fire is
best-effort on a background thread, logged on error, with the row
remaining the source of truth.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import threading
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from flux.models import ApprovalRequestModel

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"\[flux-approval:([0-9a-fA-F]{32})\]")

_APPROVE_WORDS = frozenset(
    {"approve", "approved", "allow", "allowed", "yes", "lgtm", "ok", "okay", "👍", "✅"},
)
_REJECT_WORDS = frozenset(
    {"reject", "rejected", "deny", "denied", "no", "block", "blocked", "👎", "❌"},
)


def format_token(approval_id: str) -> str:
    return f"[flux-approval:{approval_id}]"


def extract_token(text: str) -> str | None:
    """The approval id embedded in a reply, or None."""
    match = TOKEN_RE.search(text or "")
    return match.group(1).lower() if match else None


def parse_intent(text: str) -> bool | None:
    """Approve (True), reject (False), or no clear intent (None).

    A reply containing words from both sets is ambiguous and parses as no
    intent — a human should never have a message misread into the opposite
    decision.
    """
    words = {w.strip(".,!?:;()").lower() for w in (text or "").split()}
    approves = bool(words & _APPROVE_WORDS)
    rejects = bool(words & _REJECT_WORDS)
    if approves == rejects:
        return None
    return approves


class ApprovalNotifier(Protocol):
    def notify(self, payload: dict[str, Any]) -> None: ...

    def retract(self, payload: dict[str, Any]) -> None: ...


class WebhookApprovalNotifier:
    """POST approval lifecycle events to a configured URL.

    When a secret is configured, the body is signed with HMAC-SHA256 and the
    hex digest sent as ``X-Flux-Signature: sha256=<hex>`` so the receiver
    can authenticate the sender. The reply path does NOT trust the webhook:
    resolving goes through the authenticated ``/approvals/resolve`` route
    with ordinary per-task authorization.
    """

    def __init__(self, url: str, secret: str | None = None, timeout: float = 5.0):
        self._url = url
        self._secret = secret
        self._timeout = timeout

    def _post(self, payload: dict[str, Any]) -> None:
        import httpx

        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._secret:
            digest = hmac.new(self._secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
            headers["X-Flux-Signature"] = f"sha256={digest}"
        response = httpx.post(
            self._url,
            content=body,
            headers=headers,
            timeout=self._timeout,
        )
        response.raise_for_status()

    def notify(self, payload: dict[str, Any]) -> None:
        self._post(payload)

    def retract(self, payload: dict[str, Any]) -> None:
        self._post(payload)


def get_notifier() -> ApprovalNotifier | None:
    """The configured notifier, or None when none is configured."""
    from flux.config import Configuration

    approvals = Configuration.get().settings.approvals
    if not approvals.webhook_url:
        return None
    return WebhookApprovalNotifier(
        url=approvals.webhook_url,
        secret=approvals.webhook_secret,
        timeout=approvals.webhook_timeout,
    )


def _payload(event: str, row: ApprovalRequestModel) -> dict[str, Any]:
    from flux.approvals import ApprovalSnapshot

    return {
        "event": event,
        "token": format_token(row.id),
        "execution_id": row.execution_id,
        "task_call_id": row.task_call_id,
        "approval": ApprovalSnapshot.from_model(row).to_dict(),
    }


def _fire(method_name: str, payload: dict[str, Any]) -> None:
    """Deliver on a daemon thread; never raise into the caller.

    Best-effort by contract: the row is the source of truth, and a dead
    notifier endpoint must neither stall the pause nor fail the decide.
    """
    notifier = get_notifier()
    if notifier is None:
        return

    def _deliver() -> None:
        try:
            getattr(notifier, method_name)(payload)
        except Exception:
            logger.error(
                f"Approval notifier {method_name} failed for "
                f"{payload.get('execution_id')}/{payload.get('task_call_id')}",
                exc_info=True,
            )

    threading.Thread(target=_deliver, name="flux-approval-notify", daemon=True).start()


def fire_notify(row: ApprovalRequestModel) -> None:
    """A pending approval was created: tell the operator, with the token."""
    _fire("notify", _payload("approval.requested", row))


def fire_retract(row: ApprovalRequestModel) -> None:
    """The approval was decided or cancelled: retract stale prompts."""
    _fire("retract", _payload("approval.resolved", row))
