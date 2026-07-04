from __future__ import annotations

import hashlib
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from collections.abc import Callable

from flux.security.config import AuthConfig
from flux.security.errors import AuthenticationError
from flux.security.identity import FluxIdentity, ANONYMOUS
from flux.security.models import RoleModel, APIKeyModel
from flux.security.principals import PrincipalRegistry
from flux.security.providers import AuthProvider
from flux.security.providers.oidc import OIDCProvider
from flux.security.providers.api_key import APIKeyProvider
from flux.security.execution_token import ExecutionTokenProvider
from flux.utils import get_logger

logger = get_logger(__name__)

PERMISSION_PATTERN = re.compile(r"^[a-zA-Z0-9_*\-{}]+(:[a-zA-Z0-9_*\-{}]+)*$")

BUILT_IN_ROLES = {
    "admin": ["*"],
    "operator": [
        "workflow:*:*:run",
        "workflow:*:*:read",
        "workflow:*:*:register",
        "workflow:*:*:task:*:execute",
        "workflow:*:*:task:*:approve",
        "schedule:*",
        "execution:*",
        "agent:*:*",
        "config:*:read",
        "config:*:manage",
        "service:*:read",
        "service:*:manage",
    ],
    "viewer": [
        "workflow:*:*:read",
        "execution:*:read",
        "schedule:*:read",
        "agent:*:read",
        "config:*:read",
        "service:*:read",
    ],
    "worker": [
        "worker:*:*",
        "config:*:read",
        "execution:*:read",
    ],
}


class AuthorizationResult:
    def __init__(self, ok: bool, missing_permissions: list[str] | None = None):
        self.ok = ok
        self.missing_permissions = missing_permissions or []


class _TTLCache:
    """Tiny per-process TTL cache for auth resolution results.

    Bounded: when full, expired entries are swept; if still full the oldest
    insertion is dropped. No background task — expiry is checked on read.
    """

    def __init__(self, max_size: int = 4096):
        self._data: dict[str, tuple[float, object]] = {}
        self._max_size = max_size

    def get(self, key: str) -> object | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            self._data.pop(key, None)
            return None
        return value

    def put(self, key: str, value: object, ttl: float) -> None:
        if len(self._data) >= self._max_size:
            now = time.monotonic()
            for stale in [k for k, (exp, _) in self._data.items() if exp <= now]:
                self._data.pop(stale, None)
            while len(self._data) >= self._max_size:
                self._data.pop(next(iter(self._data)), None)
        self._data[key] = (time.monotonic() + ttl, value)

    def clear(self) -> None:
        self._data.clear()


class AuthService:
    def __init__(
        self,
        config: AuthConfig,
        session_factory: Callable,
        registry: PrincipalRegistry | None = None,
    ):
        self._config = config
        self._session_factory = session_factory
        self._registry = registry
        # Per-process TTL caches for the hot per-request path (token →
        # identity, principal → permissions). Local mutations invalidate
        # immediately; other replicas converge within the TTL.
        self._resolution_cache_ttl = getattr(config, "resolution_cache_ttl", 0) or 0
        self._identity_cache = _TTLCache()
        self._permission_cache = _TTLCache()
        self._providers: list[AuthProvider] = []

        self._providers.append(ExecutionTokenProvider(registry=registry))

        if config.oidc.enabled:
            self._providers.append(OIDCProvider(config.oidc, registry=registry))
        if config.api_keys.enabled:
            self._providers.append(APIKeyProvider(session_factory, registry=registry))

    @property
    def principal_registry(self) -> PrincipalRegistry | None:
        return self._registry

    async def authenticate(self, token: str | None) -> FluxIdentity:
        if not self._config.enabled:
            logger.warning("Auth disabled — request treated as admin (anonymous)")
            return ANONYMOUS

        if not token:
            raise AuthenticationError("Authorization token required")

        cache_key = None
        if self._resolution_cache_ttl > 0:
            # Never keep raw tokens as keys.
            cache_key = hashlib.sha256(token.encode()).hexdigest()
            cached = self._identity_cache.get(cache_key)
            if cached is not None:
                return cached  # type: ignore[return-value]

        for provider in self._providers:
            try:
                identity = await provider.authenticate(token)
                if identity is not None:
                    if cache_key is not None:
                        self._identity_cache.put(
                            cache_key,
                            identity,
                            self._resolution_cache_ttl,
                        )
                    return identity
            except Exception as e:
                logger.error(f"Provider {type(provider).__name__} error: {e}")
                continue

        raise AuthenticationError("Invalid or expired token")

    def _permission_cache_key(self, identity: FluxIdentity) -> str:
        principal_id = identity.metadata.get("principal_id")
        if principal_id:
            return f"principal:{principal_id}"
        provider = identity.metadata.get("provider", "")
        return f"identity:{provider}:{identity.subject}:{','.join(sorted(identity.roles))}"

    async def resolve_permissions(self, identity: FluxIdentity) -> frozenset[str]:
        cache_key = None
        if self._resolution_cache_ttl > 0:
            cache_key = self._permission_cache_key(identity)
            cached = self._permission_cache.get(cache_key)
            if cached is not None:
                return cached  # type: ignore[return-value]

        session = self._session_factory()
        try:
            role_names: list[str] = []
            if self._registry and identity.metadata.get("principal_id"):
                role_names = self._registry.get_roles(identity.metadata["principal_id"])
            else:
                role_names = list(identity.roles)

            all_permissions: set[str] = set()
            for role_name in role_names:
                role = session.query(RoleModel).filter_by(name=role_name).first()
                if role:
                    all_permissions.update(role.permissions)
                elif role_name in BUILT_IN_ROLES:
                    all_permissions.update(BUILT_IN_ROLES[role_name])
            # Immutable: the cached value is shared across requests, so a
            # caller mutating its result must not poison later authorization
            # decisions.
            resolved = frozenset(all_permissions)
            if cache_key is not None:
                self._permission_cache.put(
                    cache_key,
                    resolved,
                    self._resolution_cache_ttl,
                )
            return resolved
        finally:
            session.close()

    def invalidate_resolution_caches(self) -> None:
        """Drop cached identities and permission sets on this replica.

        Called after mutations that change authorization outcomes — role
        definitions and assignments, principal enable/disable/delete, and
        key revocation — so those take effect locally at once; other
        replicas converge within the cache TTL. Additive mutations that
        cannot invalidate an existing cached result (creating a principal,
        minting a new key, principal metadata edits) do not clear it.
        """
        self._identity_cache.clear()
        self._permission_cache.clear()

    async def is_authorized(self, identity: FluxIdentity, required: str) -> bool:
        permissions = await self.resolve_permissions(identity)
        return identity.has_permission(required, permissions)

    async def authorize(
        self,
        identity: FluxIdentity,
        namespace: str,
        workflow_name: str,
        workflow_metadata: dict,
    ) -> AuthorizationResult:
        from flux.catalogs import WorkflowCatalog

        catalog = WorkflowCatalog.create()
        permissions = await self.resolve_permissions(identity)
        required_perms = self._collect_required_permissions(
            namespace=namespace,
            workflow_name=workflow_name,
            workflow_metadata=workflow_metadata,
            catalog=catalog,
        )
        missing = [p for p in required_perms if not identity.has_permission(p, permissions)]
        if missing:
            return AuthorizationResult(ok=False, missing_permissions=missing)
        return AuthorizationResult(ok=True)

    def _collect_required_permissions(
        self,
        namespace: str,
        workflow_name: str,
        workflow_metadata: dict,
        _visited: set[tuple[str, str]] | None = None,
        catalog=None,
    ) -> list[str]:
        if _visited is None:
            _visited = set()
        key = (namespace, workflow_name)
        if key in _visited:
            return []
        _visited.add(key)
        perms = [f"workflow:{namespace}:{workflow_name}:run"]
        task_names = workflow_metadata.get("task_names", [])
        for task_name in task_names:
            perms.append(
                f"workflow:{namespace}:{workflow_name}:task:{task_name}:execute",
            )
        nested_workflows = workflow_metadata.get("nested_workflows", [])
        for nested in nested_workflows:
            nested_ns, nested_name = nested[0], nested[1]
            nested_meta = self._get_workflow_metadata(
                nested_ns,
                nested_name,
                catalog=catalog,
            )
            if nested_meta is not None:
                perms.extend(
                    self._collect_required_permissions(
                        namespace=nested_ns,
                        workflow_name=nested_name,
                        workflow_metadata=nested_meta,
                        _visited=_visited,
                        catalog=catalog,
                    ),
                )
            else:
                perms.append(f"workflow:{nested_ns}:{nested_name}:run")
        return perms

    def _get_workflow_metadata(
        self,
        namespace: str,
        workflow_name: str,
        catalog=None,
    ) -> dict | None:
        if catalog is None:
            from flux.catalogs import WorkflowCatalog

            catalog = WorkflowCatalog.create()
        try:
            workflow = catalog.get(namespace, workflow_name)
            return getattr(workflow, "metadata", None) or {}
        except Exception as e:
            logger.warning(
                f"Failed to get metadata for workflow '{namespace}/{workflow_name}': {e}",
            )
            return None

    def _validate_permissions(self, permissions: list[str]) -> None:
        for perm in permissions:
            if not PERMISSION_PATTERN.match(perm):
                raise ValueError(f"Invalid permission format: '{perm}'")

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
        self._validate_permissions(permissions)
        session = self._session_factory()
        try:
            existing = session.query(RoleModel).filter_by(name=name).first()
            if existing:
                raise ValueError(f"Role '{name}' already exists")
            role = RoleModel(name=name, permissions=permissions)
            session.add(role)
            session.commit()
            session.refresh(role)
            self.invalidate_resolution_caches()
            return role
        except Exception:
            session.rollback()
            raise
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
        if add_permissions:
            self._validate_permissions(add_permissions)
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
            role.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(role)
            self.invalidate_resolution_caches()
            return role
        except Exception:
            session.rollback()
            raise
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
            session.delete(role)
            session.commit()
            self.invalidate_resolution_caches()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    async def list_principals(self) -> list:
        if not self._registry:
            return []
        return self._registry.list_all()

    async def get_principal(self, principal_id: str):
        if not self._registry:
            return None
        return self._registry.get(principal_id)

    async def create_principal(
        self,
        type: str,
        subject: str,
        external_issuer: str,
        display_name: str | None = None,
        metadata: dict | None = None,
        roles: list[str] | None = None,
        enabled: bool = True,
    ):
        if not self._registry:
            raise RuntimeError("PrincipalRegistry not configured")
        principal = self._registry.create(
            type=type,
            subject=subject,
            external_issuer=external_issuer,
            display_name=display_name,
            metadata=metadata or {},
            enabled=enabled,
        )
        for role_name in roles or []:
            self._registry.assign_role(principal.id, role_name, assigned_by=None)
        return principal

    async def update_principal(
        self,
        principal_id: str,
        display_name: str | None = None,
        metadata: dict | None = None,
    ):
        if not self._registry:
            raise RuntimeError("PrincipalRegistry not configured")
        self._registry.update_metadata(principal_id, display_name=display_name, metadata=metadata)
        return self._registry.get(principal_id)

    async def delete_principal(self, principal_id: str, force: bool = False) -> None:
        if not self._registry:
            raise RuntimeError("PrincipalRegistry not configured")
        self._registry.delete(principal_id, force=force)
        self.invalidate_resolution_caches()

    async def enable_principal(self, principal_id: str) -> None:
        if not self._registry:
            raise RuntimeError("PrincipalRegistry not configured")
        self._registry.set_enabled(principal_id, True)
        self.invalidate_resolution_caches()

    async def disable_principal(self, principal_id: str) -> None:
        if not self._registry:
            raise RuntimeError("PrincipalRegistry not configured")
        self._registry.set_enabled(principal_id, False)
        self.invalidate_resolution_caches()

    async def grant_role(
        self,
        principal_id: str,
        role_name: str,
        granted_by: str | None = None,
    ) -> None:
        if not self._registry:
            raise RuntimeError("PrincipalRegistry not configured")
        self._registry.assign_role(principal_id, role_name, assigned_by=granted_by)
        self.invalidate_resolution_caches()

    async def revoke_role(self, principal_id: str, role_name: str) -> None:
        if not self._registry:
            raise RuntimeError("PrincipalRegistry not configured")
        self._registry.revoke_role(principal_id, role_name)
        self.invalidate_resolution_caches()

    async def create_api_key(
        self,
        principal_id: str,
        key_name: str,
        expires: timedelta | None = None,
    ) -> str:
        session = self._session_factory()
        try:
            existing = (
                session.query(APIKeyModel)
                .filter_by(principal_id=principal_id, name=key_name)
                .first()
            )
            if existing:
                raise ValueError(f"API key '{key_name}' already exists for this principal")
            key_plaintext = f"flux_sk_{secrets.token_hex(24)}"
            key_hash = hashlib.sha256(key_plaintext.encode()).hexdigest()
            key_prefix = key_plaintext[:12]
            expires_at = (datetime.now(timezone.utc) + expires) if expires else None
            key_model = APIKeyModel(
                principal_id=principal_id,
                name=key_name,
                key_hash=key_hash,
                key_prefix=key_prefix,
                expires_at=expires_at,
            )
            session.add(key_model)
            session.commit()
            return key_plaintext
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    async def list_api_keys(self, principal_id: str) -> list:
        session = self._session_factory()
        try:
            return session.query(APIKeyModel).filter_by(principal_id=principal_id).all()
        finally:
            session.close()

    async def revoke_api_key(self, principal_id: str, key_name: str) -> None:
        session = self._session_factory()
        try:
            key = (
                session.query(APIKeyModel)
                .filter_by(principal_id=principal_id, name=key_name)
                .first()
            )
            if not key:
                raise ValueError(f"API key '{key_name}' not found")
            session.delete(key)
            session.commit()
            self.invalidate_resolution_caches()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    async def revoke_all_api_keys(self, principal_id: str) -> int:
        session = self._session_factory()
        try:
            keys = session.query(APIKeyModel).filter_by(principal_id=principal_id).all()
            count = len(keys)
            for key in keys:
                session.delete(key)
            session.commit()
            self.invalidate_resolution_caches()
            return count
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def seed_built_in_roles(self) -> None:
        session = self._session_factory()
        try:
            for name, permissions in BUILT_IN_ROLES.items():
                existing = session.query(RoleModel).filter_by(name=name).first()
                if not existing:
                    session.add(RoleModel(name=name, permissions=permissions, built_in=True))
                else:
                    existing_set = set(existing.permissions or [])
                    desired_set = set(permissions)
                    extra = existing_set - desired_set
                    if extra:
                        logger.warning(
                            "Built-in role '%s' has permissions not in current "
                            "BUILT_IN_ROLES; these will not be removed automatically: %s",
                            name,
                            sorted(extra),
                        )
                    merged = existing_set | desired_set
                    if merged != existing_set:
                        existing.permissions = sorted(merged)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
