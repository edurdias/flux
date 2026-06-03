from __future__ import annotations

from flux.tasks.ai.agent_plan import AgentStep, AgentPlan


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
