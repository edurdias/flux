"""
E2E test for system_tools — verifies tools work within a Flux workflow.

Does NOT require an LLM. Directly calls the tools to verify they function
correctly inside the Flux execution engine (server/worker mode).

Usage (server/worker):
    flux workflow register examples/ai/system_tools_e2e_test.py --server-url http://localhost:8111
    flux workflow run system_tools_e2e_test '{}' --server-url http://localhost:8111 --mode sync
"""

from __future__ import annotations

from typing import Any

from flux import ExecutionContext, workflow
from flux.tasks.ai import system_tools


@workflow
async def system_tools_e2e_test(ctx: ExecutionContext[dict[str, Any]]):
    """E2E test: exercise all 9 system tools without an LLM."""
    import tempfile

    workspace = tempfile.mkdtemp(prefix="flux_e2e_")
    tools = system_tools(workspace=workspace, timeout=10)
    tool_map = {t.func.__name__: t for t in tools}

    results = {}

    # 1. shell
    r = await tool_map["shell"](command="echo hello_flux")
    assert r["status"] == "ok" and "hello_flux" in r["stdout"], f"shell failed: {r}"
    results["shell"] = "ok"

    # 2. write_file
    r = await tool_map["write_file"](path="test.txt", content="hello world")
    assert r["status"] == "ok" and r["created"] is True, f"write_file failed: {r}"
    results["write_file"] = "ok"

    # 3. read_file
    r = await tool_map["read_file"](path="test.txt")
    assert r["status"] == "ok" and "hello world" in r["content"], f"read_file failed: {r}"
    results["read_file"] = "ok"

    # 4. edit_file
    r = await tool_map["edit_file"](path="test.txt", old_string="hello", new_string="goodbye")
    assert r["status"] == "ok" and r["replacements"] == 1, f"edit_file failed: {r}"
    results["edit_file"] = "ok"

    # 5. file_info
    r = await tool_map["file_info"](path="test.txt")
    assert r["status"] == "ok" and r["type"] == "file", f"file_info failed: {r}"
    results["file_info"] = "ok"

    # 6. list_directory
    r = await tool_map["list_directory"](path="")
    assert r["status"] == "ok" and len(r["entries"]) >= 1, f"list_directory failed: {r}"
    results["list_directory"] = "ok"

    # 7. find_files
    r = await tool_map["find_files"](pattern="*.txt")
    assert r["status"] == "ok" and r["total"] >= 1, f"find_files failed: {r}"
    results["find_files"] = "ok"

    # 8. grep
    r = await tool_map["grep"](pattern="goodbye")
    assert r["status"] == "ok" and r["total"] >= 1, f"grep failed: {r}"
    results["grep"] = "ok"

    # 9. directory_tree
    r = await tool_map["directory_tree"](path="")
    assert r["status"] == "ok" and r["files"] >= 1, f"directory_tree failed: {r}"
    results["directory_tree"] = "ok"

    # 10. path escape rejected
    r = await tool_map["read_file"](path="../escape.txt")
    assert (
        r["status"] == "error" and "escape" in r["error"].lower()
    ), f"path escape not blocked: {r}"
    results["path_escape_blocked"] = "ok"

    # 11. blocklist
    r = await tool_map["shell"](command="rm -rf /")
    assert r["status"] == "error" and "blocked" in r["error"].lower(), f"blocklist not working: {r}"
    results["blocklist"] = "ok"

    return {
        "status": "all_passed",
        "tools_tested": len(results),
        "results": results,
        "workspace": workspace,
    }
