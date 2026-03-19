from __future__ import annotations

from unittest.mock import AsyncMock

from flux.tasks.ai.tool_executor import build_tool_schemas
from flux.tasks.mcp.tool_builder import build_tools


SAMPLE_SCHEMAS = [
    {
        "name": "get_weather",
        "description": "Get weather for a city",
        "inputSchema": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "unit": {"type": "string"},
            },
            "required": ["city"],
        },
    },
    {
        "name": "search",
        "description": "Search the web",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
]


def test_build_tool_schemas_from_mcp_tools():
    mock_client = AsyncMock()
    tools_dict = build_tools(SAMPLE_SCHEMAS, mock_client, "test")
    tool_list = list(tools_dict.values())

    schemas = build_tool_schemas(tool_list)
    assert len(schemas) == 2

    weather_schema = next(s for s in schemas if s["name"] == "get_weather")
    assert weather_schema["description"] == "Get weather for a city"
    assert "city" in weather_schema["parameters"]["properties"]
    assert weather_schema["parameters"]["properties"]["city"]["type"] == "string"
    assert "city" in weather_schema["parameters"]["required"]

    search_schema = next(s for s in schemas if s["name"] == "search")
    assert "query" in search_schema["parameters"]["properties"]
    assert search_schema["parameters"]["properties"]["limit"]["type"] == "integer"


def test_toolset_passable_to_build_tool_schemas():
    from flux.tasks.mcp.discovery import ToolSet

    mock_client = AsyncMock()
    tools_dict = build_tools(SAMPLE_SCHEMAS, mock_client, "test")
    ts = ToolSet(tools_dict, SAMPLE_SCHEMAS)

    schemas = build_tool_schemas(list(ts))
    assert len(schemas) == 2
