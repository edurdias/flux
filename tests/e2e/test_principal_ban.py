"""E2E: banned principals cannot re-register a worker (C1 quarantine).

Registration auto-re-enables disabled principals by design (the reaper
disables pruned workers); 'flux principals ban' is the state that wins
over any valid registration credential. Unban lifts the refusal without
re-enabling anything as a side effect.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).parent / "fixtures"

BOOTSTRAP_TOKEN = "e2e-test-bootstrap-token"


def _registration_body(name: str) -> dict:
    return {
        "name": name,
        "runtime": {"os_name": "Linux", "os_version": "6", "python_version": "3.12"},
        "packages": [],
        "resources": {
            "cpu_total": 1,
            "cpu_available": 1,
            "memory_total": 1,
            "memory_available": 1,
            "disk_total": 1,
            "disk_free": 1,
            "gpus": [],
        },
    }


def _register(cli, name: str) -> httpx.Response:
    return httpx.post(
        f"{cli.server_url}/workers/register",
        json=_registration_body(name),
        headers={"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"},
        timeout=30,
    )


def test_banned_principal_cannot_register_until_unbanned(cli):
    subject = f"quarantined-{uuid.uuid4().hex[:8]}"
    cli.principal_create(subject)

    # Sanity: with a valid bootstrap token, registration works.
    assert _register(cli, subject).status_code == 200

    cli.principal_ban(subject, reason="failed recertification")
    shown = cli.principal_show(subject)
    assert shown["banned"] is True
    assert shown["enabled"] is False

    # The quarantine wins over the (still valid) registration credential.
    resp = _register(cli, subject)
    assert resp.status_code == 403
    assert "banned" in resp.json()["detail"]

    cli.principal_unban(subject)
    shown = cli.principal_show(subject)
    assert shown["banned"] is False
    # Unban does not re-enable as a side effect.
    assert shown["enabled"] is False

    # Registration is the sanctioned way back in after the unban.
    assert _register(cli, subject).status_code == 200


def test_ban_is_scoped_to_one_principal(cli):
    banned = f"banned-{uuid.uuid4().hex[:8]}"
    bystander = f"bystander-{uuid.uuid4().hex[:8]}"
    cli.principal_create(banned)
    cli.principal_ban(banned)

    assert _register(cli, banned).status_code == 403
    assert _register(cli, bystander).status_code == 200

    # Cleanup so the quarantined name cannot leak into other tests.
    cli.principal_unban(banned)


def test_banned_flag_surfaces_in_principal_list(cli):
    subject = f"listed-{uuid.uuid4().hex[:8]}"
    cli.principal_create(subject)
    cli.principal_ban(subject)

    listed = {p["subject"]: p for p in cli.principal_list()}
    assert listed[subject]["banned"] is True

    cli.principal_unban(subject)
    listed = {p["subject"]: p for p in cli.principal_list()}
    assert listed[subject]["banned"] is False
