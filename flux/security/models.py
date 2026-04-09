from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import relationship

from flux.models import Base


class RoleModel(Base):
    __tablename__ = "roles"

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: uuid4().hex)
    name = Column(String, unique=True, nullable=False)
    permissions = Column(JSON, nullable=False)
    built_in = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __init__(
        self,
        name: str,
        permissions: list[str],
        built_in: bool = False,
        id: str | None = None,
    ):
        self.id = id or uuid4().hex
        self.name = name
        self.permissions = permissions
        self.built_in = built_in
        self.created_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)


class ServiceAccountModel(Base):
    __tablename__ = "service_accounts"

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: uuid4().hex)
    name = Column(String, unique=True, nullable=False)
    roles = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    keys = relationship(
        "APIKeyModel",
        back_populates="service_account",
        cascade="all, delete-orphan",
    )

    def __init__(
        self,
        name: str,
        roles: list[str],
        id: str | None = None,
    ):
        self.id = id or uuid4().hex
        self.name = name
        self.roles = roles
        self.created_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)


class APIKeyModel(Base):
    __tablename__ = "api_keys"

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: uuid4().hex)
    service_account_id = Column(String, ForeignKey("service_accounts.id"), nullable=False)
    name = Column(String, nullable=False)
    key_hash = Column(String, nullable=False)
    key_prefix = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    service_account = relationship("ServiceAccountModel", back_populates="keys")

    __table_args__ = (
        UniqueConstraint("service_account_id", "name", name="uix_api_key_sa_name"),
        UniqueConstraint("key_hash", name="uix_api_key_hash"),
    )

    def __init__(
        self,
        service_account_id: str,
        name: str,
        key_hash: str,
        key_prefix: str,
        expires_at: datetime | None = None,
        id: str | None = None,
    ):
        self.id = id or uuid4().hex
        self.service_account_id = service_account_id
        self.name = name
        self.key_hash = key_hash
        self.key_prefix = key_prefix
        self.expires_at = expires_at
        self.created_at = datetime.now(timezone.utc)
