"""A history written before the msgpack codec still reads (#260).

Every deployment upgrading into the codec has execution rows whose
payloads are raw dill streams. Those rows outlive the release that wrote
them, so "old histories still replay" is a compatibility guarantee, not a
nice-to-have.

The legacy database is built here rather than committed as a binary
fixture: AGENTS.md forbids committing `*.db`, and a generated one cannot
drift out of step with the encoding it is supposed to represent. What
makes it a genuine legacy row is that the payload is written exactly the
way the previous version wrote it -- `sign(dill.dumps(value))` straight
into the column, bypassing the current codec entirely.
"""

from __future__ import annotations

import datetime

import dill
import pytest

from flux.config import Configuration


@pytest.fixture
def legacy_db(tmp_path):
    """A database whose event payloads were written the old way."""
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'legacy.db'}")
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()
    yield
    DatabaseRepository._engines.clear()
    Configuration.get().reset()


def _write_legacy_row(execution_id: str, output: object) -> None:
    """Insert an execution whose output column holds a pre-codec payload."""
    from sqlalchemy import text

    from flux.models import ExecutionContextModel, RepositoryFactory, WorkflowModel
    from flux.security.integrity import sign

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        session.add(
            WorkflowModel(
                id="legacy-wf",
                name="legacy_flow",
                version=1,
                imports=[],
                source=b"async def legacy_flow(ctx): pass",
                namespace="default",
            ),
        )
        session.add(
            ExecutionContextModel(
                execution_id=execution_id,
                workflow_id="legacy-wf",
                workflow_name="legacy_flow",
                workflow_namespace="default",
                input=None,
                output=None,
                state="COMPLETED",
            ),
        )
        session.commit()

        # Bypass the ORM's type decorator: this is the previous version's
        # on-disk encoding, dill with a signature and no format tag.
        session.execute(
            text("UPDATE executions SET output = :blob WHERE execution_id = :eid"),
            {"blob": sign(dill.dumps(output)), "eid": execution_id},
        )
        session.commit()


def _read_output(execution_id: str):
    from flux.models import ExecutionContextModel, RepositoryFactory

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        row = session.get(ExecutionContextModel, execution_id)
        return row.output


LEGACY_VALUES = [
    {"status": "ok", "count": 3},
    ["a", "b", "c"],
    "a plain string",
    42,
    datetime.datetime(2026, 1, 2, 3, 4, 5),
    (1, 2, 3),
    {"nested": {"when": datetime.date(2026, 1, 2), "ids": [1, 2]}},
]


@pytest.mark.parametrize("value", LEGACY_VALUES)
def test_a_pre_codec_payload_still_reads(legacy_db, value):
    _write_legacy_row("legacy-exec", value)

    assert _read_output("legacy-exec") == value


def test_a_legacy_row_and_a_new_row_coexist(legacy_db):
    """An upgraded deployment writes new rows beside old ones; both have to
    be readable by the same code, in the same table, without a migration."""
    from flux.models import ExecutionContextModel, RepositoryFactory

    _write_legacy_row("legacy-exec", {"written": "before"})

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        session.add(
            ExecutionContextModel(
                execution_id="new-exec",
                workflow_id="legacy-wf",
                workflow_name="legacy_flow",
                workflow_namespace="default",
                input=None,
                output={"written": "after", "when": datetime.datetime(2026, 6, 1)},
                state="COMPLETED",
            ),
        )
        session.commit()

    assert _read_output("legacy-exec") == {"written": "before"}
    assert _read_output("new-exec") == {
        "written": "after",
        "when": datetime.datetime(2026, 6, 1),
    }


def test_the_new_row_is_not_a_pickle_stream(legacy_db):
    """The upgrade's whole point: new payloads are msgpack, so reading them
    executes no code."""
    from sqlalchemy import text

    from flux.models import ExecutionContextModel, RepositoryFactory
    from flux.security.integrity import verify
    from flux.serialization import is_msgpack_payload

    _write_legacy_row("legacy-exec", {"a": 1})
    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        session.add(
            ExecutionContextModel(
                execution_id="new-exec",
                workflow_id="legacy-wf",
                workflow_name="legacy_flow",
                workflow_namespace="default",
                input=None,
                output={"a": 1},
                state="COMPLETED",
            ),
        )
        session.commit()
        blobs = {
            eid: raw
            for eid, raw in session.execute(
                text("SELECT execution_id, output FROM executions"),
            ).all()
        }

    assert not is_msgpack_payload(verify(bytes(blobs["legacy-exec"])))
    assert is_msgpack_payload(verify(bytes(blobs["new-exec"])))
