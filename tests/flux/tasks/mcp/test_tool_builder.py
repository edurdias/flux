from __future__ import annotations

import inspect
from typing import get_type_hints
from unittest.mock import AsyncMock

from flux.task import task
from flux.tasks.mcp.tool_builder import build_tool_task, build_tools, JSON_TYPE_MAP


def test_json_type_map():
    assert JSON_TYPE_MAP["string"] is str
    assert JSON_TYPE_MAP["integer"] is int
    assert JSON_TYPE_MAP["number"] is float
    assert JSON_TYPE_MAP["boolean"] is bool
    assert JSON_TYPE_MAP["object"] is dict
    assert JSON_TYPE_MAP["array"] is list


def test_build_tool_task_creates_flux_task():
    schema = {
        "name": "get_weather",
        "description": "Get weather for a city",
        "inputSchema": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
            },
            "required": ["city"],
        },
    }
    mock_client = AsyncMock()
    t = build_tool_task(schema, mock_client, "weather")
    assert isinstance(t, task)
    assert t.name == "mcp_weather_get_weather"


def test_build_tool_task_has_correct_signature():
    schema = {
        "name": "search",
        "description": "Search for things",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    }
    mock_client = AsyncMock()
    t = build_tool_task(schema, mock_client, "test")
    sig = inspect.signature(t.func)
    params = list(sig.parameters.keys())
    assert "query" in params
    assert "limit" in params
    hints = get_type_hints(t.func)
    assert hints["query"] is str
    assert hints["limit"] is int


def test_build_tool_task_required_vs_optional():
    schema = {
        "name": "search",
        "description": "",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    }
    mock_client = AsyncMock()
    t = build_tool_task(schema, mock_client, "test")
    sig = inspect.signature(t.func)
    assert sig.parameters["query"].default is inspect.Parameter.empty
    assert sig.parameters["limit"].default is None


def test_build_tool_task_preserves_description():
    schema = {
        "name": "search",
        "description": "Search the web",
        "inputSchema": {"type": "object", "properties": {}},
    }
    mock_client = AsyncMock()
    t = build_tool_task(schema, mock_client, "test")
    assert t.func.__doc__ == "Search the web"


def test_build_tool_task_with_options():
    schema = {
        "name": "search",
        "description": "",
        "inputSchema": {"type": "object", "properties": {}},
    }
    mock_client = AsyncMock()
    t = build_tool_task(
        schema,
        mock_client,
        "test",
        retry_max_attempts=3,
        timeout=30,
    )
    assert t.retry_max_attempts == 3
    assert t.timeout == 30


def test_build_tool_task_with_options_override():
    schema = {
        "name": "search",
        "description": "",
        "inputSchema": {"type": "object", "properties": {}},
    }
    mock_client = AsyncMock()
    t = build_tool_task(schema, mock_client, "test", timeout=30)
    t2 = t.with_options(timeout=60)
    assert t2.timeout == 60
    assert t2.name == "mcp_test_search"


def test_build_tools_creates_dict():
    schemas = [
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
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        },
    ]
    mock_client = AsyncMock()
    tools = build_tools(schemas, mock_client, "svc")
    assert "get_weather" in tools
    assert "search" in tools
    assert isinstance(tools["get_weather"], task)
    assert isinstance(tools["search"], task)
