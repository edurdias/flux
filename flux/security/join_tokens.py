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

from sqlalchemy import Column, DateTime, String, or_

from flux.models import Base, RepositoryFactory


def _utcnow() -> datetime:
    # Naive UTC, matching the other timestamp columns (SQLite stores naive
    # datetimes; comparisons in SQL must not mix aware and naive).
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WorkerJoinTokenModel(Base):
    __tablename__ = "worker_join_tokens"

    id = Column(String, primary_key=True, nullable=False, default=lambda: uuid4().hex)
    # unique=True already creates the lookup index on every backend we support.
    token_hash = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    used_by = Column(String, nullable=True)
    created_by = Column(String, nullable=True)
    # The worker name this token authorizes. NULL means unbound: any name may
    # claim it, which is how tokens minted before binding existed behave.
    subject = Column(String, nullable=True)
    # Retired before its TTL (issue #197). Soft delete: purge_expired stays the
    # single reaper, and the row keeps who minted it and when.
    revoked_at = Column(DateTime, nullable=True)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def mint(
    ttl_seconds: int,
    *,
    subject: str | None = None,
    created_by: str | None = None,
) -> tuple[str, datetime]:
    """Create a join token; returns (plaintext, expires_at).

    The plaintext is never stored — surface it to the caller once.

    ``subject`` binds the token to one worker name, so it cannot be used to
    register under a different identity. Leaving it unset keeps the older
    unbound behaviour, where any name may claim the token.
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
                subject=subject,
            ),
        )
        session.commit()
    return token, expires_at


def claim(token: str, worker_name: str) -> bool:
    """Atomically consume a live join token for a registering worker.

    Returns True when this call claimed the token; a used, expired, unknown,
    or wrongly-addressed token returns False. Single UPDATE statement, so two
    racing registrations cannot both succeed.

    A revoked token is as dead as a used one: the filter rides in the same
    WHERE clause, so revocation takes effect on the next attempt with no
    window between the operator's call and the token becoming unusable.

    A token minted with a ``subject`` only claims for that worker name. The
    check rides in the UPDATE's WHERE clause rather than a prior SELECT, so a
    mismatched name matches no row: the attempt neither succeeds nor consumes
    the token, leaving the legitimate worker's credential live.
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
                WorkerJoinTokenModel.revoked_at.is_(None),
                WorkerJoinTokenModel.expires_at > now,
                or_(
                    WorkerJoinTokenModel.subject.is_(None),
                    WorkerJoinTokenModel.subject == worker_name,
                ),
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


def outstanding() -> list[dict]:
    """Live tokens: minted, unused, unrevoked, unexpired.

    Never returns the token or its hash — the plaintext is unrecoverable by
    design, and the hash is a credential-equivalent for an offline guesser.
    """
    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        rows = (
            session.query(WorkerJoinTokenModel)
            .filter(
                WorkerJoinTokenModel.used_at.is_(None),
                WorkerJoinTokenModel.revoked_at.is_(None),
                WorkerJoinTokenModel.expires_at > _utcnow(),
            )
            .order_by(WorkerJoinTokenModel.created_at.desc())
            .all()
        )
        return [
            {
                "id": row.id,
                "subject": row.subject,
                "created_at": row.created_at,
                "expires_at": row.expires_at,
                "created_by": row.created_by,
            }
            for row in rows
        ]


def revoke(token_id: str) -> bool:
    """Retire one live token. Returns False if it was already spent or gone."""
    return _revoke_where(WorkerJoinTokenModel.id == token_id) == 1


def revoke_for_subject(subject: str) -> int:
    """Retire every live token bound to ``subject``. Returns how many.

    The useful shape when revoking alongside a ban: the caller knows the
    worker name and does not track token ids. Unbound tokens are deliberately
    untouched — they carry no subject, so "every token for this worker" cannot
    include them without also retiring credentials meant for other workers.
    """
    if not subject:
        raise ValueError("subject must be a non-empty string")
    return _revoke_where(WorkerJoinTokenModel.subject == subject)


def _revoke_where(condition) -> int:
    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        now = _utcnow()
        revoked = (
            session.query(WorkerJoinTokenModel)
            .filter(
                condition,
                # Only live rows: revoking a spent token would rewrite history,
                # and the count is what the caller reports to an operator.
                WorkerJoinTokenModel.used_at.is_(None),
                WorkerJoinTokenModel.revoked_at.is_(None),
                WorkerJoinTokenModel.expires_at > now,
            )
            .update({"revoked_at": now}, synchronize_session=False)
        )
        session.commit()
        return revoked
