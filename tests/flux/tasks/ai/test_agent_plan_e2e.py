"""
Integration tests for Agent Plans.

Verify that the plan tools work correctly within the agent execution
pipeline (deterministic, no LLM required).

Run: poetry run python -m pytest tests/flux/tasks/ai/test_agent_plan_e2e.py -v
"""

from __future__ import annotations

import json

from flux import ExecutionContext, workflow
from flux.tasks.ai.agent_plan import build_plan_tools
from flux.tasks.ai.tool_executor import execute_tools


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
    """Full lifecycle: create -> start -> complete -> get_ready -> start -> complete."""

    @workflow
    async def wf(ctx: ExecutionContext):
        tools, summary_fn = await build_plan_tools()

        r1 = await execute_tools(
            [
                {
                    "id": "c1",
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

        r2 = await execute_tools(
            [{"id": "c2", "name": "start_step", "arguments": json.dumps({"step_name": "search"})}],
            tools,
        )

        r3 = await execute_tools(
            [
                {
                    "id": "c3",
                    "name": "mark_step_done",
                    "arguments": json.dumps(
                        {"step_name": "search", "result": "Found 3 competitors."},
                    ),
                },
            ],
            tools,
        )

        r4 = await execute_tools(
            [{"id": "c4", "name": "get_ready_steps", "arguments": "{}"}],
            tools,
        )

        r5 = await execute_tools(
            [{"id": "c5", "name": "start_step", "arguments": json.dumps({"step_name": "analyze"})}],
            tools,
        )

        r6 = await execute_tools(
            [
                {
                    "id": "c6",
                    "name": "mark_step_done",
                    "arguments": json.dumps(
                        {"step_name": "analyze", "result": "Market growing 15%."},
                    ),
                },
            ],
            tools,
        )

        return {
            "create": r1,
            "start1": r2,
            "complete1": r3,
            "ready": r4,
            "start2": r5,
            "complete2": r6,
            "summary": summary_fn(),
        }

    ctx = wf.run()
    assert ctx.has_succeeded
    out = ctx.output

    start1 = json.loads(out["start1"][0]["output"])
    assert start1["status"] == "in_progress"

    ready = json.loads(out["ready"][0]["output"])
    assert len(ready["ready_steps"]) == 1
    assert ready["ready_steps"][0]["name"] == "analyze"
    assert ready["ready_steps"][0]["dependency_results"]["search"] == "Found 3 competitors."

    assert "2/2 done" in out["summary"]


def test_integration_mark_step_failed():
    """mark_step_failed works through execute_tools."""

    @workflow
    async def wf(ctx: ExecutionContext):
        tools, summary_fn = await build_plan_tools()

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
                                    {"name": "b", "description": "Do B.", "depends_on": ["a"]},
                                ],
                            ),
                        },
                    ),
                },
            ],
            tools,
        )

        await execute_tools(
            [{"id": "c2", "name": "start_step", "arguments": json.dumps({"step_name": "a"})}],
            tools,
        )

        r = await execute_tools(
            [
                {
                    "id": "c3",
                    "name": "mark_step_failed",
                    "arguments": json.dumps(
                        {"step_name": "a", "reason": "Connection timeout."},
                    ),
                },
            ],
            tools,
        )

        ready = await execute_tools(
            [{"id": "c4", "name": "get_ready_steps", "arguments": "{}"}],
            tools,
        )

        return {
            "failed": r,
            "ready": ready,
            "summary": summary_fn(),
        }

    ctx = wf.run()
    assert ctx.has_succeeded
    out = ctx.output

    failed = json.loads(out["failed"][0]["output"])
    assert failed["status"] == "failed"
    assert failed["error"] == "Connection timeout."

    ready = json.loads(out["ready"][0]["output"])
    assert ready["ready_steps"] == []

    assert "1 failed" in out["summary"]


def test_integration_get_ready_steps():
    """get_ready_steps returns only steps whose dependencies are satisfied."""

    @workflow
    async def wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()

        await execute_tools(
            [
                {
                    "id": "c1",
                    "name": "create_plan",
                    "arguments": json.dumps(
                        {
                            "steps": json.dumps(
                                [
                                    {"name": "fetch", "description": "Fetch data."},
                                    {
                                        "name": "process",
                                        "description": "Process it.",
                                        "depends_on": ["fetch"],
                                    },
                                    {
                                        "name": "report",
                                        "description": "Write report.",
                                        "depends_on": ["process"],
                                    },
                                ],
                            ),
                        },
                    ),
                },
            ],
            tools,
            iteration=0,
        )

        r1 = await execute_tools(
            [{"id": "c2", "name": "get_ready_steps", "arguments": "{}"}],
            tools,
            iteration=1,
        )

        await execute_tools(
            [{"id": "c3", "name": "start_step", "arguments": json.dumps({"step_name": "fetch"})}],
            tools,
            iteration=2,
        )
        await execute_tools(
            [
                {
                    "id": "c4",
                    "name": "mark_step_done",
                    "arguments": json.dumps(
                        {"step_name": "fetch", "result": "Raw data fetched."},
                    ),
                },
            ],
            tools,
            iteration=3,
        )

        r2 = await execute_tools(
            [{"id": "c5", "name": "get_ready_steps", "arguments": "{}"}],
            tools,
            iteration=4,
        )

        return {"before": r1, "after": r2}

    ctx = wf.run()
    assert ctx.has_succeeded
    out = ctx.output

    before = json.loads(out["before"][0]["output"])
    assert len(before["ready_steps"]) == 1
    assert before["ready_steps"][0]["name"] == "fetch"

    after = json.loads(out["after"][0]["output"])
    assert len(after["ready_steps"]) == 1
    assert after["ready_steps"][0]["name"] == "process"
    assert after["ready_steps"][0]["dependency_results"]["fetch"] == "Raw data fetched."


def test_integration_replan_preserves_completed():
    """Replanning preserves completed steps and their results."""

    @workflow
    async def wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()

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

        await execute_tools(
            [
                {
                    "id": "c2",
                    "name": "create_plan",
                    "arguments": json.dumps(
                        {
                            "steps": json.dumps(
                                [
                                    {"name": "a", "description": "Do A."},
                                    {"name": "b", "description": "Do B.", "depends_on": ["a"]},
                                ],
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
                                [
                                    {"name": "Bad-Name", "description": "X."},
                                    {"name": "valid", "description": "Y."},
                                ],
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

        assert summary_fn() is None

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
    assert "Done A." in out["s2"]
    assert "2/2" in out["s3"]
    assert "No steps ready" in out["s3"]
