from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy import select

from flux.models import ConfigModel, RepositoryFactory


class ConfigManager(ABC):
    @abstractmethod
    def save(self, name: str, value: Any) -> None:
        raise NotImplementedError()

    @abstractmethod
    def remove(self, name: str) -> None:
        raise NotImplementedError()

    @abstractmethod
    def get(self, config_requests: list[str]) -> dict[str, Any]:
        raise NotImplementedError()

    @abstractmethod
    def all(self) -> list[str]:
        raise NotImplementedError()

    @staticmethod
    def current() -> ConfigManager:
        from flux.remote_managers import get_remote_config

        remote = get_remote_config()
        if remote is not None:
            return remote
        return DatabaseConfigManager()


class DatabaseConfigManager(ConfigManager):
    def __init__(self):
        self._repository = RepositoryFactory.create_repository()

    def session(self):
        return self._repository.session()

    def save(self, name: str, value: Any) -> None:
        if value is None:
            raise ValueError("Config value cannot be None")

        serialized = json.dumps(value)

        with self.session() as session:
            config = session.get(ConfigModel, name)
            if config:
                config.value = serialized
            else:
                session.add(ConfigModel(name=name, value=serialized))
            session.commit()

    def remove(self, name: str) -> None:
        with self.session() as session:
            config = session.get(ConfigModel, name)
            if config:
                session.delete(config)
                session.commit()

    def get(self, config_requests: list[str]) -> dict[str, Any]:
        with self.session() as session:
            stmt = select(ConfigModel.name, ConfigModel.value).where(
                ConfigModel.name.in_(config_requests),
            )
            result = {row[0]: json.loads(row[1]) for row in session.execute(stmt)}
            if missing := set(config_requests) - set(result):
                raise ValueError(f"The following configs were not found: {list(missing)}")
            return result

    def all(self) -> list[str]:
        with self.session() as session:
            stmt = select(ConfigModel.name)
            return [row[0] for row in session.execute(stmt)]
