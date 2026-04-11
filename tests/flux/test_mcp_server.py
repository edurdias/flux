"""Signature tests for MCP server tools — verify each namespace-aware tool signature."""
from __future__ import annotations

import inspect


def test_mcp_tools_accept_workflow_namespace():
    """Every MCP tool that takes a workflow reference must accept namespace."""
    import flux.mcp_server as src

    source = inspect.getsource(src)

    # Tools that should have both workflow_namespace and workflow_name params
    tools = [
        "get_workflow_details",
        "execute_workflow_async",
        "execute_workflow_sync",
        "resume_workflow_async",
        "resume_workflow_sync",
        "get_execution_status",
    ]

    for tool in tools:
        sig = f"async def {tool}"
        assert sig in source, f"{tool} missing from module"
        idx = source.index(sig)
        # Look in the next ~400 chars for the parameter declarations
        window = source[idx : idx + 500]
        assert "workflow_namespace" in window, f"{tool} missing workflow_namespace parameter"
        assert "workflow_name" in window, f"{tool} missing workflow_name parameter"


def test_mcp_has_list_namespaces_tool():
    import flux.mcp_server as src

    source = inspect.getsource(src)
    assert "async def list_namespaces" in source, "list_namespaces MCP tool missing"


def test_mcp_workflow_tools_include_workflow_namespace_param():
    """All 11 workflow-scoped MCP tools must declare workflow_namespace (covers C-NEW-1 fix)."""
    import flux.mcp_server as src

    source = inspect.getsource(src)
    tools_with_workflow_namespace = [
        "get_workflow_details",
        "execute_workflow_async",
        "execute_workflow_sync",
        "resume_workflow_async",
        "resume_workflow_sync",
        "get_execution_status",
        # Newly fixed (C-NEW-1):
        "cancel_execution",
        "delete_workflow",
        "list_workflow_versions",
        "get_workflow_version",
        "list_workflow_executions",
    ]
    for tool in tools_with_workflow_namespace:
        idx = source.index(f"async def {tool}")
        window = source[idx : idx + 800]
        assert "workflow_namespace" in window, f"{tool} missing workflow_namespace"
