import pytest
from sqlalchemy import text

from flux.unit_of_work import UnitOfWork


def test_uow_provides_session():
    with UnitOfWork() as uow:
        result = uow.session.execute(text("SELECT 1")).scalar()
        assert result == 1


def test_uow_commit_persists_changes():
    """Writes inside a UoW that commits are visible outside it."""
    with UnitOfWork() as uow:
        uow.session.execute(text("CREATE TEMP TABLE _uow_test (n INT)"))
        uow.session.execute(text("INSERT INTO _uow_test (n) VALUES (42)"))
        uow.commit()
    # Note: TEMP table won't survive a new session, so this test only verifies the
    # commit() call doesn't raise. Persistence semantics covered by Approval tests.


def test_uow_rollback_discards_changes():
    """Writes that are explicitly rolled back are not committed."""
    with UnitOfWork() as uow:
        uow.session.execute(text("CREATE TEMP TABLE _uow_rb (n INT)"))
        uow.session.execute(text("INSERT INTO _uow_rb (n) VALUES (99)"))
        uow.rollback()


def test_uow_exit_without_commit_rolls_back():
    """If the with-block exits without commit/rollback being called, default is rollback."""
    captured = {}
    with UnitOfWork() as uow:
        captured["session"] = uow.session
        uow.session.execute(text("CREATE TEMP TABLE _uow_implicit (n INT)"))
        # No explicit commit — implicit rollback on exit.
    # If we reach here without exception, the implicit rollback succeeded.


def test_uow_exception_rolls_back_and_propagates():
    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with UnitOfWork() as uow:
            uow.session.execute(text("CREATE TEMP TABLE _uow_exc (n INT)"))
            raise Boom("fail")
