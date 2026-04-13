from __future__ import annotations

import pytest

from flux.catalogs import WorkflowInfo
from flux.service_resolver import CollisionError, ServiceResolver, WorkflowNotInServiceError
from flux.service_store import ServiceInfo, ServiceNotFoundError


class FakeCatalog:
    def __init__(self, workflows: list[WorkflowInfo]):
        self._workflows = workflows

    def all(self, namespace=None):
        if namespace is None:
            return self._workflows
        return [w for w in self._workflows if w.namespace == namespace]

    def get(self, namespace, name, version=None):
        for w in self._workflows:
            if w.namespace == namespace and w.name == name:
                return w
        return None


class FakeStore:
    def __init__(self, services: dict[str, ServiceInfo]):
        self._services = services

    def get(self, name):
        return self._services.get(name)


def _wf(namespace: str, name: str) -> WorkflowInfo:
    return WorkflowInfo(
        id=f"{namespace}-{name}",
        name=name,
        namespace=namespace,
        imports=[],
        source=b"",
    )


class TestServiceResolverResolve:
    def test_namespace_selector(self):
        wf1 = _wf("billing", "invoice")
        wf2 = _wf("billing", "receipt")
        catalog = FakeCatalog([wf1, wf2])
        store = FakeStore({"my-svc": ServiceInfo(id="1", name="my-svc", namespaces=["billing"])})
        resolver = ServiceResolver(catalog, store)

        result = resolver.resolve("my-svc")

        assert set(result.keys()) == {"invoice", "receipt"}
        assert result["invoice"] is wf1
        assert result["receipt"] is wf2

    def test_workflow_selector(self):
        wf1 = _wf("billing", "invoice")
        wf2 = _wf("billing", "receipt")
        catalog = FakeCatalog([wf1, wf2])
        store = FakeStore(
            {"my-svc": ServiceInfo(id="1", name="my-svc", workflows=["billing/invoice"])},
        )
        resolver = ServiceResolver(catalog, store)

        result = resolver.resolve("my-svc")

        assert list(result.keys()) == ["invoice"]
        assert result["invoice"] is wf1

    def test_exclusion(self):
        wf1 = _wf("billing", "invoice")
        wf2 = _wf("billing", "receipt")
        catalog = FakeCatalog([wf1, wf2])
        store = FakeStore(
            {
                "my-svc": ServiceInfo(
                    id="1",
                    name="my-svc",
                    namespaces=["billing"],
                    exclusions=["billing/receipt"],
                ),
            },
        )
        resolver = ServiceResolver(catalog, store)

        result = resolver.resolve("my-svc")

        assert list(result.keys()) == ["invoice"]

    def test_collision_raises(self):
        wf1 = _wf("billing", "process")
        wf2 = _wf("shipping", "process")
        catalog = FakeCatalog([wf1, wf2])
        store = FakeStore(
            {"my-svc": ServiceInfo(id="1", name="my-svc", namespaces=["billing", "shipping"])},
        )
        resolver = ServiceResolver(catalog, store)

        with pytest.raises(CollisionError) as exc_info:
            resolver.resolve("my-svc")

        assert exc_info.value.service_name == "my-svc"
        assert "process" in exc_info.value.collisions
        assert "billing" in exc_info.value.collisions["process"]
        assert "shipping" in exc_info.value.collisions["process"]

    def test_exclusion_resolves_collision(self):
        wf1 = _wf("billing", "process")
        wf2 = _wf("shipping", "process")
        catalog = FakeCatalog([wf1, wf2])
        store = FakeStore(
            {
                "my-svc": ServiceInfo(
                    id="1",
                    name="my-svc",
                    namespaces=["billing", "shipping"],
                    exclusions=["shipping/process"],
                ),
            },
        )
        resolver = ServiceResolver(catalog, store)

        result = resolver.resolve("my-svc")

        assert result["process"] is wf1

    def test_empty_service(self):
        catalog = FakeCatalog([])
        store = FakeStore({"empty": ServiceInfo(id="1", name="empty")})
        resolver = ServiceResolver(catalog, store)

        result = resolver.resolve("empty")

        assert result == {}

    def test_service_not_found(self):
        catalog = FakeCatalog([])
        store = FakeStore({})
        resolver = ServiceResolver(catalog, store)

        with pytest.raises(ServiceNotFoundError):
            resolver.resolve("missing")

    def test_mixed_namespace_and_workflow_selectors(self):
        wf1 = _wf("billing", "invoice")
        wf2 = _wf("shipping", "track")
        catalog = FakeCatalog([wf1, wf2])
        store = FakeStore(
            {
                "my-svc": ServiceInfo(
                    id="1",
                    name="my-svc",
                    namespaces=["billing"],
                    workflows=["shipping/track"],
                ),
            },
        )
        resolver = ServiceResolver(catalog, store)

        result = resolver.resolve("my-svc")

        assert set(result.keys()) == {"invoice", "track"}
        assert result["invoice"] is wf1
        assert result["track"] is wf2


class TestServiceResolverFind:
    def test_find_existing(self):
        wf1 = _wf("billing", "invoice")
        catalog = FakeCatalog([wf1])
        store = FakeStore({"my-svc": ServiceInfo(id="1", name="my-svc", namespaces=["billing"])})
        resolver = ServiceResolver(catalog, store)

        result = resolver.find("my-svc", "invoice")

        assert result is wf1

    def test_find_missing_raises(self):
        wf1 = _wf("billing", "invoice")
        catalog = FakeCatalog([wf1])
        store = FakeStore({"my-svc": ServiceInfo(id="1", name="my-svc", namespaces=["billing"])})
        resolver = ServiceResolver(catalog, store)

        with pytest.raises(WorkflowNotInServiceError) as exc_info:
            resolver.find("my-svc", "nonexistent")

        assert exc_info.value.service_name == "my-svc"
        assert exc_info.value.endpoint_name == "nonexistent"
