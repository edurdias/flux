from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from flux.task import task
from flux.tasks.mcp.discovery import ToolSet, ToolSetOutputStorage
from flux.tasks.mcp.tool_builder import build_tools
from flux.output_storage import OutputStorageReference


SAMPLE_SCHEMAS = [
    {
        "name": "get_weather",
        "description": "Get weather",
        "inputSchema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
    {
        "name": "search",
        "description": "Search",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]


def _make_toolset():
    mock_client = AsyncMock()
    tools_dict = build_tools(SAMPLE_SCHEMAS, mock_client, "test")
    return ToolSet(tools_dict, SAMPLE_SCHEMAS)


def test_toolset_attribute_access():
    ts = _make_toolset()
    assert isinstance(ts.get_weather, task)
    assert isinstance(ts.search, task)


def test_toolset_attribute_error():
    ts = _make_toolset()
    with pytest.raises(AttributeError):
        _ = ts.nonexistent


def test_toolset_iteration():
    ts = _make_toolset()
    items = list(ts)
    assert len(items) == 2
    assert all(isinstance(t, task) for t in items)


def test_toolset_len():
    ts = _make_toolset()
    assert len(ts) == 2


def test_toolset_contains():
    ts = _make_toolset()
    assert "get_weather" in ts
    assert "nonexistent" not in ts


def test_toolset_schemas_property():
    ts = _make_toolset()
    assert ts.schemas == SAMPLE_SCHEMAS


def test_toolset_output_storage_round_trip():
    mock_client = AsyncMock()
    tools_dict = build_tools(SAMPLE_SCHEMAS, mock_client, "test")
    ts = ToolSet(tools_dict, SAMPLE_SCHEMAS)

    storage = ToolSetOutputStorage(mock_client, "test")
    ref = storage.store("discover_123", ts)

    assert isinstance(ref, OutputStorageReference)
    assert ref.storage_type == "mcp_toolset"
    assert ref.metadata["schemas"] == SAMPLE_SCHEMAS

    restored = storage.retrieve(ref)
    assert isinstance(restored, ToolSet)
    assert len(restored) == 2
    assert isinstance(restored.get_weather, task)
    assert isinstance(restored.search, task)
