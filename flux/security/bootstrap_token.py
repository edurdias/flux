"""Bootstrap token: resolution, persistence, and rotation.

The bootstrap token authenticates a worker's first call to ``POST /workers/register``.
It is a long-lived shared secret between the server and any worker that wants to join.

Resolution order (most specific wins):

1. Caller-supplied value, typically from ``FLUX_WORKERS__BOOTSTRAP_TOKEN`` env var or
   ``[flux.workers] bootstrap_token`` in flux.toml. Used as-is if non-empty.
2. Persisted file at ``<home>/bootstrap-token`` (mode 0600). Used as-is if it exists
   and is non-empty.
3. Auto-generated via ``secrets.token_hex(32)``, persisted to the same path with mode
   0600, and a one-line WARNING is logged so operators can capture it.

Workers do NOT auto-generate; they must be supplied a token (env var, config, or CLI).
Auto-generation is server-side only because worker hosts typically do not share a
filesystem with the server.
"""

from __future__ import annotations

import logging
import secrets
import stat
from pathlib import Path

logger = logging.getLogger(__name__)

TOKEN_FILENAME = "bootstrap-token"
_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR  # 0600


def _path(home: str | Path) -> Path:
    return Path(home) / TOKEN_FILENAME


def read_persisted(home: str | Path) -> str | None:
    """Return the persisted bootstrap token if present, else None."""
    p = _path(home)
    if not p.exists():
        return None
    return p.read_text().strip() or None


def write(home: str | Path, token: str) -> Path:
    """Persist ``token`` to ``<home>/bootstrap-token`` with mode 0600."""
    p = _path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(token)
    p.chmod(_FILE_MODE)
    return p


def generate() -> str:
    return secrets.token_hex(32)


def resolve_or_generate(home: str | Path, configured: str | None) -> tuple[str, bool]:
    """Resolve the active bootstrap token; generate + persist on first call.

    Returns ``(token, generated_now)`` where ``generated_now`` is True only when
    this call wrote a brand-new token to disk.
    """
    if configured:
        return configured, False
    persisted = read_persisted(home)
    if persisted:
        return persisted, False
    token = generate()
    path = write(home, token)
    logger.warning(
        "Generated bootstrap token at %s. Workers must use this token to register; "
        "retrieve via 'flux server bootstrap-token'.",
        path,
    )
    return token, True


def rotate(home: str | Path) -> str:
    """Force-generate a new token and persist it. Returns the new token."""
    token = generate()
    write(home, token)
    return token
