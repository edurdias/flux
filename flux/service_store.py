from __future__ import annotations

import builtins
from dataclasses import dataclass, field
from datetime import datetime

from flux.models import RepositoryFactory, ServiceModel

_list = builtins.list


class ServiceNotFoundError(Exception):
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Service '{name}' not found")


@dataclass
class ServiceInfo:
    id: str
    name: str
    namespaces: list[str] = field(default_factory=list)
    workflows: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    mcp_enabled: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _to_info(model: ServiceModel) -> ServiceInfo:
    return ServiceInfo(
        id=model.id,
        name=model.name,
        namespaces=list(model.namespaces or []),
        workflows=list(model.workflows or []),
        exclusions=list(model.exclusions or []),
        mcp_enabled=bool(model.mcp_enabled),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class ServiceStore:
    def __init__(self):
        self._repository = RepositoryFactory.create_repository()

    def create(
        self,
        name: str,
        namespaces: _list[str] | None = None,
        workflows: _list[str] | None = None,
        exclusions: _list[str] | None = None,
        mcp_enabled: bool = False,
    ) -> ServiceInfo:
        with self._repository.session() as session:
            existing = session.query(ServiceModel).filter(ServiceModel.name == name).first()
            if existing:
                raise ValueError(f"Service '{name}' already exists")

            model = ServiceModel()
            model.name = name
            model.namespaces = namespaces or []
            model.workflows = workflows or []
            model.exclusions = exclusions or []
            model.mcp_enabled = mcp_enabled

            session.add(model)
            session.commit()
            session.refresh(model)
            return _to_info(model)

    def get(self, name: str) -> ServiceInfo | None:
        with self._repository.session() as session:
            model = session.query(ServiceModel).filter(ServiceModel.name == name).first()
            if model is None:
                return None
            return _to_info(model)

    def list(self) -> _list[ServiceInfo]:
        with self._repository.session() as session:
            models = session.query(ServiceModel).order_by(ServiceModel.name).all()
            return [_to_info(m) for m in models]

    def update(
        self,
        name: str,
        add_namespaces: _list[str] | None = None,
        add_workflows: _list[str] | None = None,
        add_exclusions: _list[str] | None = None,
        remove_namespaces: _list[str] | None = None,
        remove_workflows: _list[str] | None = None,
        remove_exclusions: _list[str] | None = None,
        mcp_enabled: bool | None = None,
    ) -> ServiceInfo:
        with self._repository.session() as session:
            model = session.query(ServiceModel).filter(ServiceModel.name == name).first()
            if model is None:
                raise ServiceNotFoundError(name)

            current_ns = list(model.namespaces or [])
            current_wf = list(model.workflows or [])
            current_ex = list(model.exclusions or [])

            if add_namespaces:
                current_ns = list(dict.fromkeys(current_ns + add_namespaces))
            if remove_namespaces:
                current_ns = [n for n in current_ns if n not in remove_namespaces]

            if add_workflows:
                current_wf = list(dict.fromkeys(current_wf + add_workflows))
            if remove_workflows:
                current_wf = [w for w in current_wf if w not in remove_workflows]

            if add_exclusions:
                current_ex = list(dict.fromkeys(current_ex + add_exclusions))
            if remove_exclusions:
                current_ex = [e for e in current_ex if e not in remove_exclusions]

            model.namespaces = current_ns
            model.workflows = current_wf
            model.exclusions = current_ex
            if mcp_enabled is not None:
                model.mcp_enabled = mcp_enabled

            session.commit()
            session.refresh(model)
            return _to_info(model)

    def delete(self, name: str) -> None:
        with self._repository.session() as session:
            model = session.query(ServiceModel).filter(ServiceModel.name == name).first()
            if model is None:
                raise ServiceNotFoundError(name)

            session.delete(model)
            session.commit()
