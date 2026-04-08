from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Callable

from flux.security.config import AuthConfig
from flux.security.errors import AuthenticationError
from flux.security.identity import FluxIdentity, ANONYMOUS
from flux.security.models import RoleModel, ServiceAccountModel, APIKeyModel
from flux.security.providers import AuthProvider
from flux.security.providers.oidc import OIDCProvider
from flux.security.providers.api_key import APIKeyProvider
from flux.utils import get_logger

logger = get_logger(__name__)

BUILT_IN_ROLES = {
    "admin": ["*"],
    "operator": [
        "workflow:*:run",
        "workflow:*:read",
        "workflow:*:register",
        "workflow:*:task:*:execute",
        "schedule:*",
        "execution:*",
    ],
    "viewer": [
        "workflow:*:read",
        "execution:*:read",
        "schedule:*:read",
    ],
}


class AuthorizationResult:
    def __init__(self, ok: bool, missing_permissions: list[str] | None = None):
        self.ok = ok
        self.missing_permissions = missing_permissions or []


class AuthService:
    def __init__(self, config: AuthConfig, session_factory: Callable):
        self._config = config
        self._session_factory = session_factory
        self._providers: list[AuthProvider] = []

        if config.oidc.enabled:
            self._providers.append(OIDCProvider(config.oidc))
        if config.api_keys.enabled:
            self._providers.append(APIKeyProvider(session_factory))

    async def authenticate(self, token: str | None) -> FluxIdentity:
        if not self._config.enabled:
            logger.warning("Auth disabled — request treated as admin (anonymous)")
            return ANONYMOUS

        if not token:
            raise AuthenticationError("Authorization token required")

        for provider in self._providers:
            identity = await provider.authenticate(token)
            if identity is not None:
                return identity

        raise AuthenticationError("Invalid or expired token")

    async def resolve_permissions(self, identity: FluxIdentity) -> set[str]:
        session = self._session_factory()
        try:
            all_permissions: set[str] = set()
            for role_name in identity.roles:
                role = session.query(RoleModel).filter_by(name=role_name).first()
                if role:
                    all_permissions.update(role.permissions)
            return all_permissions
        finally:
            session.close()

    async def is_authorized(self, identity: FluxIdentity, required: str) -> bool:
        permissions = await self.resolve_permissions(identity)
        return identity.has_permission(required, permissions)

    async def authorize(
        self,
        identity: FluxIdentity,
        workflow_name: str,
        workflow_metadata: dict,
    ) -> AuthorizationResult:
        permissions = await self.resolve_permissions(identity)
        required_perms = self._collect_required_permissions(workflow_name, workflow_metadata)
        missing = [p for p in required_perms if not identity.has_permission(p, permissions)]
        if missing:
            return AuthorizationResult(ok=False, missing_permissions=missing)
        return AuthorizationResult(ok=True)

    def _collect_required_permissions(
        self,
        workflow_name: str,
        workflow_metadata: dict,
    ) -> list[str]:
        perms = [f"workflow:{workflow_name}:run"]
        task_names = workflow_metadata.get("task_names", [])
        for task_name in task_names:
            perms.append(f"workflow:{workflow_name}:task:{task_name}:execute")
        nested_workflows = workflow_metadata.get("nested_workflows", [])
        for nested_name in nested_workflows:
            nested_meta = self._get_workflow_metadata(nested_name)
            if nested_meta:
                perms.extend(self._collect_required_permissions(nested_name, nested_meta))
        return perms

    def _get_workflow_metadata(self, workflow_name: str) -> dict | None:
        from flux.catalogs import WorkflowCatalog

        try:
            catalog = WorkflowCatalog.create()
            workflow = catalog.get(workflow_name)
            return workflow.metadata if hasattr(workflow, "metadata") and workflow.metadata else {}
        except Exception:
            return None

    async def list_roles(self) -> list[RoleModel]:
        session = self._session_factory()
        try:
            return session.query(RoleModel).all()
        finally:
            session.close()

    async def get_role(self, name: str) -> RoleModel | None:
        session = self._session_factory()
        try:
            return session.query(RoleModel).filter_by(name=name).first()
        finally:
            session.close()

    async def create_role(self, name: str, permissions: list[str]) -> RoleModel:
        session = self._session_factory()
        try:
            existing = session.query(RoleModel).filter_by(name=name).first()
            if existing:
                raise ValueError(f"Role '{name}' already exists")
            role = RoleModel(name=name, permissions=permissions)
            session.add(role)
            session.commit()
            session.refresh(role)
            return role
        finally:
            session.close()

    async def clone_role(self, source_name: str, new_name: str) -> RoleModel:
        source = await self.get_role(source_name)
        if not source:
            raise ValueError(f"Role '{source_name}' not found")
        return await self.create_role(new_name, list(source.permissions))

    async def update_role(
        self,
        name: str,
        add_permissions: list[str] | None = None,
        remove_permissions: list[str] | None = None,
    ) -> RoleModel:
        session = self._session_factory()
        try:
            role = session.query(RoleModel).filter_by(name=name).first()
            if not role:
                raise ValueError(f"Role '{name}' not found")
            if role.built_in:
                raise ValueError(f"Cannot modify built-in role '{name}'")
            perms = set(role.permissions)
            if add_permissions:
                perms.update(add_permissions)
            if remove_permissions:
                perms -= set(remove_permissions)
            role.permissions = list(perms)
            role.updated_at = datetime.now()
            session.commit()
            session.refresh(role)
            return role
        finally:
            session.close()

    async def delete_role(self, name: str) -> None:
        session = self._session_factory()
        try:
            role = session.query(RoleModel).filter_by(name=name).first()
            if not role:
                raise ValueError(f"Role '{name}' not found")
            if role.built_in:
                raise ValueError(f"Cannot delete built-in role '{name}'")
            referencing = session.query(ServiceAccountModel).all()
            refs = [sa.name for sa in referencing if name in sa.roles]
            if refs:
                raise ValueError(
                    f"Cannot delete role '{name}': referenced by service accounts: {', '.join(refs)}",
                )
            session.delete(role)
            session.commit()
        finally:
            session.close()

    async def list_service_accounts(self) -> list[ServiceAccountModel]:
        session = self._session_factory()
        try:
            return session.query(ServiceAccountModel).all()
        finally:
            session.close()

    async def get_service_account(self, name: str) -> ServiceAccountModel | None:
        session = self._session_factory()
        try:
            return session.query(ServiceAccountModel).filter_by(name=name).first()
        finally:
            session.close()

    async def create_service_account(self, name: str, roles: list[str]) -> ServiceAccountModel:
        session = self._session_factory()
        try:
            existing = session.query(ServiceAccountModel).filter_by(name=name).first()
            if existing:
                raise ValueError(f"Service account '{name}' already exists")
            sa = ServiceAccountModel(name=name, roles=roles)
            session.add(sa)
            session.commit()
            session.refresh(sa)
            return sa
        finally:
            session.close()

    async def update_service_account(
        self,
        name: str,
        add_roles: list[str] | None = None,
        remove_roles: list[str] | None = None,
    ) -> ServiceAccountModel:
        session = self._session_factory()
        try:
            sa = session.query(ServiceAccountModel).filter_by(name=name).first()
            if not sa:
                raise ValueError(f"Service account '{name}' not found")
            roles = set(sa.roles)
            if add_roles:
                roles.update(add_roles)
            if remove_roles:
                roles -= set(remove_roles)
            sa.roles = list(roles)
            sa.updated_at = datetime.now()
            session.commit()
            session.refresh(sa)
            return sa
        finally:
            session.close()

    async def delete_service_account(self, name: str) -> None:
        session = self._session_factory()
        try:
            sa = session.query(ServiceAccountModel).filter_by(name=name).first()
            if not sa:
                raise ValueError(f"Service account '{name}' not found")
            session.delete(sa)
            session.commit()
        finally:
            session.close()

    async def create_api_key(
        self,
        account_name: str,
        key_name: str,
        expires: timedelta | None = None,
    ) -> str:
        session = self._session_factory()
        try:
            sa = session.query(ServiceAccountModel).filter_by(name=account_name).first()
            if not sa:
                raise ValueError(f"Service account '{account_name}' not found")
            key_plaintext = f"flux_sk_{secrets.token_hex(24)}"
            key_hash = hashlib.sha256(key_plaintext.encode()).hexdigest()
            key_prefix = key_plaintext[:12]
            expires_at = (datetime.now() + expires) if expires else None
            key_model = APIKeyModel(
                service_account_id=sa.id,
                name=key_name,
                key_hash=key_hash,
                key_prefix=key_prefix,
                expires_at=expires_at,
            )
            session.add(key_model)
            session.commit()
            return key_plaintext
        finally:
            session.close()

    async def revoke_api_key(self, account_name: str, key_name: str) -> None:
        session = self._session_factory()
        try:
            sa = session.query(ServiceAccountModel).filter_by(name=account_name).first()
            if not sa:
                raise ValueError(f"Service account '{account_name}' not found")
            key = (
                session.query(APIKeyModel)
                .filter_by(service_account_id=sa.id, name=key_name)
                .first()
            )
            if not key:
                raise ValueError(f"API key '{key_name}' not found")
            session.delete(key)
            session.commit()
        finally:
            session.close()

    async def list_api_keys(self, account_name: str) -> list[APIKeyModel]:
        session = self._session_factory()
        try:
            sa = session.query(ServiceAccountModel).filter_by(name=account_name).first()
            if not sa:
                raise ValueError(f"Service account '{account_name}' not found")
            return session.query(APIKeyModel).filter_by(service_account_id=sa.id).all()
        finally:
            session.close()

    def seed_built_in_roles(self) -> None:
        session = self._session_factory()
        try:
            for name, permissions in BUILT_IN_ROLES.items():
                existing = session.query(RoleModel).filter_by(name=name).first()
                if not existing:
                    session.add(RoleModel(name=name, permissions=permissions, built_in=True))
            session.commit()
        finally:
            session.close()
