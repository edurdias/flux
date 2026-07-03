from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from collections.abc import Sequence
from datetime import datetime, timezone
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
        packages: list[dict[str, str]] | None = None,
        resources: WorkerResourcesInfo | None = None,
        session_token: str | None = None,
        labels: dict[str, str] | None = None,
        max_concurrent_executions: int | None = None,
        last_seen_at: datetime | None = None,
    ):
        self.name = name
        self.runtime = runtime
        self.packages = list(packages) if packages is not None else []
        self.resources = resources
        self.session_token = session_token
        self.labels = labels or {}
        # Advertised capacity; None/0 means unlimited (legacy workers).
        self.max_concurrent_executions = max_concurrent_executions
        # Persisted heartbeat timestamp; None until the first pong lands.
        self.last_seen_at = last_seen_at


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
        labels: dict[str, str] | None = None,
        max_concurrent_executions: int | None = None,
    ) -> WorkerInfo:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def list(self) -> list[WorkerInfo]:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def record_heartbeat(self, name: str) -> None:  # pragma: no cover
        """Persist ``name``'s heartbeat as the current wall-clock UTC time."""
        raise NotImplementedError()

    @abstractmethod
    def record_heartbeats(self, names: Sequence[str]) -> None:  # pragma: no cover
        """Persist heartbeats for many workers in one statement.

        All names get the same (current) timestamp — callers buffer pongs for
        at most one heartbeat interval, which is well inside the staleness
        thresholds, and one UPDATE per interval replaces one commit per pong.
        """
        raise NotImplementedError()

    @abstractmethod
    def find_stale(self, threshold: datetime) -> Sequence[str]:  # pragma: no cover
        """Names of workers whose last heartbeat predates ``threshold``.

        Workers that have never reported a heartbeat (NULL ``last_seen_at``)
        are excluded, so freshly-registered workers are not swept before they
        first connect.
        """
        raise NotImplementedError()

    @staticmethod
    def create() -> WorkerRegistry:
        return DatabaseWorkerRegistry()


class DatabaseWorkerRegistry(WorkerRegistry):
    """Dialect-agnostic worker registry. Delegates to ``RepositoryFactory``."""

    def __init__(self):
        from flux.models import RepositoryFactory

        self.repository = RepositoryFactory.create_repository()

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
        labels: dict[str, str] | None = None,
        max_concurrent_executions: int | None = None,
    ) -> WorkerInfo:
        # Import here to avoid circular imports
        from flux.models import WorkerModel

        with self.session() as session:
            try:
                model = session.query(WorkerModel).filter(WorkerModel.name == name).first()
                if model:
                    # generate a new session token
                    model.session_token = uuid4().hex
                    model.labels = labels or {}
                    model.max_concurrent_executions = max_concurrent_executions
                else:
                    # Creates a new model and assigns the session token
                    model = self._from_info(
                        name,
                        runtime,
                        packages,
                        resources,
                        labels=labels,
                        max_concurrent_executions=max_concurrent_executions,
                    )
                    session.add(model)
                session.commit()
                return self._to_info(model)
            except Exception:  # pragma: no cover
                session.rollback()
                raise

    def list(self) -> list[WorkerInfo]:
        # Import here to avoid circular imports
        from flux.models import WorkerModel

        with self.session() as session:
            workers = session.query(WorkerModel).all()
            return [self._to_info(worker) for worker in workers]

    def record_heartbeat(self, name: str) -> None:
        from flux.models import WorkerModel

        # Naive UTC, matching the column type and every comparison site
        # (reaper threshold, /workers liveness). Writing an aware datetime
        # into TIMESTAMP WITHOUT TIME ZONE round-trips correctly only when
        # the session TimeZone happens to be UTC — make it explicit instead.
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self.session() as session:
            # Targeted UPDATE — cheap enough to run on every pong, and avoids
            # loading the worker's runtime/packages/resources relationships.
            session.query(WorkerModel).filter(WorkerModel.name == name).update(
                {WorkerModel.last_seen_at: now},
                synchronize_session=False,
            )
            session.commit()

    def record_heartbeats(self, names: Sequence[str]) -> None:
        if not names:
            return
        from flux.models import WorkerModel

        # Naive UTC — see record_heartbeat.
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self.session() as session:
            session.query(WorkerModel).filter(WorkerModel.name.in_(list(names))).update(
                {WorkerModel.last_seen_at: now},
                synchronize_session=False,
            )
            session.commit()

    def find_stale(self, threshold: datetime) -> Sequence[str]:
        from flux.models import WorkerModel

        with self.session() as session:
            rows = (
                session.query(WorkerModel.name)
                .filter(
                    WorkerModel.last_seen_at.isnot(None),
                    WorkerModel.last_seen_at < threshold,
                )
                .all()
            )
            return [row[0] for row in rows]

    def _from_info(
        self,
        name,
        runtime,
        packages,
        resources,
        labels=None,
        max_concurrent_executions=None,
    ):
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
            labels=labels,
            max_concurrent_executions=max_concurrent_executions,
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
            labels=model.labels if model.labels else {},
            max_concurrent_executions=model.max_concurrent_executions,
            last_seen_at=model.last_seen_at,
        )
