from __future__ import annotations

import pytest

from flux.service_store import ServiceNotFoundError, ServiceStore


@pytest.fixture(autouse=True)
def _reset_singletons():
    from flux.config import Configuration
    from flux.models import DatabaseRepository

    Configuration._instance = None
    Configuration._config = None
    DatabaseRepository._engines.clear()
    yield
    Configuration._instance = None
    Configuration._config = None
    DatabaseRepository._engines.clear()


@pytest.fixture
def store(tmp_path, monkeypatch):
    from flux.models import Base, DatabaseRepository

    DatabaseRepository._engines.clear()
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("FLUX_DATABASE_URL", db_url)
    from flux.config import Configuration

    Configuration._instance = None
    from flux.models import RepositoryFactory

    repo = RepositoryFactory.create_repository()
    Base.metadata.create_all(repo._engine)
    return ServiceStore()


class TestServiceStoreCreate:
    def test_with_namespace(self, store):
        info = store.create("svc1", namespaces=["billing"])
        assert info.name == "svc1"
        assert info.namespaces == ["billing"]
        assert info.id

    def test_with_workflows(self, store):
        info = store.create("svc1", workflows=["wf1", "wf2"])
        assert info.workflows == ["wf1", "wf2"]

    def test_with_exclusions(self, store):
        info = store.create("svc1", exclusions=["wf_exclude"])
        assert info.exclusions == ["wf_exclude"]

    def test_empty(self, store):
        info = store.create("svc1")
        assert info.namespaces == []
        assert info.workflows == []
        assert info.exclusions == []
        assert info.created_at is not None
        assert info.updated_at is not None

    def test_duplicate_raises(self, store):
        store.create("svc1")
        with pytest.raises(ValueError, match="already exists"):
            store.create("svc1")


class TestServiceStoreGet:
    def test_existing(self, store):
        created = store.create("svc1", namespaces=["ns1"])
        fetched = store.get("svc1")
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.namespaces == ["ns1"]

    def test_missing_returns_none(self, store):
        assert store.get("nonexistent") is None


class TestServiceStoreList:
    def test_empty(self, store):
        assert store.list() == []

    def test_multiple(self, store):
        store.create("alpha")
        store.create("beta")
        result = store.list()
        assert len(result) == 2
        assert result[0].name == "alpha"
        assert result[1].name == "beta"


class TestServiceStoreUpdate:
    def test_add_namespace(self, store):
        store.create("svc1", namespaces=["ns1"])
        updated = store.update("svc1", add_namespaces=["ns2"])
        assert "ns1" in updated.namespaces
        assert "ns2" in updated.namespaces

    def test_add_workflow(self, store):
        store.create("svc1", workflows=["wf1"])
        updated = store.update("svc1", add_workflows=["wf2"])
        assert updated.workflows == ["wf1", "wf2"]

    def test_add_exclusion(self, store):
        store.create("svc1")
        updated = store.update("svc1", add_exclusions=["ex1"])
        assert updated.exclusions == ["ex1"]

    def test_remove_namespace(self, store):
        store.create("svc1", namespaces=["ns1", "ns2"])
        updated = store.update("svc1", remove_namespaces=["ns1"])
        assert updated.namespaces == ["ns2"]

    def test_remove_workflow(self, store):
        store.create("svc1", workflows=["wf1", "wf2"])
        updated = store.update("svc1", remove_workflows=["wf1"])
        assert updated.workflows == ["wf2"]

    def test_remove_exclusion(self, store):
        store.create("svc1", exclusions=["ex1", "ex2"])
        updated = store.update("svc1", remove_exclusions=["ex2"])
        assert updated.exclusions == ["ex1"]

    def test_missing_raises(self, store):
        with pytest.raises(ServiceNotFoundError):
            store.update("nonexistent", add_namespaces=["ns1"])


class TestServiceStoreUpdateEdgeCases:
    def test_add_duplicate_namespace_is_idempotent(self, store):
        store.create("svc1", namespaces=["ns1"])
        updated = store.update("svc1", add_namespaces=["ns1"])
        assert updated.namespaces == ["ns1"]

    def test_add_duplicate_workflow_is_idempotent(self, store):
        store.create("svc1", workflows=["billing/invoice"])
        updated = store.update("svc1", add_workflows=["billing/invoice"])
        assert updated.workflows == ["billing/invoice"]

    def test_remove_nonexistent_namespace_is_noop(self, store):
        store.create("svc1", namespaces=["ns1"])
        updated = store.update("svc1", remove_namespaces=["ns_gone"])
        assert updated.namespaces == ["ns1"]

    def test_remove_nonexistent_workflow_is_noop(self, store):
        store.create("svc1", workflows=["wf1"])
        updated = store.update("svc1", remove_workflows=["wf_gone"])
        assert updated.workflows == ["wf1"]

    def test_add_and_remove_in_same_call(self, store):
        store.create("svc1", namespaces=["ns1", "ns2"])
        updated = store.update("svc1", add_namespaces=["ns3"], remove_namespaces=["ns1"])
        assert "ns1" not in updated.namespaces
        assert "ns2" in updated.namespaces
        assert "ns3" in updated.namespaces


class TestServiceStoreMCP:
    def test_create_mcp_enabled(self, store):
        info = store.create("svc1", namespaces=["ns1"], mcp_enabled=True)
        assert info.mcp_enabled is True

    def test_create_default_mcp_disabled(self, store):
        info = store.create("svc1")
        assert info.mcp_enabled is False

    def test_update_enable_mcp(self, store):
        store.create("svc1")
        updated = store.update("svc1", mcp_enabled=True)
        assert updated.mcp_enabled is True

    def test_update_disable_mcp(self, store):
        store.create("svc1", mcp_enabled=True)
        updated = store.update("svc1", mcp_enabled=False)
        assert updated.mcp_enabled is False

    def test_update_mcp_none_preserves(self, store):
        store.create("svc1", mcp_enabled=True)
        updated = store.update("svc1", add_namespaces=["ns2"])
        assert updated.mcp_enabled is True


class TestServiceStoreDelete:
    def test_existing(self, store):
        store.create("svc1")
        store.delete("svc1")
        assert store.get("svc1") is None

    def test_missing_raises(self, store):
        with pytest.raises(ServiceNotFoundError):
            store.delete("nonexistent")
