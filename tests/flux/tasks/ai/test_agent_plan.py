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
    tools, summary_fn = build_plan_tools()
    assert len(tools) == 5
    assert callable(summary_fn)
    assert summary_fn() is None


def test_build_plan_tools_tool_names():
    tools, _ = build_plan_tools()
    names = {t.func.__name__ for t in tools}
    assert names == {"create_plan", "start_step", "mark_step_done", "mark_step_failed", "get_plan"}


def test_create_plan():
    import json

    from flux import ExecutionContext, workflow

    tools, summary_fn = build_plan_tools()
    create_plan_tool = tools[0]

    @workflow
    async def test_wf(ctx: ExecutionContext):
        return await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "research", "description": "Research the topic."},
                    {"name": "analyze", "description": "Analyze data.", "depends_on": ["research"]},
                ],
            ),
        )

    ctx = test_wf.run()
    assert ctx.has_succeeded
    result = ctx.output
    assert len(result["steps"]) == 2
    assert result["steps"][0]["name"] == "research"
    assert result["steps"][1]["name"] == "analyze"
    assert result["steps"][1]["depends_on"] == ["research"]
    assert summary_fn() is not None
    assert "0/2" in summary_fn()


def test_create_plan_invalid_name():
    import json

    from flux import ExecutionContext, workflow

    tools, _ = build_plan_tools()
    create_plan_tool = tools[0]

    @workflow
    async def test_wf(ctx: ExecutionContext):
        return await create_plan_tool(
            steps=json.dumps([{"name": "Bad-Name", "description": "Invalid."}]),
        )

    ctx = test_wf.run()
    assert ctx.has_failed


def test_create_plan_circular_dependency():
    import json

    from flux import ExecutionContext, workflow

    tools, _ = build_plan_tools()
    create_plan_tool = tools[0]

    @workflow
    async def test_wf(ctx: ExecutionContext):
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

    tools, _ = build_plan_tools()
    create_plan_tool = tools[0]
    mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")

    @workflow
    async def test_wf(ctx: ExecutionContext):
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

    tools, summary_fn = build_plan_tools()
    create_plan_tool = tools[0]
    mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")

    @workflow
    async def test_wf(ctx: ExecutionContext):
        await create_plan_tool(
            steps=json.dumps([{"name": "a", "description": "Do A."}]),
        )
        return await mark_step_done_tool(step_name="a", result="Done A.")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output["status"] == "completed"
    assert ctx.output["result"] == "Done A."
    assert "1/1" in summary_fn()


def test_mark_step_done_no_plan():
    from flux import ExecutionContext, workflow

    tools, _ = build_plan_tools()
    mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")

    @workflow
    async def test_wf(ctx: ExecutionContext):
        return await mark_step_done_tool(step_name="a", result="Done.")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "error" in ctx.output


def test_mark_step_done_not_found():
    import json

    from flux import ExecutionContext, workflow

    tools, _ = build_plan_tools()
    create_plan_tool = tools[0]
    mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")

    @workflow
    async def test_wf(ctx: ExecutionContext):
        await create_plan_tool(
            steps=json.dumps([{"name": "a", "description": "Do A."}]),
        )
        return await mark_step_done_tool(step_name="nonexistent", result="Done.")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "error" in ctx.output
    assert "nonexistent" in ctx.output["error"]


def test_mark_step_done_already_completed():
    import json

    from flux import ExecutionContext, workflow

    tools, _ = build_plan_tools()
    create_plan_tool = tools[0]
    mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")

    @workflow
    async def test_wf(ctx: ExecutionContext):
        await create_plan_tool(
            steps=json.dumps([{"name": "a", "description": "Do A."}]),
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

    tools, _ = build_plan_tools()
    create_plan_tool = tools[0]
    get_plan_tool = next(t for t in tools if t.func.__name__ == "get_plan")

    @workflow
    async def test_wf(ctx: ExecutionContext):
        await create_plan_tool(
            steps=json.dumps([{"name": "a", "description": "Do A."}]),
        )
        return await get_plan_tool()

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert len(ctx.output["steps"]) == 1


def test_get_plan_no_plan():
    from flux import ExecutionContext, workflow

    tools, _ = build_plan_tools()
    get_plan_tool = next(t for t in tools if t.func.__name__ == "get_plan")

    @workflow
    async def test_wf(ctx: ExecutionContext):
        return await get_plan_tool()

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "message" in ctx.output


# --- start_step tool tests ---


def test_start_step():
    import json
    from flux import ExecutionContext, workflow

    tools, summary_fn = build_plan_tools()
    create_plan_tool = tools[0]
    start_step_tool = next(t for t in tools if t.func.__name__ == "start_step")

    @workflow
    async def test_wf(ctx: ExecutionContext):
        await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B.", "depends_on": ["a"]},
                ],
            ),
        )
        return await start_step_tool(step_name="a")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output["status"] == "in_progress"
    assert 'Active: "a"' in summary_fn()


def test_start_step_no_plan():
    from flux import ExecutionContext, workflow

    tools, _ = build_plan_tools()
    start_step_tool = next(t for t in tools if t.func.__name__ == "start_step")

    @workflow
    async def test_wf(ctx: ExecutionContext):
        return await start_step_tool(step_name="a")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "error" in ctx.output


def test_start_step_not_found():
    import json
    from flux import ExecutionContext, workflow

    tools, _ = build_plan_tools()
    create_plan_tool = tools[0]
    start_step_tool = next(t for t in tools if t.func.__name__ == "start_step")

    @workflow
    async def test_wf(ctx: ExecutionContext):
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

    tools, _ = build_plan_tools()
    create_plan_tool = tools[0]
    start_step_tool = next(t for t in tools if t.func.__name__ == "start_step")

    @workflow
    async def test_wf(ctx: ExecutionContext):
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

    tools, _ = build_plan_tools()
    create_plan_tool = tools[0]
    start_step_tool = next(t for t in tools if t.func.__name__ == "start_step")

    @workflow
    async def test_wf(ctx: ExecutionContext):
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

    tools, _ = build_plan_tools(strict_dependencies=True)
    create_plan_tool = tools[0]
    start_step_tool = next(t for t in tools if t.func.__name__ == "start_step")

    @workflow
    async def test_wf(ctx: ExecutionContext):
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

    tools, summary_fn = build_plan_tools()
    create_plan_tool = tools[0]
    start_step_tool = next(t for t in tools if t.func.__name__ == "start_step")
    mark_step_failed_tool = next(t for t in tools if t.func.__name__ == "mark_step_failed")

    @workflow
    async def test_wf(ctx: ExecutionContext):
        await create_plan_tool(
            steps=json.dumps(
                [
                    {"name": "a", "description": "Do A."},
                    {"name": "b", "description": "Do B."},
                ],
            ),
        )
        await start_step_tool(step_name="a")
        return await mark_step_failed_tool(step_name="a", reason="Connection timeout.")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output["status"] == "failed"
    assert ctx.output["error"] == "Connection timeout."
    assert "1 failed" in summary_fn()


def test_mark_step_failed_no_plan():
    from flux import ExecutionContext, workflow

    tools, _ = build_plan_tools()
    mark_step_failed_tool = next(t for t in tools if t.func.__name__ == "mark_step_failed")

    @workflow
    async def test_wf(ctx: ExecutionContext):
        return await mark_step_failed_tool(step_name="a", reason="Error.")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "error" in ctx.output


def test_mark_step_failed_allows_from_pending():
    import json
    from flux import ExecutionContext, workflow

    tools, _ = build_plan_tools()
    create_plan_tool = tools[0]
    mark_step_failed_tool = next(t for t in tools if t.func.__name__ == "mark_step_failed")

    @workflow
    async def test_wf(ctx: ExecutionContext):
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

    tools, _ = build_plan_tools()
    create_plan_tool = tools[0]
    mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")
    mark_step_failed_tool = next(t for t in tools if t.func.__name__ == "mark_step_failed")

    @workflow
    async def test_wf(ctx: ExecutionContext):
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

    tools, _ = build_plan_tools()
    create_plan_tool = tools[0]
    start_step_tool = next(t for t in tools if t.func.__name__ == "start_step")
    mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")

    @workflow
    async def test_wf(ctx: ExecutionContext):
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

    tools, _ = build_plan_tools()
    create_plan_tool = tools[0]
    mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")

    @workflow
    async def test_wf(ctx: ExecutionContext):
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

    tools, _ = build_plan_tools()
    create_plan_tool = tools[0]
    mark_step_failed_tool = next(t for t in tools if t.func.__name__ == "mark_step_failed")
    mark_step_done_tool = next(t for t in tools if t.func.__name__ == "mark_step_done")

    @workflow
    async def test_wf(ctx: ExecutionContext):
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
