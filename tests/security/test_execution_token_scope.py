"""An execution token carries the triggering principal's identity, but not
its full authority.

The token is handed to workflow code — stored on the execution row, forwarded
into the runner child, exposed as ``ctx.exec_token`` and
``FluxClient.for_current_execution()`` — so whatever it can reach, a workflow
author can reach. It needs to run and register workflows; it never needs the
admin, rbac, schedule, service, worker, config or secret families.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from flux.security.auth_service import AuthService, scope_to_execution
from flux.security.config import AuthConfig
from flux.security.identity import FluxIdentity
from flux.security.models import Base, RoleModel


class TestScopeToExecution:
    def test_blanket_grant_becomes_the_allowed_families(self):
        scoped = scope_to_execution(frozenset({"*"}))
        assert scoped == {"workflow:*", "execution:*"}
        # The point: admin is no longer reachable.
        assert not FluxIdentity(subject="s").has_permission("admin:secrets:read", scoped)
        assert not FluxIdentity(subject="s").has_permission("admin:principals:manage", scoped)

    def test_out_of_family_permissions_are_dropped(self):
        scoped = scope_to_execution(
            frozenset(
                {
                    "workflow:*:*:register",
                    "workflow:*:*:run",
                    "execution:*:read",
                    "schedule:*",
                    "config:*:manage",
                    "admin:workers:manage",
                    "service:*:manage",
                    "worker:*:*",
                },
            ),
        )
        assert scoped == {"workflow:*:*:register", "workflow:*:*:run", "execution:*:read"}

    def test_wildcard_resource_is_narrowed_not_dropped(self):
        assert scope_to_execution(frozenset({"*:*:read"})) == {
            "workflow:*:read",
            "execution:*:read",
        }

    def test_scoped_permissions_still_authorize_the_real_calls(self):
        """call_workflow and register_dynamic_workflow must keep working."""
        identity = FluxIdentity(subject="s")
        scoped = scope_to_execution(frozenset({"*"}))
        assert identity.has_permission("workflow:default:report:run", scoped)
        assert identity.has_permission("workflow:dyn-abc:generated:register", scoped)
        assert identity.has_permission("workflow:default:report:task:load:execute", scoped)


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'exec_scope.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _auth(session_factory) -> AuthService:
    return AuthService(config=AuthConfig(), session_factory=session_factory)


def _identity(roles: set[str], *, execution: bool) -> FluxIdentity:
    metadata = {"token_type": "execution", "exec_id": "e1"} if execution else {}
    return FluxIdentity(subject="svc", roles=frozenset(roles), metadata=metadata)


async def test_execution_token_cannot_reach_admin(session_factory):
    with session_factory() as s:
        s.add(RoleModel(name="privileged", permissions=["*"]))
        s.commit()

    auth = _auth(session_factory)
    perms = await auth.resolve_permissions(_identity({"privileged"}, execution=True))

    identity = FluxIdentity(subject="svc")
    assert not identity.has_permission("admin:secrets:read", perms)
    assert not identity.has_permission("schedule:*:manage", perms)
    assert identity.has_permission("workflow:default:report:run", perms)


async def test_ordinary_identity_is_unchanged(session_factory):
    with session_factory() as s:
        s.add(RoleModel(name="privileged", permissions=["*"]))
        s.commit()

    auth = _auth(session_factory)
    perms = await auth.resolve_permissions(_identity({"privileged"}, execution=False))
    assert FluxIdentity(subject="svc").has_permission("admin:secrets:read", perms)


async def test_scoping_does_not_poison_the_shared_cache(session_factory):
    """resolve_permissions caches on the principal, which an execution token
    shares with that principal's other credentials — so the narrowed set must
    never be what gets cached."""
    with session_factory() as s:
        s.add(RoleModel(name="privileged", permissions=["*"]))
        s.commit()

    auth = _auth(session_factory)
    scoped = await auth.resolve_permissions(_identity({"privileged"}, execution=True))
    full = await auth.resolve_permissions(_identity({"privileged"}, execution=False))

    identity = FluxIdentity(subject="svc")
    assert not identity.has_permission("admin:secrets:read", scoped)
    assert identity.has_permission("admin:secrets:read", full)
