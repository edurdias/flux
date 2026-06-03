from __future__ import annotations

from flux.tasks.ai.agent_plan import AgentPlan, AgentStep, build_plan_tools


def test_step_defaults_to_reasoning():
    s = AgentStep(name="a", description="d")
    assert s.type == "reasoning"
    assert s.code is None
    assert "type" not in s.to_dict()
    assert "code" not in s.to_dict()


def test_code_step_roundtrips_through_dict():
    s = AgentStep(name="a", description="d", type="code", code="lambda: now()")
    d = s.to_dict()
    assert d["type"] == "code"
    assert d["code"] == "lambda: now()"
    restored = AgentPlan.from_dict({"steps": [d]}).steps[0]
    assert restored.type == "code"
    assert restored.code == "lambda: now()"


def test_agent_config_defaults_off():
    from flux.config import Configuration

    cfg = Configuration.get().settings.agent
    assert cfg.dynamic_code_steps_enabled is False
    assert cfg.dynamic_code_steps_agent_tools_enabled is False
    assert cfg.dynamic_code_step_timeout == 30


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


def test_create_plan_rejects_invalid_code():
    from flux import ExecutionContext, workflow

    @workflow
    async def wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools(
            code_config={"enabled": True, "timeout": 5},
            code_bindings={"now": lambda: 1},
        )
        create = _tool(tools, "create_plan")
        steps = '[{"name":"a","description":"d","type":"code","code":"lambda: __import__(\'os\')"},{"name":"b","description":"d"}]'
        return await create(steps)

    ctx = wf.run()
    assert ctx.has_succeeded
    result = ctx.output
    assert "error" in result and "code" in result["error"].lower()


def test_run_step_executes_code_and_records_result():
    from flux import ExecutionContext, workflow

    @workflow
    async def wf(ctx: ExecutionContext):
        tools, _ = await build_plan_tools(
            code_config={"enabled": True, "timeout": 5},
            code_bindings={"answer": lambda: 42},
        )
        create = _tool(tools, "create_plan")
        run_step = _tool(tools, "run_step")
        await create(
            '[{"name":"a","description":"compute","type":"code","code":"lambda: answer()"},{"name":"b","description":"d"}]',
        )
        return await run_step("a")

    ctx = wf.run()
    assert ctx.has_succeeded
    result = ctx.output
    assert result["status"] == "completed"
    assert result["result"] == 42


def test_preamble_describes_code_steps_when_enabled():
    from flux.tasks.ai.agent_plan import build_plan_preamble

    text = build_plan_preamble(code_steps_enabled=True)
    assert "code" in text.lower()
    assert "lambda" in text.lower()
    assert "lambda" not in build_plan_preamble(code_steps_enabled=False).lower()


def test_default_code_bindings_includes_builtins():
    from flux.tasks.ai.agent import _build_code_bindings

    b = _build_code_bindings(agents=[], tools_enabled=False)
    for name in (
        "now",
        "uuid4",
        "parallel",
        "pipeline",
        "call",
        "Graph",
        "progress",
        "choice",
        "randint",
        "randrange",
        "sleep",
    ):
        assert name in b
    assert "delegate" not in b


def test_code_bindings_includes_delegate_when_tools_enabled():
    from flux.tasks.ai.agent import _build_code_bindings

    b = _build_code_bindings(agents=[], tools_enabled=True)
    assert "delegate" in b
