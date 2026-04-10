from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import relationship

from flux.models import Base


class PrincipalModel(Base):
    __tablename__ = "principals"

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: uuid4().hex)
    type = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    external_issuer = Column(String, nullable=False)
    display_name = Column(String, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    metadata_ = Column("metadata", JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    last_seen_at = Column(DateTime, nullable=True)

    roles = relationship(
        "PrincipalRoleModel",
        back_populates="principal",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("subject", "external_issuer", name="uix_principal_subject_issuer"),
    )

    def __init__(
        self,
        type: str,
        subject: str,
        external_issuer: str,
        display_name: str | None = None,
        enabled: bool = True,
        metadata: dict | None = None,
        id: str | None = None,
    ):
        self.id = id or uuid4().hex
        self.type = type
        self.subject = subject
        self.external_issuer = external_issuer
        self.display_name = display_name
        self.enabled = enabled
        self.metadata_ = metadata or {}
        self.created_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)
        self.last_seen_at = None


class PrincipalRoleModel(Base):
    __tablename__ = "principal_roles"

    principal_id = Column(String, ForeignKey("principals.id"), primary_key=True, nullable=False)
    role_name = Column(String, primary_key=True, nullable=False)
    assigned_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    assigned_by = Column(String, nullable=True)

    principal = relationship("PrincipalModel", back_populates="roles")

    def __init__(self, principal_id: str, role_name: str, assigned_by: str | None = None):
        self.principal_id = principal_id
        self.role_name = role_name
        self.assigned_by = assigned_by
        self.assigned_at = datetime.now(timezone.utc)


class PrincipalRegistry:
    def __init__(self, session_factory: Callable):
        self._session_factory = session_factory

    def find(self, subject: str, external_issuer: str) -> PrincipalModel | None:
        session = self._session_factory()
        try:
            return (
                session.query(PrincipalModel)
                .filter_by(subject=subject, external_issuer=external_issuer)
                .first()
            )
        finally:
            session.close()

    def create(
        self,
        type: str,
        subject: str,
        external_issuer: str,
        display_name: str | None = None,
        metadata: dict | None = None,
        enabled: bool = True,
    ) -> PrincipalModel:
        session = self._session_factory()
        try:
            principal = PrincipalModel(
                type=type,
                subject=subject,
                external_issuer=external_issuer,
                display_name=display_name,
                enabled=enabled,
                metadata=metadata or {},
            )
            session.add(principal)
            session.commit()
            session.refresh(principal)
            return principal
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_roles(self, principal_id: str) -> list[str]:
        session = self._session_factory()
        try:
            rows = session.query(PrincipalRoleModel).filter_by(principal_id=principal_id).all()
            return [r.role_name for r in rows]
        finally:
            session.close()

    def assign_role(
        self,
        principal_id: str,
        role_name: str,
        assigned_by: str | None = None,
    ) -> None:
        session = self._session_factory()
        try:
            existing = (
                session.query(PrincipalRoleModel)
                .filter_by(principal_id=principal_id, role_name=role_name)
                .first()
            )
            if not existing:
                session.add(
                    PrincipalRoleModel(
                        principal_id=principal_id,
                        role_name=role_name,
                        assigned_by=assigned_by,
                    ),
                )
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def revoke_role(self, principal_id: str, role_name: str) -> None:
        session = self._session_factory()
        try:
            row = (
                session.query(PrincipalRoleModel)
                .filter_by(principal_id=principal_id, role_name=role_name)
                .first()
            )
            if row:
                session.delete(row)
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def set_enabled(self, principal_id: str, enabled: bool) -> None:
        session = self._session_factory()
        try:
            principal = session.query(PrincipalModel).filter_by(id=principal_id).first()
            if principal:
                principal.enabled = enabled
                principal.updated_at = datetime.now(timezone.utc)
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_last_seen(self, principal_id: str) -> None:
        session = self._session_factory()
        try:
            principal = session.query(PrincipalModel).filter_by(id=principal_id).first()
            if principal:
                principal.last_seen_at = datetime.now(timezone.utc)
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_metadata(
        self,
        principal_id: str,
        display_name: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        session = self._session_factory()
        try:
            principal = session.query(PrincipalModel).filter_by(id=principal_id).first()
            if principal:
                if display_name is not None:
                    principal.display_name = display_name
                if metadata is not None:
                    principal.metadata_ = metadata
                principal.updated_at = datetime.now(timezone.utc)
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get(self, principal_id: str) -> PrincipalModel | None:
        session = self._session_factory()
        try:
            return session.query(PrincipalModel).filter_by(id=principal_id).first()
        finally:
            session.close()

    def delete(self, principal_id: str, force: bool = False) -> None:
        session = self._session_factory()
        try:
            principal = session.query(PrincipalModel).filter_by(id=principal_id).first()
            if not principal:
                raise ValueError(f"Principal '{principal_id}' not found")
            from flux.security.models import APIKeyModel

            has_keys = session.query(APIKeyModel).filter_by(principal_id=principal_id).first()
            if has_keys and not force:
                raise ValueError(
                    f"Principal '{principal_id}' has active API keys. Use force=True to delete.",
                )
            if has_keys:
                session.query(APIKeyModel).filter_by(principal_id=principal_id).delete(
                    synchronize_session=False,
                )
            session.delete(principal)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_all(self, type: str | None = None) -> list[PrincipalModel]:
        session = self._session_factory()
        try:
            query = session.query(PrincipalModel)
            if type:
                query = query.filter_by(type=type)
            return query.all()
        finally:
            session.close()
