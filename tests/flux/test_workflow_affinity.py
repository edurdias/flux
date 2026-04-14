from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

from flux.catalogs import WorkflowCatalog, WorkflowInfo


def test_workflow_info_with_affinity():
    info = WorkflowInfo(
        id="test/wf",
        name="wf",
        imports=[],
        source=b"",
        affinity={"role": "harness"},
    )
    assert info.affinity == {"role": "harness"}


def test_workflow_info_default_affinity():
    info = WorkflowInfo(id="test/wf", name="wf", imports=[], source=b"")
    assert info.affinity is None


def test_workflow_info_to_dict_includes_affinity():
    info = WorkflowInfo(
        id="test/wf",
        name="wf",
        imports=[],
        source=b"",
        affinity={"role": "harness", "env": "sandbox"},
    )
    d = info.to_dict()
    assert d["affinity"] == {"role": "harness", "env": "sandbox"}


def test_workflow_info_to_dict_no_affinity():
    info = WorkflowInfo(id="test/wf", name="wf", imports=[], source=b"")
    d = info.to_dict()
    assert d["affinity"] is None


def test_parse_workflow_with_affinity(clean_db):
    source = b"""
from flux import workflow

@workflow.with_options(affinity={"role": "harness", "browser": "true"})
async def my_agent(ctx):
    pass
"""
    infos = clean_db.parse(source)
    assert len(infos) == 1
    assert infos[0].affinity == {"role": "harness", "browser": "true"}


def test_parse_workflow_without_affinity(clean_db):
    source = b"""
from flux import workflow

@workflow
async def simple(ctx):
    pass
"""
    infos = clean_db.parse(source)
    assert len(infos) == 1
    assert infos[0].affinity is None


@pytest.fixture
def clean_db():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name
    db_url = f"sqlite:///{db_path}"
    with patch("flux.config.Configuration.get") as mock_config:
        mock_config.return_value.settings.database_url = db_url
        mock_config.return_value.settings.database_type = "sqlite"
        mock_config.return_value.settings.security.auth.enabled = False
        yield WorkflowCatalog.create()
    if os.path.exists(db_path):
        os.unlink(db_path)


def test_save_and_get_workflow_with_affinity(clean_db):
    catalog = clean_db
    info = WorkflowInfo(
        id="default/test_affinity",
        name="test_affinity",
        imports=["flux"],
        source=b"async def test_affinity(ctx): pass",
        affinity={"role": "harness"},
    )
    catalog.save([info])
    result = catalog.get("default", "test_affinity")
    assert result.affinity == {"role": "harness"}


def test_save_and_get_workflow_without_affinity(clean_db):
    catalog = clean_db
    info = WorkflowInfo(
        id="default/no_affinity",
        name="no_affinity",
        imports=["flux"],
        source=b"async def no_affinity(ctx): pass",
    )
    catalog.save([info])
    result = catalog.get("default", "no_affinity")
    assert result.affinity is None
