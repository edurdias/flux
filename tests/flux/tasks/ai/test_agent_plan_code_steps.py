from __future__ import annotations

import json as _json

from flux.tasks.ai.agent_plan import AgentPlan, AgentStep, build_plan_tools


async def _answer():
    return 42


async def _raise():
    raise RuntimeError("boom")


def test_step_defaults_to_reasoning():
    s = AgentStep(name="a", description="d")
    assert s.type == "reasoning"
    assert s.code is None
    assert "type" not in s.to_dict()
    assert "code" not in s.to_dict()


def test_code_step_roundtrips_through_dict():
    s = AgentStep(
        name="a",
        description="d",
        type="code",
        code="async def step(deps, input):\n    return await now()\n",
    )
    d = s.to_dict()
    assert d["type"] == "code"
    assert d["code"] == "async def step(deps, input):\n    return await now()\n"
    restored = AgentPlan.from_dict({"steps": [d]}).steps[0]
    assert restored.type == "code"
    assert restored.code == "async def step(deps, input):\n    return await now()\n"


def test_agent_config_defaults_off():
    from flux.config import Configuration

    cfg = Configuration.get().settings.agent
    assert cfg.dynamic_code_steps_enabled is False
    assert cfg.dynamic_code_steps_agent_tools_enabled is False
    assert cfg.dynamic_code_step_timeout == 30


def _tool(tools, name):
    return next((t for t in tools if t.name == name), None)


def test_default_code_bindings_excludes_graph():
    from flux.tasks.ai.agent import _build_code_bindings

    b = _build_code_bindings(agents=[], tools_enabled=False)
    for name in (
        "now",
        "uuid4",
        "parallel",
        "pipeline",
        "call",
        "progress",
        "choice",
        "randint",
        "randrange",
        "sleep",
    ):
        assert name in b
    assert "Graph" not in b  # unusable: fluent builder needs attribute access
    assert "delegate" not in b


def test_code_bindings_delegate_requires_agents():
    from flux.tasks.ai.agent import _build_code_bindings

    class _StubAgent:
        name = "helper"
        description = "a helper sub-agent"

        def __call__(self, *a, **k):
            return None

    assert "delegate" not in _build_code_bindings(agents=[], tools_enabled=True)
    assert "delegate" in _build_code_bindings(agents=[_StubAgent()], tools_enabled=True)


def test_approve_plan_resume_rejects_invalid_code():
    from flux import ExecutionContext, workflow

    @workflow
    async def wf(ctx: ExecutionContext):
        tools, _, _ = await build_plan_tools(
            approve_plan=True,
            code_config={"enabled": True, "timeout": 5},
            code_bindings={"now": lambda: 1},
        )
        create = _tool(tools, "create_plan")
        return await create('[{"name":"a","description":"d"},{"name":"b","description":"d"}]')

    ctx = wf.run()
    assert ctx.is_paused
    modified = {
        "steps": [
            {
                "name": "a",
                "description": "d",
                "type": "code",
                "code": "async def step(deps, input):\n    import os\n    return os\n",
            },
            {"name": "b", "description": "d"},
        ],
    }
    ctx = wf.resume(ctx.execution_id, modified)
    assert ctx.has_succeeded
    assert "error" in ctx.output and "code" in ctx.output["error"].lower()


def test_agent_config_rejects_nonpositive_timeout():
    import pytest
    from pydantic import ValidationError

    from flux.config import AgentConfig

    with pytest.raises(ValidationError):
        AgentConfig(dynamic_code_step_timeout=0)


GOOD_STEP = "async def step(deps, input):\n    return await answer()\n"


def test_create_plan_accepts_v2_code_step():
    from flux import ExecutionContext, workflow

    @workflow
    async def wf(ctx: ExecutionContext):
        tools, _, _ = await build_plan_tools(
            code_config={"enabled": True, "timeout": 5},
            code_bindings={"answer": lambda: 42},
        )
        create = _tool(tools, "create_plan")
        steps = _json.dumps(
            [
                {"name": "calc", "description": "c", "type": "code", "code": GOOD_STEP},
                {"name": "think", "description": "d"},
            ],
        )
        return await create(steps)

    ctx = wf.run()
    assert ctx.has_succeeded
    assert "error" not in ctx.output


def test_create_plan_rejects_invalid_v2_code():
    from flux import ExecutionContext, workflow

    @workflow
    async def wf(ctx: ExecutionContext):
        tools, _, _ = await build_plan_tools(
            code_config={"enabled": True, "timeout": 5},
            code_bindings={"answer": lambda: 42},
        )
        create = _tool(tools, "create_plan")
        bad = "async def step(deps, input):\n    import os\n    return os\n"
        steps = _json.dumps(
            [
                {"name": "calc", "description": "c", "type": "code", "code": bad},
                {"name": "think", "description": "d"},
            ],
        )
        return await create(steps)

    ctx = wf.run()
    assert ctx.has_succeeded
    assert "error" in ctx.output and "code" in ctx.output["error"].lower()


def test_run_step_tool_removed():
    from flux import ExecutionContext, workflow

    @workflow
    async def wf(ctx: ExecutionContext):
        tools, _, _ = await build_plan_tools(
            code_config={"enabled": True, "timeout": 5},
            code_bindings={"answer": lambda: 42},
        )
        return [t.name for t in tools]

    ctx = wf.run()
    assert ctx.has_succeeded
    assert "run_step" not in ctx.output


def test_preamble_describes_v2_contract_when_enabled():
    from flux.tasks.ai.agent_plan import build_plan_preamble

    text = build_plan_preamble(code_steps_enabled=True, code_bindings=["now", "parallel"])
    assert "async def step(deps, input)" in text
    assert "attribute access" in text.lower()
    assert "now" in text and "parallel" in text
    assert "async def step" not in build_plan_preamble(code_steps_enabled=False)


def test_advance_runs_ready_code_steps_to_fixpoint():
    from flux import ExecutionContext, workflow

    @workflow
    async def wf(ctx: ExecutionContext):
        tools, _, advance = await build_plan_tools(
            code_config={"enabled": True, "timeout": 5},
            code_bindings={"answer": _answer},
        )
        create = _tool(tools, "create_plan")
        get_plan = _tool(tools, "get_plan")
        a = "async def step(deps, input):\n    return await answer()\n"
        b = "async def step(deps, input):\n    return deps['a'] + 1\n"
        steps = _json.dumps(
            [
                {"name": "a", "description": "x", "type": "code", "code": a},
                {"name": "b", "description": "y", "type": "code", "code": b, "depends_on": ["a"]},
            ],
        )
        await create(steps)
        summary = await advance()
        return {"summary": summary, "plan": await get_plan()}

    ctx = wf.run()
    assert ctx.has_succeeded
    steps = {s["name"]: s for s in ctx.output["plan"]["steps"]}
    assert steps["a"]["status"] == "completed" and steps["a"]["result"] == 42
    assert steps["b"]["status"] == "completed" and steps["b"]["result"] == 43


def test_advance_waits_on_reasoning_dependency():
    from flux import ExecutionContext, workflow

    @workflow
    async def wf(ctx: ExecutionContext):
        tools, _, advance = await build_plan_tools(
            code_config={"enabled": True, "timeout": 5},
            code_bindings={"answer": _answer},
        )
        create = _tool(tools, "create_plan")
        get_plan = _tool(tools, "get_plan")
        c = "async def step(deps, input):\n    return deps['think']\n"
        steps = _json.dumps(
            [
                {"name": "think", "description": "reason"},
                {
                    "name": "use",
                    "description": "u",
                    "type": "code",
                    "code": c,
                    "depends_on": ["think"],
                },
            ],
        )
        await create(steps)
        await advance()
        return await get_plan()

    ctx = wf.run()
    assert ctx.has_succeeded
    steps = {s["name"]: s for s in ctx.output["steps"]}
    assert steps["use"]["status"] == "pending"


def test_advance_records_failure():
    from flux import ExecutionContext, workflow

    @workflow
    async def wf(ctx: ExecutionContext):
        tools, _, advance = await build_plan_tools(
            code_config={"enabled": True, "timeout": 5},
            code_bindings={"boom": _raise},
        )
        create = _tool(tools, "create_plan")
        get_plan = _tool(tools, "get_plan")
        a = "async def step(deps, input):\n    return await boom()\n"
        steps = _json.dumps(
            [
                {"name": "a", "description": "x", "type": "code", "code": a},
                {"name": "b", "description": "y"},
            ],
        )
        await create(steps)
        summary = await advance()
        return {"summary": summary, "plan": await get_plan()}

    ctx = wf.run()
    assert ctx.has_succeeded
    steps = {s["name"]: s for s in ctx.output["plan"]["steps"]}
    assert steps["a"]["status"] == "failed"
