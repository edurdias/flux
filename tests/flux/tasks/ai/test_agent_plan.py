from __future__ import annotations

import pytest

from flux.tasks.ai.agent_plan import (
    AgentPlan,
    AgentStep,
    PlanContext,
    PlanValidationError,
    build_plan_preamble,
    build_plan_tools,
    _validate_dependencies,
    _validate_step_name,
)


# --- AgentStep tests ---


def test_step_construction():
    step = AgentStep(name="research", description="Research the topic.")
    assert step.name == "research"
    assert step.description == "Research the topic."
    assert step.depends_on == []
    assert step.status == "pending"
    assert step.result is None


def test_step_with_dependencies():
    step = AgentStep(
        name="analyze",
        description="Analyze data.",
        depends_on=["research", "collect"],
    )
    assert step.depends_on == ["research", "collect"]


def test_step_to_dict_minimal():
    step = AgentStep(name="research", description="Research the topic.")
    d = step.to_dict()
    assert d == {"name": "research", "description": "Research the topic.", "status": "pending"}
    assert "depends_on" not in d
    assert "result" not in d


def test_step_to_dict_full():
    step = AgentStep(
        name="analyze",
        description="Analyze data.",
        depends_on=["research"],
        status="completed",
        result="Found 3 competitors.",
    )
    d = step.to_dict()
    assert d["depends_on"] == ["research"]
    assert d["result"] == "Found 3 competitors."
    assert d["status"] == "completed"


def test_step_in_progress_status():
    step = AgentStep(name="research", description="Research.", status="in_progress")
    assert step.status == "in_progress"
    d = step.to_dict()
    assert d["status"] == "in_progress"


def test_step_failed_status():
    step = AgentStep(
        name="research",
        description="Research.",
        status="failed",
        error="Connection timeout.",
    )
    assert step.status == "failed"
    assert step.error == "Connection timeout."
    d = step.to_dict()
    assert d["status"] == "failed"
    assert d["error"] == "Connection timeout."


def test_step_to_dict_omits_none_error():
    step = AgentStep(name="research", description="Research.")
    d = step.to_dict()
    assert "error" not in d


# --- AgentPlan tests ---


def test_plan_construction():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="Do A."),
            AgentStep(name="b", description="Do B.", depends_on=["a"]),
        ],
    )
    assert len(plan.steps) == 2


def test_plan_get_step():
    step_a = AgentStep(name="a", description="Do A.")
    plan = AgentPlan(steps=[step_a])
    assert plan.get_step("a") is step_a
    assert plan.get_step("nonexistent") is None


def test_plan_completed_steps():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="Do A.", status="completed", result="Done."),
            AgentStep(name="b", description="Do B."),
        ],
    )
    completed = plan.completed_steps()
    assert len(completed) == 1
    assert completed[0].name == "a"


def test_plan_pending_steps():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="Do A.", status="completed", result="Done."),
            AgentStep(name="b", description="Do B."),
            AgentStep(name="c", description="Do C."),
        ],
    )
    pending = plan.pending_steps()
    assert len(pending) == 2


def test_plan_in_progress_steps():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="A.", status="in_progress"),
            AgentStep(name="b", description="B."),
            AgentStep(name="c", description="C.", status="completed", result="Done."),
        ],
    )
    in_progress = plan.in_progress_steps()
    assert len(in_progress) == 1
    assert in_progress[0].name == "a"


def test_plan_failed_steps():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="A.", status="failed", error="Timeout."),
            AgentStep(name="b", description="B."),
        ],
    )
    failed = plan.failed_steps()
    assert len(failed) == 1
    assert failed[0].name == "a"


def test_plan_ready_steps():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="A.", status="completed", result="Done."),
            AgentStep(name="b", description="B.", depends_on=["a"]),
            AgentStep(name="c", description="C.", depends_on=["a", "b"]),
            AgentStep(name="d", description="D."),
        ],
    )
    ready = plan.ready_steps()
    assert len(ready) == 2
    names = [s.name for s in ready]
    assert "b" in names
    assert "d" in names
    assert "c" not in names


def test_plan_ready_steps_excludes_in_progress():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="A.", status="in_progress"),
            AgentStep(name="b", description="B."),
        ],
    )
    ready = plan.ready_steps()
    assert len(ready) == 1
    assert ready[0].name == "b"


def test_plan_failed_steps_block_dependents():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="A.", status="failed", error="Broke."),
            AgentStep(name="b", description="B.", depends_on=["a"]),
        ],
    )
    assert plan.dependencies_satisfied(plan.get_step("b")) is False
    ready = plan.ready_steps()
    assert len(ready) == 0


def test_plan_active_step():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="A.", status="in_progress"),
            AgentStep(name="b", description="B."),
        ],
    )
    assert plan.active_step().name == "a"


def test_plan_active_step_none():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="A."),
            AgentStep(name="b", description="B."),
        ],
    )
    assert plan.active_step() is None


def test_plan_dependencies_satisfied():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="Do A.", status="completed", result="Done."),
            AgentStep(name="b", description="Do B.", depends_on=["a"]),
            AgentStep(name="c", description="Do C.", depends_on=["a", "b"]),
        ],
    )
    assert plan.dependencies_satisfied(plan.get_step("b")) is True
    assert plan.dependencies_satisfied(plan.get_step("c")) is False


def test_plan_dependencies_satisfied_no_deps():
    plan = AgentPlan(steps=[AgentStep(name="a", description="Do A.")])
    assert plan.dependencies_satisfied(plan.get_step("a")) is True


def test_plan_dependency_results():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="Do A.", status="completed", result="Result A."),
            AgentStep(name="b", description="Do B.", status="completed", result="Result B."),
            AgentStep(name="c", description="Do C.", depends_on=["a", "b"]),
        ],
    )
    results = plan.dependency_results(plan.get_step("c"))
    assert results == {"a": "Result A.", "b": "Result B."}


def test_plan_dependency_results_partial():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="Do A.", status="completed", result="Result A."),
            AgentStep(name="b", description="Do B."),
            AgentStep(name="c", description="Do C.", depends_on=["a", "b"]),
        ],
    )
    results = plan.dependency_results(plan.get_step("c"))
    assert results == {"a": "Result A."}


def test_plan_summary_all_complete():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="A.", status="completed", result="Done."),
            AgentStep(
                name="b",
                description="B.",
                depends_on=["a"],
                status="completed",
                result="Done.",
            ),
        ],
    )
    summary = plan.summary()
    assert "2/2 done" in summary
    assert "No steps ready" in summary


def test_plan_summary_with_ready_and_dep_results():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="A.", status="completed", result="Done."),
            AgentStep(name="b", description="B.", depends_on=["a"]),
            AgentStep(name="c", description="C."),
        ],
    )
    summary = plan.summary()
    assert "1/3 done" in summary
    assert '"b"' in summary
    assert '"c"' in summary
    assert "Done." in summary


def test_plan_summary_no_deps_no_results():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="A."),
        ],
    )
    summary = plan.summary()
    assert '"a"' in summary
    assert "from" not in summary


def test_plan_summary_limits_to_3():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="A."),
            AgentStep(name="b", description="B."),
            AgentStep(name="c", description="C."),
            AgentStep(name="d", description="D."),
            AgentStep(name="e", description="E."),
        ],
    )
    summary = plan.summary()
    assert summary.count('"') <= 6


def test_plan_summary_shows_active_step():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="A.", status="in_progress"),
            AgentStep(name="b", description="B."),
        ],
    )
    summary = plan.summary()
    assert "0/2 done" in summary
    assert 'Active: "a"' in summary


def test_plan_summary_shows_failed_steps():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="A.", status="failed", error="Timeout."),
            AgentStep(name="b", description="B."),
        ],
    )
    summary = plan.summary()
    assert "1 failed" in summary
    assert '"a"' in summary


def test_plan_summary_full_status():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="A.", status="completed", result="Done."),
            AgentStep(name="b", description="B.", status="in_progress"),
            AgentStep(name="c", description="C.", status="failed", error="Broke."),
            AgentStep(name="d", description="D."),
            AgentStep(name="e", description="E.", depends_on=["a"]),
        ],
    )
    summary = plan.summary()
    assert "1/5 done" in summary
    assert "1 failed" in summary
    assert 'Active: "b"' in summary
    assert "Ready:" in summary
    assert '"d"' in summary
    assert '"e"' in summary


def test_plan_to_dict():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="Do A."),
            AgentStep(name="b", description="Do B.", depends_on=["a"]),
        ],
    )
    d = plan.to_dict()
    assert len(d["steps"]) == 2
    assert d["steps"][0]["name"] == "a"
    assert d["steps"][1]["depends_on"] == ["a"]


# --- Validation tests ---


def test_validate_step_name_valid():
    _validate_step_name("research")
    _validate_step_name("step-1")
    _validate_step_name("a")
    _validate_step_name("research-data-v2")
    _validate_step_name("research_data")
    _validate_step_name("step_1")


def test_validate_step_name_empty():
    with pytest.raises(PlanValidationError, match="empty"):
        _validate_step_name("")


def test_validate_step_name_too_long():
    with pytest.raises(PlanValidationError, match="64"):
        _validate_step_name("a" * 65)


def test_validate_step_name_consecutive_hyphens():
    with pytest.raises(PlanValidationError, match="consecutive"):
        _validate_step_name("my--step")


def test_validate_step_name_consecutive_underscores():
    with pytest.raises(PlanValidationError, match="consecutive"):
        _validate_step_name("my__step")


def test_validate_step_name_uppercase():
    with pytest.raises(PlanValidationError, match="invalid"):
        _validate_step_name("MyStep")


def test_validate_step_name_leading_hyphen():
    with pytest.raises(PlanValidationError, match="invalid"):
        _validate_step_name("-step")


def test_validate_step_name_trailing_hyphen():
    with pytest.raises(PlanValidationError, match="invalid"):
        _validate_step_name("step-")


def test_validate_dependencies_valid():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="A."),
            AgentStep(name="b", description="B.", depends_on=["a"]),
        ],
    )
    _validate_dependencies(plan)


def test_validate_dependencies_missing_reference():
    plan = AgentPlan(
        steps=[AgentStep(name="a", description="A.", depends_on=["nonexistent"])],
    )
    with pytest.raises(PlanValidationError, match="nonexistent"):
        _validate_dependencies(plan)


def test_validate_dependencies_circular():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="A.", depends_on=["b"]),
            AgentStep(name="b", description="B.", depends_on=["a"]),
        ],
    )
    with pytest.raises(PlanValidationError, match="Circular"):
        _validate_dependencies(plan)


def test_validate_dependencies_self_reference():
    plan = AgentPlan(
        steps=[AgentStep(name="a", description="A.", depends_on=["a"])],
    )
    with pytest.raises(PlanValidationError, match="Circular"):
        _validate_dependencies(plan)


def test_validate_dependencies_duplicate_names():
    plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="A."),
            AgentStep(name="a", description="A again."),
        ],
    )
    with pytest.raises(PlanValidationError, match="Duplicate"):
        _validate_dependencies(plan)


# --- PlanContext tests ---


def test_plan_context_initial_state():
    ctx = PlanContext()
    assert ctx.plan is None
    assert ctx.summary() is None


def test_plan_context_summary_with_plan():
    ctx = PlanContext()
    ctx.plan = AgentPlan(
        steps=[
            AgentStep(name="a", description="A."),
            AgentStep(name="b", description="B."),
        ],
    )
    summary = ctx.summary()
    assert summary is not None
    assert "0/2" in summary


# --- build_plan_tools tests ---


def test_build_plan_tools_returns_tools_and_summary():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, summary_fn = await build_plan_tools()
        return {"count": len(tools), "summary": summary_fn()}

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output["count"] == 6
    assert ctx.output["summary"] is None


def test_build_plan_tools_tool_names():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        return {t.func.__name__ for t in tools}

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output == {
        "create_plan",
        "start_step",
        "mark_step_done",
        "mark_step_failed",
        "get_plan",
        "get_ready_steps",
    }


def test_create_plan():
    import json

    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, summary_fn = await build_plan_tools()
        create_plan_tool = tools[0]
        result = await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "research", "description": "Research the topic."},
                    {"name": "analyze", "description": "Analyze data.", "depends_on": ["research"]},
                ],
            ),
        )
        return {"result": result, "summary": summary_fn()}

    ctx = test_wf.run()
    assert ctx.has_succeeded
    result = ctx.output["result"]
    assert len(result["steps"]) == 2
    assert result["steps"][0]["name"] == "research"
    assert result["steps"][1]["name"] == "analyze"
    assert result["steps"][1]["depends_on"] == ["research"]
    assert ctx.output["summary"] is not None
    assert "0/2" in ctx.output["summary"]


def test_create_plan_invalid_name():
    import json

    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        create_plan_tool = tools[0]
        return await create_plan_tool(
            steps=json.dumps([{"name": "Bad-Name", "description": "Invalid."}]),
        )

    ctx = test_wf.run()
    assert ctx.has_failed


def test_create_plan_circular_dependency():
    import json

    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        create_plan_tool = tools[0]
        return await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "A.", "depends_on": ["b"]},
                    {"name": "b", "description": "B.", "depends_on": ["a"]},
                ],
            ),
        )

    ctx = test_wf.run()
    assert ctx.has_failed


def test_create_plan_preserves_completed_on_replan():
    import json

    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        create_plan_tool = tools[0]
        mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")
        await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B."},
                ],
            ),
        )
        await mark_step_done_tool(step_name="a", result="Done A.")
        result = await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A differently."},
                    {"name": "b", "description": "Do B revised."},
                    {"name": "c", "description": "Do C.", "depends_on": ["a"]},
                ],
            ),
        )
        return result

    ctx = test_wf.run()
    assert ctx.has_succeeded
    result = ctx.output
    step_a = next(s for s in result["steps"] if s["name"] == "a")
    assert step_a["status"] == "completed"
    assert step_a["result"] == "Done A."
    step_b = next(s for s in result["steps"] if s["name"] == "b")
    assert step_b["status"] == "pending"


# --- complete_step tool tests ---


def test_mark_step_done():
    import json

    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, summary_fn = await build_plan_tools()
        create_plan_tool = tools[0]
        mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")
        await create_plan_tool(
            steps=json.dumps(
                [{"name": "a", "description": "Do A."}, {"name": "b", "description": "Do B."}],
            ),
        )
        result = await mark_step_done_tool(step_name="a", result="Done A.")
        return {"result": result, "summary": summary_fn()}

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output["result"]["status"] == "completed"
    assert ctx.output["result"]["result"] == "Done A."
    assert "1/2" in ctx.output["summary"]


def test_mark_step_done_no_plan():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")
        return await mark_step_done_tool(step_name="a", result="Done.")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "error" in ctx.output


def test_mark_step_done_not_found():
    import json

    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        create_plan_tool = tools[0]
        mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")
        await create_plan_tool(
            steps=json.dumps(
                [{"name": "a", "description": "Do A."}, {"name": "b", "description": "Do B."}],
            ),
        )
        return await mark_step_done_tool(step_name="nonexistent", result="Done.")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "error" in ctx.output
    assert "nonexistent" in ctx.output["error"]


def test_mark_step_done_already_completed():
    import json

    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        create_plan_tool = tools[0]
        mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")
        await create_plan_tool(
            steps=json.dumps(
                [{"name": "a", "description": "Do A."}, {"name": "b", "description": "Do B."}],
            ),
        )
        await mark_step_done_tool(step_name="a", result="First result.")
        return await mark_step_done_tool(step_name="a", result="Second result.")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output["result"] == "First result."


# --- get_plan tool tests ---


def test_get_plan():
    import json

    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        create_plan_tool = tools[0]
        get_plan_tool = next(t for t in tools if t.func.__name__ == "get_plan")
        await create_plan_tool(
            steps=json.dumps(
                [{"name": "a", "description": "Do A."}, {"name": "b", "description": "Do B."}],
            ),
        )
        return await get_plan_tool()

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert len(ctx.output["steps"]) == 2


def test_get_plan_no_plan():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        get_plan_tool = next(t for t in tools if t.func.__name__ == "get_plan")
        return await get_plan_tool()

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "message" in ctx.output


# --- start_step tool tests ---


def test_start_step():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, summary_fn = await build_plan_tools()
        create_plan_tool = tools[0]
        start_step_tool = next(t for t in tools if t.func.__name__ == "start_step")
        await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B.", "depends_on": ["a"]},
                ],
            ),
        )
        result = await start_step_tool(step_name="a")
        return {"result": result, "summary": summary_fn()}

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output["result"]["status"] == "in_progress"
    assert 'Active: "a"' in ctx.output["summary"]


def test_start_step_no_plan():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        start_step_tool = next(t for t in tools if t.func.__name__ == "start_step")
        return await start_step_tool(step_name="a")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "error" in ctx.output


def test_start_step_not_found():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        create_plan_tool = tools[0]
        start_step_tool = next(t for t in tools if t.func.__name__ == "start_step")
        await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B."},
                ],
            ),
        )
        return await start_step_tool(step_name="nonexistent")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "error" in ctx.output


def test_start_step_already_in_progress():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        create_plan_tool = tools[0]
        start_step_tool = next(t for t in tools if t.func.__name__ == "start_step")
        await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B."},
                ],
            ),
        )
        await start_step_tool(step_name="a")
        return await start_step_tool(step_name="b")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "error" in ctx.output
    assert '"a"' in ctx.output["error"]


def test_start_step_deps_not_satisfied_warns():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        create_plan_tool = tools[0]
        start_step_tool = next(t for t in tools if t.func.__name__ == "start_step")
        await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B.", "depends_on": ["a"]},
                ],
            ),
        )
        return await start_step_tool(step_name="b")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "warning" in ctx.output
    assert ctx.output["status"] == "in_progress"


def test_start_step_strict_deps_blocks():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools(strict_dependencies=True)
        create_plan_tool = tools[0]
        start_step_tool = next(t for t in tools if t.func.__name__ == "start_step")
        await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B.", "depends_on": ["a"]},
                ],
            ),
        )
        return await start_step_tool(step_name="b")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "error" in ctx.output
    assert "a" in ctx.output["error"]


# --- Preamble tests ---


def test_build_plan_preamble_contains_tool_names():
    preamble = build_plan_preamble()
    assert "create_plan" in preamble
    assert "mark_step_done" in preamble
    assert "get_plan" in preamble


def test_build_plan_preamble_contains_guidance():
    preamble = build_plan_preamble()
    assert "When to create a plan" in preamble
    assert "When NOT to create a plan" in preamble
    assert "depends_on" in preamble
    assert "Replanning" in preamble


# --- mark_step_failed tool tests ---


def test_mark_step_failed():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, summary_fn = await build_plan_tools()
        create_plan_tool = tools[0]
        start_step_tool = next(t for t in tools if t.func.__name__ == "start_step")
        mark_step_failed_tool = next(t for t in tools if t.func.__name__ == "mark_step_failed")
        await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B."},
                ],
            ),
        )
        await start_step_tool(step_name="a")
        result = await mark_step_failed_tool(step_name="a", reason="Connection timeout.")
        return {"result": result, "summary": summary_fn()}

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output["result"]["status"] == "failed"
    assert ctx.output["result"]["error"] == "Connection timeout."
    assert "1 failed" in ctx.output["summary"]


def test_mark_step_failed_no_plan():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        mark_step_failed_tool = next(t for t in tools if t.func.__name__ == "mark_step_failed")
        return await mark_step_failed_tool(step_name="a", reason="Error.")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "error" in ctx.output


def test_mark_step_failed_allows_from_pending():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        create_plan_tool = tools[0]
        mark_step_failed_tool = next(t for t in tools if t.func.__name__ == "mark_step_failed")
        await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B."},
                ],
            ),
        )
        return await mark_step_failed_tool(step_name="a", reason="Skip.")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output["status"] == "failed"


def test_mark_step_failed_already_completed():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        create_plan_tool = tools[0]
        mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")
        mark_step_failed_tool = next(t for t in tools if t.func.__name__ == "mark_step_failed")
        await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B."},
                ],
            ),
        )
        await mark_step_done_tool(step_name="a", result="Done.")
        return await mark_step_failed_tool(step_name="a", reason="Too late.")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "error" in ctx.output


def test_mark_step_done_from_in_progress():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        create_plan_tool = tools[0]
        start_step_tool = next(t for t in tools if t.func.__name__ == "start_step")
        mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")
        await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B."},
                ],
            ),
        )
        await start_step_tool(step_name="a")
        return await mark_step_done_tool(step_name="a", result="Done.")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output["status"] == "completed"


def test_mark_step_done_from_pending_grace():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        create_plan_tool = tools[0]
        mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")
        await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B."},
                ],
            ),
        )
        return await mark_step_done_tool(step_name="a", result="Done directly.")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output["status"] == "completed"


def test_mark_step_done_rejects_failed():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        create_plan_tool = tools[0]
        mark_step_failed_tool = next(t for t in tools if t.func.__name__ == "mark_step_failed")
        mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")
        await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B."},
                ],
            ),
        )
        await mark_step_failed_tool(step_name="a", reason="Broke.")
        return await mark_step_done_tool(step_name="a", result="Actually done.")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "error" in ctx.output


# --- Step count validation tests ---


def test_create_plan_rejects_single_step():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        create_plan_tool = tools[0]
        return await create_plan_tool(
            steps=json.dumps([{"name": "a", "description": "Do A."}]),
        )

    ctx = test_wf.run()
    assert ctx.has_failed


def test_create_plan_rejects_too_many_steps():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools(max_plan_steps=5)
        create_plan_tool = tools[0]
        steps = [{"name": f"s{i}", "description": f"Step {i}."} for i in range(6)]
        return await create_plan_tool(steps=json.dumps(steps))

    ctx = test_wf.run()
    assert ctx.has_failed


def test_create_plan_accepts_at_limit():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools(max_plan_steps=5)
        create_plan_tool = tools[0]
        steps = [{"name": f"s{i}", "description": f"Step {i}."} for i in range(5)]
        return await create_plan_tool(steps=json.dumps(steps))

    ctx = test_wf.run()
    assert ctx.has_succeeded


def test_create_plan_default_max_is_20():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        create_plan_tool = tools[0]
        steps = [{"name": f"s{i}", "description": f"Step {i}."} for i in range(21)]
        return await create_plan_tool(steps=json.dumps(steps))

    ctx = test_wf.run()
    assert ctx.has_failed


# --- get_ready_steps tool tests ---


def test_get_ready_steps():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        create_plan_tool = tools[0]
        mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")
        get_ready_steps_tool = next(t for t in tools if t.func.__name__ == "get_ready_steps")
        await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B.", "depends_on": ["a"]},
                    {"name": "c", "description": "Do C."},
                ],
            ),
        )
        await mark_step_done_tool(step_name="a", result="Done A.")
        return await get_ready_steps_tool()

    ctx = test_wf.run()
    assert ctx.has_succeeded
    steps = ctx.output["ready_steps"]
    names = [s["name"] for s in steps]
    assert "b" in names
    assert "c" in names
    b_step = next(s for s in steps if s["name"] == "b")
    assert b_step["dependency_results"] == {"a": "Done A."}


def test_get_ready_steps_no_plan():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        get_ready_steps_tool = next(t for t in tools if t.func.__name__ == "get_ready_steps")
        return await get_ready_steps_tool()

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "message" in ctx.output


def test_get_ready_steps_none_ready():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        create_plan_tool = tools[0]
        start_step_tool = next(t for t in tools if t.func.__name__ == "start_step")
        get_ready_steps_tool = next(t for t in tools if t.func.__name__ == "get_ready_steps")
        await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B.", "depends_on": ["a"]},
                ],
            ),
        )
        await start_step_tool(step_name="a")
        return await get_ready_steps_tool()

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output["ready_steps"] == []


def test_replan_warns_about_dropped_completed_steps():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        create_plan_tool = tools[0]
        mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")
        await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B."},
                ],
            ),
        )
        await mark_step_done_tool(step_name="a", result="Done A.")
        return await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "c", "description": "Do C."},
                    {"name": "d", "description": "Do D."},
                ],
            ),
        )

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "warning" in ctx.output
    assert "a" in ctx.output["warning"]


def test_replan_no_warning_when_steps_preserved():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools()
        create_plan_tool = tools[0]
        mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")
        await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B."},
                ],
            ),
        )
        await mark_step_done_tool(step_name="a", result="Done A.")
        return await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A differently."},
                    {"name": "c", "description": "Do C."},
                ],
            ),
        )

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "warning" not in ctx.output


# --- Plan approval tests ---


def test_create_plan_pauses_for_approval():
    import json
    from flux import ExecutionContext, workflow
    from flux.domain.events import ExecutionEventType

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools(approve_plan=True)
        create_plan_tool = tools[0]
        return await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B."},
                ],
            ),
        )

    ctx = test_wf.run()
    assert ctx.is_paused
    pause_events = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
    assert len(pause_events) == 1
    pause_output = pause_events[0].value["output"]
    assert len(pause_output["steps"]) == 2


def test_create_plan_resumes_with_approved_plan():
    import json
    from flux import ExecutionContext, workflow
    from flux.domain.events import ExecutionEventType

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, summary_fn = await build_plan_tools(approve_plan=True)
        create_plan_tool = tools[0]
        result = await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B."},
                ],
            ),
        )
        return {"result": result, "summary": summary_fn()}

    ctx = test_wf.run()
    assert ctx.is_paused

    pause_events = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
    pause_output = pause_events[0].value["output"]

    ctx = test_wf.resume(ctx.execution_id, pause_output)
    assert ctx.has_succeeded
    assert len(ctx.output["result"]["steps"]) == 2
    assert ctx.output["summary"] is not None


def test_create_plan_resumes_with_modified_plan():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools(approve_plan=True)
        create_plan_tool = tools[0]
        return await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B."},
                ],
            ),
        )

    ctx = test_wf.run()
    assert ctx.is_paused

    modified = {
        "steps": [
            {"name": "a", "description": "Do A revised."},
            {"name": "b", "description": "Do B revised."},
            {"name": "c", "description": "Do C.", "depends_on": ["a"]},
        ],
    }
    ctx = test_wf.resume(ctx.execution_id, modified)
    assert ctx.has_succeeded
    assert len(ctx.output["steps"]) == 3


def test_create_plan_resumes_with_rejection():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, summary_fn = await build_plan_tools(approve_plan=True)
        create_plan_tool = tools[0]
        result = await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B."},
                ],
            ),
        )
        return {"result": result, "summary": summary_fn()}

    ctx = test_wf.run()
    assert ctx.is_paused

    ctx = test_wf.resume(ctx.execution_id, {"rejected": True})
    assert ctx.has_succeeded
    assert "error" in ctx.output["result"]
    assert ctx.output["summary"] is None


def test_create_plan_no_pause_when_approval_disabled():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools(approve_plan=False)
        create_plan_tool = tools[0]
        return await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B."},
                ],
            ),
        )

    ctx = test_wf.run()
    assert ctx.has_succeeded


# --- LTM persistence tests ---


def test_build_plan_tools_restores_from_ltm():
    import json
    from flux import ExecutionContext, workflow
    from flux.tasks.ai.memory.long_term_memory import LongTermMemory
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()

    @workflow
    async def plan_wf(ctx: ExecutionContext):
        ltm = LongTermMemory(provider=provider, scope="_plan")
        phase = ctx.input or "setup"
        if phase == "setup":
            tools, _ = await build_plan_tools(long_term_memory=ltm)
            create_plan_tool = tools[0]
            mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")
            await create_plan_tool(
                steps=json.dumps(
                    [
                        {"name": "a", "description": "Do A."},
                        {"name": "b", "description": "Do B."},
                    ],
                ),
            )
            await mark_step_done_tool(step_name="a", result="Done A.")
            return "setup done"
        else:
            tools, summary_fn = await build_plan_tools(long_term_memory=ltm)
            get_plan_tool = next(t for t in tools if t.func.__name__ == "get_plan")
            plan = await get_plan_tool()
            return {"plan": plan, "summary": summary_fn()}

    ctx = plan_wf.run("setup")
    assert ctx.has_succeeded

    ctx = plan_wf.run("restore")
    assert ctx.has_succeeded
    step_a = next(s for s in ctx.output["plan"]["steps"] if s["name"] == "a")
    assert step_a["status"] == "completed"
    assert step_a["result"] == "Done A."
    assert "1/2" in ctx.output["summary"]


def test_build_plan_tools_works_without_ltm():
    import json
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf2(ctx: ExecutionContext):
        tools, summary_fn = await build_plan_tools()
        create_plan_tool = tools[0]
        await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B."},
                ],
            ),
        )
        return summary_fn()

    ctx = test_wf2.run()
    assert ctx.has_succeeded
    assert "0/2" in ctx.output
