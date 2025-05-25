from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from flux.models import SecretModel
from flux.models import SQLiteRepository


class SecretManager(ABC):
    @abstractmethod
    def save(self, name: str, value: Any):  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def remove(self, name: str):  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def get(self, secret_requests: list[str]) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def all(self) -> list[str]:  # pragma: no cover
        """Return a list of all secret names."""
        raise NotImplementedError()

    @staticmethod
    def current() -> SecretManager:
        return SQLiteSecretManager()


class SQLiteSecretManager(SecretManager, SQLiteRepository):
    def __init__(self):
        super().__init__()

    def save(self, name: str, value: Any):
        if value is None:
            raise ValueError("Secret value cannot be None")

        with self.session() as session:
            try:
                secret = session.get(SecretModel, name)
                if secret:
                    secret.value = value
                else:
                    session.add(SecretModel(name=name, value=value))
                session.commit()
            except IntegrityError:  # pragma: no cover
                session.rollback()
                raise

    def remove(self, name: str):
        with self.session() as session:
            try:
                secret = session.get(SecretModel, name)
                if secret:
                    session.delete(secret)
                    session.commit()
            except IntegrityError:  # pragma: no cover
                session.rollback()
                raise

    def get(self, secret_requests: list[str]) -> dict[str, Any]:
        with self.session() as session:
            stmt = select(SecretModel.name, SecretModel.value).where(
                SecretModel.name.in_(secret_requests),
            )
            result = {row[0]: row[1] for row in session.execute(stmt)}
            if missing := set(secret_requests) - set(result):
                raise ValueError(f"The following secrets were not found: {list(missing)}")
            return result

    def all(self) -> list[str]:
        """Return a list of all secret names in the database."""
        with self.session() as session:
            stmt = select(SecretModel.name)
            result = [row[0] for row in session.execute(stmt)]
            return result
