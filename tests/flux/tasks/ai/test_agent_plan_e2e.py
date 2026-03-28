"""
End-to-end tests for Agent Plans with a real LLM (Ollama).

These tests verify that planning works through the full stack:
LLM -> tool calls -> plan tools -> framework -> results.

Tests are structured in two groups:
1. Integration tests that verify the plan tools work correctly within
   the agent execution pipeline (deterministic, fast)
2. LLM-driven tests that verify a real model can use the tools
   (non-deterministic, slower, may need a capable model)

Requires: ollama serve with llama3.2 pulled.

Run: uv run python -m pytest tests/flux/tasks/ai/test_agent_plan_e2e.py -v -s --timeout=180
"""

from __future__ import annotations

import json


from flux import ExecutionContext, workflow
from flux.task import task
from flux.tasks.ai import agent
from flux.tasks.ai.agent_plan import build_plan_tools
from flux.tasks.ai.tool_executor import execute_tools

MODEL = "ollama/llama3.2"
TIMEOUT = 120


# ========================================================================
# Integration tests: Plan tools work within the execute_tools pipeline
# These are deterministic and fast (no LLM needed)
# ========================================================================


def test_integration_create_plan_via_execute_tools():
    """create_plan works when called through execute_tools like the agent loop would."""

    @workflow
    async def wf(ctx: ExecutionContext):
        tools, summary_fn = await build_plan_tools()
        result = await execute_tools(
            [
                {
                    "id": "call_1",
                    "name": "create_plan",
                    "arguments": json.dumps(
                        {
                            "steps": json.dumps(
                                [
                                    {"name": "research", "description": "Research the topic."},
                                    {
                                        "name": "analyze",
                                        "description": "Analyze data.",
                                        "depends_on": ["research"],
                                    },
                                ],
                            ),
                        },
                    ),
                },
            ],
            tools,
        )
        return {"result": result, "summary": summary_fn()}

    ctx = wf.run()
    assert ctx.has_succeeded
    result = json.loads(ctx.output["result"][0]["output"])
    assert len(result["steps"]) == 2
    assert result["steps"][0]["name"] == "research"
    assert result["steps"][1]["depends_on"] == ["research"]
    assert ctx.output["summary"] is not None
    assert "0/2" in ctx.output["summary"]


def test_integration_full_plan_lifecycle():
    """Full lifecycle: create -> complete -> get_plan through execute_tools."""

    @workflow
    async def wf(ctx: ExecutionContext):
        tools, summary_fn = await build_plan_tools()

        # Step 1: Create plan
        r1 = await execute_tools(
            [
                {
                    "id": "call_1",
                    "name": "create_plan",
                    "arguments": json.dumps(
                        {
                            "steps": json.dumps(
                                [
                                    {"name": "search", "description": "Search for data."},
                                    {
                                        "name": "analyze",
                                        "description": "Analyze it.",
                                        "depends_on": ["search"],
                                    },
                                ],
                            ),
                        },
                    ),
                },
            ],
            tools,
        )

        # Step 2: Complete first step
        r2 = await execute_tools(
            [
                {
                    "id": "call_2",
                    "name": "mark_step_done",
                    "arguments": json.dumps(
                        {"step_name": "search", "result": "Found 3 competitors."},
                    ),
                },
            ],
            tools,
        )

        # Step 3: Get plan to verify state
        r3 = await execute_tools(
            [
                {
                    "id": "call_3",
                    "name": "get_plan",
                    "arguments": "{}",
                },
            ],
            tools,
        )

        # Step 4: Complete second step
        r4 = await execute_tools(
            [
                {
                    "id": "call_4",
                    "name": "mark_step_done",
                    "arguments": json.dumps(
                        {"step_name": "analyze", "result": "Market growing 15%."},
                    ),
                },
            ],
            tools,
        )

        return {"create": r1, "complete1": r2, "get": r3, "complete2": r4, "summary": summary_fn()}

    ctx = wf.run()
    assert ctx.has_succeeded

    out = ctx.output

    # Verify create_plan result
    create_result = json.loads(out["create"][0]["output"])
    assert len(create_result["steps"]) == 2

    # Verify first mark_step_done
    complete1 = json.loads(out["complete1"][0]["output"])
    assert complete1["status"] == "completed"
    assert complete1["result"] == "Found 3 competitors."

    # Verify get_plan shows progress
    plan = json.loads(out["get"][0]["output"])
    search_step = next(s for s in plan["steps"] if s["name"] == "search")
    assert search_step["status"] == "completed"
    analyze_step = next(s for s in plan["steps"] if s["name"] == "analyze")
    assert analyze_step["status"] == "pending"

    # Verify second mark_step_done
    complete2 = json.loads(out["complete2"][0]["output"])
    assert complete2["status"] == "completed"

    # Verify summary
    assert "2/2" in out["summary"]


def test_integration_replan_preserves_completed():
    """Replanning preserves completed steps and their results."""

    @workflow
    async def wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()

        # Create initial plan
        await execute_tools(
            [
                {
                    "id": "c1",
                    "name": "create_plan",
                    "arguments": json.dumps(
                        {
                            "steps": json.dumps(
                                [
                                    {"name": "a", "description": "Do A."},
                                    {"name": "b", "description": "Do B."},
                                ],
                            ),
                        },
                    ),
                },
            ],
            tools,
        )

        # Complete step a
        await execute_tools(
            [
                {
                    "id": "c2",
                    "name": "mark_step_done",
                    "arguments": json.dumps({"step_name": "a", "result": "A done."}),
                },
            ],
            tools,
        )

        # Replan with new step c, keeping a
        result = await execute_tools(
            [
                {
                    "id": "c3",
                    "name": "create_plan",
                    "arguments": json.dumps(
                        {
                            "steps": json.dumps(
                                [
                                    {"name": "a", "description": "Do A differently."},
                                    {"name": "c", "description": "Do C.", "depends_on": ["a"]},
                                ],
                            ),
                        },
                    ),
                },
            ],
            tools,
        )

        return json.loads(result[0]["output"])

    ctx = wf.run()
    assert ctx.has_succeeded
    plan = ctx.output
    step_a = next(s for s in plan["steps"] if s["name"] == "a")
    assert step_a["status"] == "completed"
    assert step_a["result"] == "A done."
    step_c = next(s for s in plan["steps"] if s["name"] == "c")
    assert step_c["status"] == "pending"


def test_integration_mark_step_done_errors():
    """mark_step_done returns error dicts for invalid calls."""

    @workflow
    async def wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()

        # No plan exists
        r1 = await execute_tools(
            [
                {
                    "id": "c1",
                    "name": "mark_step_done",
                    "arguments": json.dumps({"step_name": "x", "result": "Done."}),
                },
            ],
            tools,
        )

        # Create plan
        await execute_tools(
            [
                {
                    "id": "c2",
                    "name": "create_plan",
                    "arguments": json.dumps(
                        {
                            "steps": json.dumps(
                                [{"name": "a", "description": "Do A."}],
                            ),
                        },
                    ),
                },
            ],
            tools,
        )

        # Step not found
        r2 = await execute_tools(
            [
                {
                    "id": "c3",
                    "name": "mark_step_done",
                    "arguments": json.dumps({"step_name": "nonexistent", "result": "Done."}),
                },
            ],
            tools,
        )

        return {"no_plan": r1, "not_found": r2}

    ctx = wf.run()
    assert ctx.has_succeeded
    assert "error" in ctx.output["no_plan"][0]["output"]
    assert "error" in ctx.output["not_found"][0]["output"]


def test_integration_validation_errors():
    """create_plan returns error for invalid step names and circular deps."""

    @workflow
    async def wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()

        r1 = await execute_tools(
            [
                {
                    "id": "c1",
                    "name": "create_plan",
                    "arguments": json.dumps(
                        {
                            "steps": json.dumps(
                                [{"name": "Bad-Name", "description": "X."}],
                            ),
                        },
                    ),
                },
            ],
            tools,
        )

        r2 = await execute_tools(
            [
                {
                    "id": "c2",
                    "name": "create_plan",
                    "arguments": json.dumps(
                        {
                            "steps": json.dumps(
                                [
                                    {"name": "a", "description": "A.", "depends_on": ["b"]},
                                    {"name": "b", "description": "B.", "depends_on": ["a"]},
                                ],
                            ),
                        },
                    ),
                },
            ],
            tools,
        )

        return {"invalid_name": r1, "circular": r2}

    ctx = wf.run()
    assert ctx.has_succeeded
    assert "Error" in ctx.output["invalid_name"][0]["output"]
    assert "Error" in ctx.output["circular"][0]["output"]


def test_integration_plan_summary_injection():
    """Plan summary function returns correct status after operations."""

    @workflow
    async def wf(ctx: ExecutionContext):
        tools, summary_fn = await build_plan_tools()

        # No plan — summary should be None
        assert summary_fn() is None

        # Create plan
        await execute_tools(
            [
                {
                    "id": "c1",
                    "name": "create_plan",
                    "arguments": json.dumps(
                        {
                            "steps": json.dumps(
                                [
                                    {"name": "a", "description": "A."},
                                    {"name": "b", "description": "B.", "depends_on": ["a"]},
                                ],
                            ),
                        },
                    ),
                },
            ],
            tools,
        )

        s1 = summary_fn()

        # Complete step a
        await execute_tools(
            [
                {
                    "id": "c2",
                    "name": "mark_step_done",
                    "arguments": json.dumps({"step_name": "a", "result": "Done A."}),
                },
            ],
            tools,
        )

        s2 = summary_fn()

        # Complete step b
        await execute_tools(
            [
                {
                    "id": "c3",
                    "name": "mark_step_done",
                    "arguments": json.dumps({"step_name": "b", "result": "Done B."}),
                },
            ],
            tools,
        )

        s3 = summary_fn()

        return {"s1": s1, "s2": s2, "s3": s3}

    ctx = wf.run()
    assert ctx.has_succeeded
    out = ctx.output
    assert "0/2" in out["s1"]
    assert '"a"' in out["s1"]
    assert "1/2" in out["s2"]
    assert '"b"' in out["s2"]
    # b depends on a — summary should include a's result
    assert "Done A." in out["s2"]
    assert "2/2" in out["s3"]
    assert "No steps ready" in out["s3"]


# ========================================================================
# LLM-driven tests: Verify real model interaction
# These are non-deterministic — test basic connectivity
# ========================================================================


# Shared tools for LLM tests
@task
async def search_web(query: str) -> str:
    """Search the web and return results for a query."""
    return f"Results for '{query}': Company A leads with 35% market share."


def test_e2e_agent_with_planning_succeeds():
    """An agent with planning=True can complete a task without errors."""

    @workflow
    async def wf(ctx: ExecutionContext):
        planner = await agent(
            "You are a research analyst. Use your tools to help with research tasks.",
            model=MODEL,
            tools=[search_web],
            planning=True,
            max_tool_calls=15,
            stream=False,
        )
        return await planner("Search for information about AI frameworks.")

    ctx = wf.run(timeout=TIMEOUT)
    assert ctx.has_succeeded, f"Workflow failed: {ctx.output}"
    assert ctx.output is not None


def test_e2e_agent_without_planning_succeeds():
    """An agent with planning=False still works normally."""

    @workflow
    async def wf(ctx: ExecutionContext):
        basic = await agent(
            "You are a research analyst.",
            model=MODEL,
            tools=[search_web],
            planning=False,
            max_tool_calls=10,
            stream=False,
        )
        return await basic("Search for AI framework data.")

    ctx = wf.run(timeout=TIMEOUT)
    assert ctx.has_succeeded, f"Workflow failed: {ctx.output}"


def test_e2e_planning_and_non_planning_both_succeed():
    """Both planning and non-planning agents should complete without crashing."""

    @workflow
    async def wf(ctx: ExecutionContext):
        planner = await agent(
            "You are a research analyst.",
            model=MODEL,
            tools=[search_web],
            planning=True,
            max_tool_calls=15,
            stream=False,
        )
        basic = await agent(
            "You are a research analyst.",
            model=MODEL,
            tools=[search_web],
            planning=False,
            max_tool_calls=10,
            stream=False,
        )
        r1 = await planner("What companies are in the AI market?")
        r2 = await basic("What companies are in the AI market?")
        return {"planning": r1, "basic": r2}

    ctx = wf.run(timeout=TIMEOUT)
    assert ctx.has_succeeded, f"Workflow failed: {ctx.output}"
    assert ctx.output["planning"] is not None
    assert ctx.output["basic"] is not None
