"""Cross-manager transactional context.

Holds a single SQLAlchemy Session. Managers that accept a ``uow=`` argument use
this session instead of opening their own. See spec §4.6.

Flux uses synchronous Session throughout (flux/models.py:70); UoW does too.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from flux.models import RepositoryFactory


class UnitOfWork:
    """Sync transactional context manager.

    Usage:
        with UnitOfWork() as uow:
            context_manager.save(ctx, uow=uow)
            approval_manager.create(..., uow=uow)
            uow.commit()

    If commit() is not called before exit, the session is rolled back. If an
    exception escapes the with-block, the session is rolled back and the
    exception propagates.
    """

    def __init__(self) -> None:
        self._session: Session | None = None
        self._committed = False

    def __enter__(self) -> UnitOfWork:
        repo = RepositoryFactory.create_repository()
        self._session = repo.session()
        self._committed = False
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._session is None:
            return  # __enter__ never ran or already torn down
        try:
            if exc_type is not None or not self._committed:
                self._session.rollback()
        finally:
            self._session.close()
            self._session = None

    @property
    def session(self) -> Session:
        if self._session is None:
            raise RuntimeError("UnitOfWork.session accessed outside of `with` block")
        return self._session

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("UnitOfWork.commit called outside of `with` block")
        self._session.commit()
        self._committed = True

    def rollback(self) -> None:
        if self._session is None:
            raise RuntimeError("UnitOfWork.rollback called outside of `with` block")
        self._session.rollback()
        self._committed = True
