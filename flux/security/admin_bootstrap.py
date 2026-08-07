"""First-admin bootstrap (issue #154).

API keys are minted through ``POST /admin/principals/{subject}/keys``,
which requires an admin — so the first admin used to be reachable only by
running the server with auth disabled. This module is the ``kubeadm
admin.conf`` analog: on the first auth-enabled start with **no admin
principal**, it mints a random admin API key, stores only its hash in the
database bound to the built-in ``admin`` role, and writes the plaintext to
``<home>/admin-bootstrap-key`` (mode 0600).

Load-bearing properties (deliberately mirroring the worker bootstrap
token and kubeadm):

- **Generated per-install**, never a fixed default (CWE-798).
- **Delivered via a host-local 0600 file, not over the network** — the
  boundary is "can read the server host's disk".
- **Init-only**: seeding is a no-op whenever any enabled principal already
  holds the admin role, so restarts never re-mint and deleting the
  bootstrap principal after creating real admins retires it for good.
- **Rotatable** via ``flux server admin-key --rotate``.
- **Bound to the built-in admin role**, not a bespoke grant.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING
from collections.abc import Callable

from flux.security.bootstrap_token import read_persisted as _read_file
from flux.security.bootstrap_token import write as _write_file

if TYPE_CHECKING:
    from flux.security.auth_service import AuthService
    from flux.security.principals import PrincipalRegistry

logger = logging.getLogger(__name__)

ADMIN_KEY_FILENAME = "admin-bootstrap-key"
ADMIN_SUBJECT = "admin"
ADMIN_ISSUER = "flux"
ADMIN_KEY_NAME = "bootstrap"
ADMIN_ROLE = "admin"


def read_persisted(home: str | Path) -> str | None:
    """Return the persisted admin bootstrap key if present, else None."""
    return _read_file(home, ADMIN_KEY_FILENAME)


def has_admin_principal(session_factory: Callable) -> bool:
    """True if any enabled principal holds the admin role.

    This is the init-only guard: once an admin exists — bootstrap-seeded or
    operator-created — seeding never runs again.
    """
    from flux.security.principals import PrincipalModel, PrincipalRoleModel

    session = session_factory()
    try:
        row = (
            session.query(PrincipalRoleModel)
            .join(PrincipalModel, PrincipalModel.id == PrincipalRoleModel.principal_id)
            .filter(
                PrincipalRoleModel.role_name == ADMIN_ROLE,
                PrincipalModel.enabled.is_(True),
            )
            .first()
        )
        return row is not None
    finally:
        session.close()


async def _mint(
    auth_service: AuthService,
    registry: PrincipalRegistry,
    home: str | Path,
) -> str:
    """Create/repair the bootstrap admin principal and mint a fresh key.

    Idempotent against partial prior runs: an existing principal is reused,
    the role grant is re-asserted, and a stale key of the same name is
    revoked before the new one is created.
    """
    principal = registry.find(ADMIN_SUBJECT, ADMIN_ISSUER)
    if principal is None:
        # service_account, not user: the API-key provider only authenticates
        # service accounts (an API key is a machine credential), and this
        # principal exists solely to hold the bootstrap key — a user-typed
        # principal here mints a key that can never authenticate (issue #170).
        principal = registry.create(
            type="service_account",
            subject=ADMIN_SUBJECT,
            external_issuer=ADMIN_ISSUER,
            display_name="Bootstrap admin",
            metadata={"via": "admin-bootstrap"},
        )
    elif principal.type != "service_account":
        # Repair a principal seeded by the #154 implementation that minted
        # dead keys: rotating (or re-minting) heals the deployment in place.
        registry.set_type(principal.id, "service_account")
        logger.warning(
            "Repaired bootstrap admin principal '%s': type changed to "
            "'service_account' so its API key can authenticate (issue #170)",
            ADMIN_SUBJECT,
        )
    registry.assign_role(principal.id, ADMIN_ROLE, assigned_by="admin-bootstrap")

    try:
        await auth_service.revoke_api_key(principal.id, ADMIN_KEY_NAME)
    except ValueError:
        pass  # no stale key — the common case
    key = await auth_service.create_api_key(principal.id, ADMIN_KEY_NAME)
    _write_file(home, key, ADMIN_KEY_FILENAME)
    return key


async def ensure_admin_key(
    auth_service: AuthService,
    registry: PrincipalRegistry,
    session_factory: Callable,
    home: str | Path,
) -> str | None:
    """Seed the first admin credential; no-op when an admin already exists.

    Returns the plaintext key when one was minted this call, else None.
    Called from the server's lifespan startup hook when auth is enabled —
    after ``seed_built_in_roles``, so the admin role row exists.
    """
    if has_admin_principal(session_factory):
        return None
    key = await _mint(auth_service, registry, home)
    logger.warning(
        "No admin principal found; generated an admin bootstrap API key at "
        "%s (subject '%s', bound to the '%s' role). Retrieve it via "
        "'flux server admin-key'.",
        Path(home) / ADMIN_KEY_FILENAME,
        ADMIN_SUBJECT,
        ADMIN_ROLE,
    )
    return key


async def rotate(
    auth_service: AuthService,
    registry: PrincipalRegistry,
    home: str | Path,
) -> str:
    """Force-mint a fresh key for the bootstrap admin principal.

    Unlike ``ensure_admin_key`` this runs regardless of other admins — it
    rotates *this* credential, invalidating the previous one (the old hash
    row is replaced).
    """
    return await _mint(auth_service, registry, home)
