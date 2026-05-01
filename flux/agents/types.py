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
    reasoning_effort: str | None = None
    long_term_memory: dict[str, Any] | None = None

    @field_validator("model")
    @classmethod
    def validate_model_format(cls, v: str) -> str:
        if "/" not in v:
            raise ValueError(f"Model must be in 'provider/model_name' format, got: '{v}'")
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
