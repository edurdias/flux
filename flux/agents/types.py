from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class AgentDefinition(BaseModel):
    name: str
    model: str
    system_prompt: str
    description: str | None = None
    tools: list[Any] = Field(default_factory=list)
    tools_file: str | None = None
    workflow_file: str | None = None
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)
    skills_dir: str | None = None
    agents: list[str] = Field(default_factory=list)
    planning: bool = False
    max_plan_steps: int = 20
    approve_plan: bool = False
    max_tool_calls: int = 10
    max_concurrent_tools: int | None = None
    max_tokens: int = 4096
    stream: bool = True
    approval_mode: str = "default"
    # Split policy (issue #146): explicit values win over the legacy
    # approval_mode mapping. None defers.
    autonomy: str | None = None
    approval_routing: str | None = None
    reasoning_effort: str | None = None
    long_term_memory: dict[str, Any] | None = None

    @field_validator("model")
    @classmethod
    def validate_model_format(cls, v: str) -> str:
        if "/" not in v:
            raise ValueError(f"Model must be in 'provider/model_name' format, got: '{v}'")
        return v

    @field_validator("autonomy")
    @classmethod
    def validate_autonomy(cls, v: str | None) -> str | None:
        from flux.tasks.ai.approval_policy import AUTONOMY_LEVELS

        if v is not None and v not in AUTONOMY_LEVELS:
            raise ValueError(f"autonomy must be one of {AUTONOMY_LEVELS}, got: '{v}'")
        return v

    @field_validator("approval_routing")
    @classmethod
    def validate_approval_routing(cls, v: str | None) -> str | None:
        from flux.tasks.ai.approval_policy import ROUTING_MODES

        if v is not None and v not in ROUTING_MODES:
            raise ValueError(f"approval_routing must be one of {ROUTING_MODES}, got: '{v}'")
        return v

    @field_validator("reasoning_effort")
    @classmethod
    def validate_reasoning_effort(cls, v: str | None) -> str | None:
        if v is not None and v not in ("low", "medium", "high"):
            raise ValueError(
                f"reasoning_effort must be 'low', 'medium', 'high', or None, got: '{v}'",
            )
        return v

    @model_validator(mode="after")
    def validate_long_term_memory(self) -> AgentDefinition:
        if self.long_term_memory is not None:
            if not self.long_term_memory.get("connection"):
                raise ValueError(
                    "long_term_memory.connection is required when long_term_memory is set",
                )
        return self

    def has_skills_bundle(self) -> bool:
        """Return True if skills_dir carries an inline JSON bundle (vs a worker-side path)."""
        if not self.skills_dir:
            return False
        try:
            return isinstance(json.loads(self.skills_dir), dict)
        except (json.JSONDecodeError, ValueError):
            return False

    def requires_code_upload_permission(self) -> bool:
        """Return True if this definition ships content that escalates beyond ``agent:*:create``.

        ``tools_file``/``workflow_file`` are exec'd on workers; an inline ``skills_dir`` bundle
        ships arbitrary file content materialized on the worker filesystem.
        """
        return bool(self.tools_file or self.workflow_file or self.has_skills_bundle())


def payload_ships_code(value: Any) -> bool:
    """``requires_code_upload_permission`` for a raw, unvalidated payload.

    Agent definitions are mirrored into the config store as plain JSON, so the
    same rule has to hold there without constructing an AgentDefinition.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return False
    if not isinstance(value, dict):
        return False
    if value.get("tools_file") or value.get("workflow_file"):
        return True
    skills = value.get("skills_dir")
    if isinstance(skills, str):
        try:
            return isinstance(json.loads(skills), dict)
        except (json.JSONDecodeError, ValueError):
            return False
    return isinstance(skills, dict)


class AgentPauseOutput(BaseModel):
    type: str


class ChatResponseOutput(AgentPauseOutput):
    type: Literal["chat_response"] = "chat_response"
    content: str | None
    turn: int


class SessionEndOutput(AgentPauseOutput):
    type: Literal["session_end"] = "session_end"
    reason: str
    turns: int
