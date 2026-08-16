from __future__ import annotations

from abc import ABC, abstractmethod

from flux.agents.types import AgentDefinition
from flux.config_manager import ConfigManager
from flux.errors import HookNameConflictError
from flux.hooks.registry import HookRegistry
from flux.models import AgentModel, RepositoryFactory


# Reserved to the agent routes: agent_chat execs tools_file from this key, so
# the generic config endpoints refuse the prefix rather than let config:*:manage
# sidestep the workflow:*:*:register gate.
AGENT_CONFIG_PREFIX = "agent:"


def _config_key(name: str) -> str:
    """Config key under which an agent definition is published.

    The ``agents/agent_chat`` template loads an agent definition at runtime
    via ``get_config(f"agent:{agent_name}")``. Keeping the key convention in
    a single helper means no caller has to know the string format.
    """
    return f"{AGENT_CONFIG_PREFIX}{name}"


class AgentManager(ABC):
    @abstractmethod
    def create(self, definition: AgentDefinition) -> None:
        raise NotImplementedError()

    @abstractmethod
    def get(self, name: str) -> AgentDefinition:
        raise NotImplementedError()

    @abstractmethod
    def update(self, definition: AgentDefinition) -> None:
        raise NotImplementedError()

    @abstractmethod
    def delete(self, name: str) -> None:
        raise NotImplementedError()

    @abstractmethod
    def list(self) -> list[AgentDefinition]:
        raise NotImplementedError()

    @staticmethod
    def current() -> AgentManager:
        return DatabaseAgentManager()


class DatabaseAgentManager(AgentManager):
    def __init__(self):
        self._repository = RepositoryFactory.create_repository()

    def session(self):
        return self._repository.session()

    def create(self, definition: AgentDefinition) -> None:
        with self.session() as session:
            existing = session.get(AgentModel, definition.name)
            if existing:
                raise ValueError(f"Agent '{definition.name}' already exists")
            model = AgentModel(**definition.model_dump())
            session.add(model)
            session.commit()
        # Publish the definition to the configs table so the agent_chat
        # template can load it at runtime via get_config("agent:<name>").
        # Kept outside the agents-table transaction to respect each manager's
        # session boundary; a failure here leaves the agents row behind, which
        # update()/delete() cope with idempotently.
        ConfigManager.current().save(_config_key(definition.name), definition.model_dump())
        try:
            HookRegistry.create().reconcile_owned_hooks(
                owner_type="agent",
                owner_ref=definition.name,
                specs=definition.hooks,
            )
        except HookNameConflictError as e:
            # This manager has no HTTP layer of its own -- it speaks in
            # ValueError the same way the "already exists"/"not found"
            # raises above do, and admin_routes.py already maps that to a
            # clean 4xx instead of a raw exception reaching the response.
            raise ValueError(str(e)) from e

    def get(self, name: str) -> AgentDefinition:
        with self.session() as session:
            model = session.get(AgentModel, name)
            if not model:
                raise ValueError(f"Agent '{name}' not found")
            return self._to_definition(model)

    def update(self, definition: AgentDefinition) -> None:
        with self.session() as session:
            model = session.get(AgentModel, definition.name)
            if not model:
                raise ValueError(f"Agent '{definition.name}' not found")
            data = definition.model_dump(exclude={"name"})
            for key, value in data.items():
                setattr(model, key, value)
            session.commit()
        ConfigManager.current().save(_config_key(definition.name), definition.model_dump())
        # Deliberately not wrapped in ValueError like create()'s equivalent
        # block below: admin_update_agent needs to tell a hook-name conflict
        # (409) apart from this method's own "not found" ValueError (404),
        # and both would look identical as ValueError once wrapped.
        HookRegistry.create().reconcile_owned_hooks(
            owner_type="agent",
            owner_ref=definition.name,
            specs=definition.hooks,
        )

    def delete(self, name: str) -> None:
        with self.session() as session:
            model = session.get(AgentModel, name)
            if not model:
                raise ValueError(f"Agent '{name}' not found")
            session.delete(model)
            session.commit()
        # Best-effort config cleanup; ConfigManager.remove is a no-op if
        # the key is already absent so this is safe to call unconditionally.
        ConfigManager.current().remove(_config_key(name))
        HookRegistry.create().delete_owned_hooks(owner_type="agent", owner_ref=name)

    def list(self) -> list[AgentDefinition]:
        with self.session() as session:
            models = session.query(AgentModel).all()
            return [self._to_definition(m) for m in models]

    @staticmethod
    def _to_definition(model: AgentModel) -> AgentDefinition:
        return AgentDefinition(
            name=model.name,
            model=model.model,
            system_prompt=model.system_prompt,
            description=model.description,
            tools=model.tools or [],
            tools_file=model.tools_file,
            workflow_file=model.workflow_file,
            mcp_servers=model.mcp_servers or [],
            skills_dir=model.skills_dir,
            agents=model.agents or [],
            planning=model.planning,
            max_plan_steps=model.max_plan_steps,
            approve_plan=model.approve_plan,
            max_tool_calls=model.max_tool_calls,
            max_concurrent_tools=model.max_concurrent_tools,
            max_tokens=model.max_tokens,
            stream=model.stream,
            approval_mode=model.approval_mode,
            autonomy=model.autonomy,
            approval_routing=model.approval_routing,
            reasoning_effort=model.reasoning_effort,
            long_term_memory=model.long_term_memory,
            hooks=model.hooks or [],
        )
