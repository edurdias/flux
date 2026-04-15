from __future__ import annotations

from abc import ABC, abstractmethod

from flux.agents.types import AgentDefinition
from flux.models import AgentModel, RepositoryFactory


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

    def delete(self, name: str) -> None:
        with self.session() as session:
            model = session.get(AgentModel, name)
            if not model:
                raise ValueError(f"Agent '{name}' not found")
            session.delete(model)
            session.commit()

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
            reasoning_effort=model.reasoning_effort,
            long_term_memory=model.long_term_memory,
        )
