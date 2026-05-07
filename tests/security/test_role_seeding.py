from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from flux.models import Base
from flux.security.auth_service import AuthService, BUILT_IN_ROLES
from flux.security.config import AuthConfig
from flux.security.models import RoleModel


def test_operator_role_includes_task_approve_permission():
    assert "workflow:*:*:task:*:approve" in BUILT_IN_ROLES["operator"], (
        f"Operator role missing task:approve permission. Has: {BUILT_IN_ROLES['operator']}"
    )


def test_admin_unchanged():
    assert BUILT_IN_ROLES["admin"] == ["*"]


def test_viewer_does_not_get_approve():
    for perm in BUILT_IN_ROLES["viewer"]:
        assert "approve" not in perm, f"Viewer should not have approve perm; has {perm}"


def test_seed_built_in_roles_is_idempotent_and_merges_new_perms(tmp_path):
    """Seeding should add new permissions to existing role rows, not skip them."""
    db_path = tmp_path / "seed_test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as s:
        s.add(RoleModel(name="operator", permissions=["workflow:*:*:read"], built_in=True))
        s.commit()

    auth = AuthService(
        config=AuthConfig(),
        session_factory=Session,
    )
    auth.seed_built_in_roles()

    with Session() as s:
        op = s.query(RoleModel).filter_by(name="operator").first()
        assert op is not None
        assert "workflow:*:*:read" in op.permissions
        for required in BUILT_IN_ROLES["operator"]:
            assert required in op.permissions, f"Missing seeded permission: {required}"


def test_seed_built_in_roles_creates_missing_roles(tmp_path):
    """Seeding on a fresh DB should create all built-in roles."""
    db_path = tmp_path / "seed_fresh.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    auth = AuthService(
        config=AuthConfig(),
        session_factory=Session,
    )
    auth.seed_built_in_roles()

    with Session() as s:
        for role_name, expected_perms in BUILT_IN_ROLES.items():
            row = s.query(RoleModel).filter_by(name=role_name).first()
            assert row is not None, f"Built-in role {role_name} not seeded"
            for perm in expected_perms:
                assert perm in row.permissions


def test_seed_built_in_roles_running_twice_is_safe(tmp_path):
    """Running seed twice should not duplicate or change anything."""
    db_path = tmp_path / "seed_twice.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    auth = AuthService(
        config=AuthConfig(),
        session_factory=Session,
    )
    auth.seed_built_in_roles()
    auth.seed_built_in_roles()

    with Session() as s:
        rows = s.query(RoleModel).filter_by(name="operator").all()
        assert len(rows) == 1
