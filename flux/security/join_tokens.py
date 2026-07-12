"""One-time worker join tokens.

The shared ``bootstrap_token`` is a fleet-wide master secret: anyone who
obtains it can register a worker under any name, forever. Join tokens are
the per-registration upgrade path (SEC3): an operator mints a short-lived,
single-use token, hands it to exactly one new worker, and the secret is
worthless the moment it is used (or expires unused).

Only a SHA-256 hash of the token is stored; the plaintext is shown once at
mint time. Claiming is a single atomic UPDATE so two concurrent
registrations cannot both consume the same token.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime, String

from flux.models import Base, RepositoryFactory


def _utcnow() -> datetime:
    # Naive UTC, matching the other timestamp columns (SQLite stores naive
    # datetimes; comparisons in SQL must not mix aware and naive).
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WorkerJoinTokenModel(Base):
    __tablename__ = "worker_join_tokens"

    id = Column(String, primary_key=True, nullable=False, default=lambda: uuid4().hex)
    token_hash = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    used_by = Column(String, nullable=True)
    created_by = Column(String, nullable=True)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def mint(ttl_seconds: int, *, created_by: str | None = None) -> tuple[str, datetime]:
    """Create a join token; returns (plaintext, expires_at).

    The plaintext is never stored — surface it to the caller once.
    """
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    token = secrets.token_urlsafe(32)
    expires_at = _utcnow() + timedelta(seconds=ttl_seconds)
    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        session.add(
            WorkerJoinTokenModel(
                token_hash=_hash(token),
                expires_at=expires_at,
                created_by=created_by,
            ),
        )
        session.commit()
    return token, expires_at


def claim(token: str, worker_name: str) -> bool:
    """Atomically consume a live join token for a registering worker.

    Returns True when this call claimed the token; a used, expired, or
    unknown token returns False. Single UPDATE statement, so two racing
    registrations cannot both succeed.
    """
    if not token:
        return False
    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        now = _utcnow()
        claimed = (
            session.query(WorkerJoinTokenModel)
            .filter(
                WorkerJoinTokenModel.token_hash == _hash(token),
                WorkerJoinTokenModel.used_at.is_(None),
                WorkerJoinTokenModel.expires_at > now,
            )
            .update(
                {"used_at": now, "used_by": worker_name},
                synchronize_session=False,
            )
        )
        session.commit()
        return claimed == 1


def purge_expired(*, older_than_seconds: int = 86400) -> int:
    """Delete tokens whose expiry passed more than the grace window ago.

    Used rows are kept inside the window as an audit trail of recent joins.
    Returns the number of rows removed.
    """
    repo = RepositoryFactory.create_repository()
    cutoff = _utcnow() - timedelta(seconds=older_than_seconds)
    with repo.session() as session:
        removed = (
            session.query(WorkerJoinTokenModel)
            .filter(WorkerJoinTokenModel.expires_at < cutoff)
            .delete(synchronize_session=False)
        )
        session.commit()
        return removed
