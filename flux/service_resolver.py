from __future__ import annotations

from flux.catalogs import WorkflowCatalog, WorkflowInfo, resolve_workflow_ref
from flux.errors import WorkflowNotFoundError
from flux.service_store import ServiceNotFoundError, ServiceStore


class CollisionError(Exception):
    def __init__(self, service_name: str, collisions: dict[str, list[str]]):
        self.service_name = service_name
        self.collisions = collisions
        details = ", ".join(
            f"'{name}' exists in namespaces: {', '.join(nss)}" for name, nss in collisions.items()
        )
        super().__init__(f"Service '{service_name}' has name collisions: {details}")


class WorkflowNotInServiceError(Exception):
    def __init__(self, service_name: str, endpoint_name: str):
        self.service_name = service_name
        self.endpoint_name = endpoint_name
        super().__init__(f"Workflow '{endpoint_name}' not found in service '{service_name}'")


class ServiceResolver:
    def __init__(self, catalog: WorkflowCatalog, service_store: ServiceStore):
        self._catalog = catalog
        self._store = service_store

    def resolve(self, service_name: str) -> dict[str, WorkflowInfo]:
        """Returns {endpoint_name: WorkflowInfo}. Raises CollisionError if name collision detected."""
        service = self._store.get(service_name)
        if not service:
            raise ServiceNotFoundError(service_name)

        candidates: dict[str, list[WorkflowInfo]] = {}

        for ns in service.namespaces:
            for wf in self._catalog.all(namespace=ns):
                candidates.setdefault(wf.name, []).append(wf)

        for ref in service.workflows:
            ns, name = resolve_workflow_ref(ref)
            try:
                wf = self._catalog.get(ns, name)
            except WorkflowNotFoundError:
                continue
            if wf:
                existing = candidates.get(wf.name, [])
                if not any(e.namespace == wf.namespace for e in existing):
                    candidates.setdefault(wf.name, []).append(wf)

        for ref in service.exclusions:
            ns, name = resolve_workflow_ref(ref)
            if name in candidates:
                candidates[name] = [w for w in candidates[name] if w.namespace != ns]
                if not candidates[name]:
                    del candidates[name]

        collisions: dict[str, list[str]] = {}
        workflows: dict[str, WorkflowInfo] = {}
        for name, wfs in candidates.items():
            namespaces = list({w.namespace for w in wfs})
            if len(namespaces) > 1:
                collisions[name] = namespaces
            else:
                workflows[name] = wfs[0]

        if collisions:
            raise CollisionError(service_name, collisions)

        return workflows

    def find(self, service_name: str, endpoint_name: str) -> WorkflowInfo:
        endpoints = self.resolve(service_name)
        if endpoint_name not in endpoints:
            raise WorkflowNotInServiceError(service_name, endpoint_name)
        return endpoints[endpoint_name]
