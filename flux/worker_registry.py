from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import TYPE_CHECKING
from uuid import uuid4

from flux.errors import WorkerNotFoundError

# Used for forward references
if TYPE_CHECKING:
    from flux.models import WorkerModel


class WorkerRuntimeInfo:
    def __init__(
        self,
        os_name: str,
        os_version: str,
        python_version: str,
    ):
        self.os_name = os_name
        self.os_version = os_version
        self.python_version = python_version


class WorkerResouceGPUInfo:
    def __init__(
        self,
        name: str,
        memory_total: int,
        memory_available: int,
    ):
        self.name = name
        self.memory_total = memory_total
        self.memory_available = memory_available


class WorkerResourcesInfo:
    def __init__(
        self,
        cpu_total: int,
        cpu_available: int,
        memory_total: int,
        memory_available: int,
        disk_total: int,
        disk_free: int,
        gpus: list[WorkerResouceGPUInfo],
    ):
        self.cpu_total = cpu_total
        self.cpu_available = cpu_available
        self.memory_total = memory_total
        self.memory_available = memory_available
        self.disk_total = disk_total
        self.disk_free = disk_free
        self.gpus = gpus


class WorkerInfo:
    def __init__(
        self,
        name: str,
        runtime: WorkerRuntimeInfo | None = None,
        packages: list[dict[str, str]] = [],
        resources: WorkerResourcesInfo | None = None,
        session_token: str | None = None,
    ):
        self.name = name
        self.runtime = runtime
        self.packages = packages
        self.resources = resources
        self.session_token = session_token


class WorkerRegistry(ABC):
    @abstractmethod
    def get(self, name: str) -> WorkerInfo:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def register(
        self,
        name: str,
        runtime: WorkerRuntimeInfo | None,
        packages: list[dict[str, str]],
        resources: WorkerResourcesInfo | None,
    ) -> WorkerInfo:  # pragma: no cover
        raise NotImplementedError()

    @staticmethod
    def create() -> WorkerRegistry:
        return SQLiteWorkerRegistry()


class SQLiteWorkerRegistry(WorkerRegistry):
    def __init__(self):
        # Import here to avoid circular imports
        from flux.models import SQLiteRepository

        self.repository = SQLiteRepository()

    def session(self):
        return self.repository.session()

    def get(self, name: str) -> WorkerInfo:
        # Import here to avoid circular imports
        from flux.models import WorkerModel

        with self.session() as session:
            worker = session.query(WorkerModel).filter(WorkerModel.name == name).first()
            if not worker:
                raise WorkerNotFoundError(name)
            return self._to_info(worker)

    def register(
        self,
        name: str,
        runtime: WorkerRuntimeInfo | None,
        packages: list[dict[str, str]],
        resources: WorkerResourcesInfo | None,
    ) -> WorkerInfo:
        # Import here to avoid circular imports
        from flux.models import WorkerModel

        with self.session() as session:
            try:
                model = session.query(WorkerModel).filter(WorkerModel.name == name).first()
                if model:
                    # generate a new session token
                    model.session_token = uuid4().hex
                else:
                    # Creates a new model and assigns the session token
                    model = self._from_info(name, runtime, packages, resources)
                    session.add(model)
                session.commit()
                return self._to_info(model)
            except Exception:  # pragma: no cover
                session.rollback()
                raise

    def _from_info(self, name, runtime, packages, resources):
        # Import here to avoid circular imports
        from flux.models import (
            WorkerModel,
            WorkerRuntimeModel,
            WorkerPackageModel,
            WorkerResourcesModel,
            WorkerResourcesGPUModel,
        )

        return WorkerModel(
            name=name,
            runtime=WorkerRuntimeModel(
                runtime.os_name,
                runtime.os_version,
                runtime.python_version,
            ),
            packages=[WorkerPackageModel(p["name"], p["version"]) for p in packages],
            resources=WorkerResourcesModel(
                resources.cpu_total,
                resources.cpu_available,
                resources.memory_total,
                resources.memory_available,
                resources.disk_total,
                resources.disk_free,
                [
                    WorkerResourcesGPUModel(
                        gpu.name,
                        gpu.memory_total,
                        gpu.memory_available,
                    )
                    for gpu in resources.gpus
                ],
            ),
        )

    def _to_info(self, model: WorkerModel) -> WorkerInfo:
        return WorkerInfo(
            name=model.name,
            runtime=WorkerRuntimeInfo(
                model.runtime.os_name,
                model.runtime.os_version,
                model.runtime.python_version,
            ),
            packages=[{"name": p.name, "version": p.version} for p in model.packages],
            resources=WorkerResourcesInfo(
                model.resources.cpu_total,
                model.resources.cpu_available,
                model.resources.memory_total,
                model.resources.memory_available,
                model.resources.disk_total,
                model.resources.disk_free,
                [
                    WorkerResouceGPUInfo(
                        gpu.name,
                        gpu.memory_total,
                        gpu.memory_available,
                    )
                    for gpu in model.resources.gpus
                ],
            ),
            session_token=model.session_token,
        )
