def test_agent_config_defaults_off():
    from flux.config import Configuration
    cfg = Configuration.get().settings.agent
    assert cfg.dynamic_code_steps_enabled is False
    assert cfg.dynamic_code_steps_agent_tools_enabled is False
    assert cfg.dynamic_code_step_timeout == 30
