from __future__ import annotations

import base64
from datetime import datetime
from typing import Any
from uuid import uuid4

import dill
from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Random import get_random_bytes
from sqlalchemy import Column
from sqlalchemy import create_engine
from sqlalchemy import DateTime
from sqlalchemy import Enum as SqlEnum
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import PickleType
from sqlalchemy import String
from sqlalchemy import TypeDecorator
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import relationship
from sqlalchemy.orm import Session

from flux import ExecutionContext
from flux.config import Configuration
from flux.domain.events import ExecutionEvent
from flux.domain.events import ExecutionEventType
from flux.domain.events import ExecutionState
from flux.domain.resource_request import ResourceRequest


class Base(DeclarativeBase):
    pass


class SQLiteRepository:
    def __init__(self):
        self._engine = create_engine(Configuration.get().settings.database_url)
        Base.metadata.create_all(self._engine)

    def session(self) -> Session:
        return Session(self._engine)


class EncryptedType(TypeDecorator):
    impl = String
    cache_ok = True

    def __init__(self):
        super().__init__()
        self.protocol = dill.HIGHEST_PROTOCOL

    def _get_key(self) -> str:
        key = Configuration.get().settings.security.encryption_key
        if not key:
            raise ValueError("Encryption key is not set in the configuration.")
        return key

    def _derive_key(self, salt: bytes) -> bytes:
        """Derive an encryption key using PBKDF2"""
        return PBKDF2(
            password=self._get_key().encode("utf-8"),
            salt=salt,
            dkLen=32,  # AES-256 key length
            count=1000000,  # Number of iterations
            hmac_hash_module=SHA256,
        )

    def _encrypt(self, data: bytes) -> bytes:
        """Encrypt data using AES-GCM"""
        salt = get_random_bytes(32)
        key = self._derive_key(salt)

        cipher = AES.new(key, AES.MODE_GCM)
        ciphertext, tag = cipher.encrypt_and_digest(data)

        # Combine all the pieces for storage
        return salt + cipher.nonce + tag + ciphertext

    def _decrypt(self, data: bytes) -> bytes:
        """Decrypt data using AES-GCM"""
        salt = data[:32]
        nonce = data[32:48]  # AES GCM nonce is 16 bytes
        tag = data[48:64]  # AES GCM tag is 16 bytes
        ciphertext = data[64:]

        key = self._derive_key(salt)
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ciphertext, tag)

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        """Encrypt value before storing"""
        if value is not None:
            try:
                value = dill.dumps(value, protocol=self.protocol)
                encrypted = self._encrypt(value)
                return base64.b64encode(encrypted).decode("utf-8")
            except Exception as e:
                raise ValueError(f"Failed to encrypt value: {str(e)}") from e
        return None

    def process_result_value(self, value: str | None, dialect: Any) -> Any:
        """Decrypt value when retrieving"""
        if value is not None:
            try:
                # Decode base64 and decrypt
                encrypted = base64.b64decode(value.encode("utf-8"))
                decrypted = self._decrypt(encrypted)
                return dill.loads(decrypted)
            except Exception as e:
                raise ValueError(f"Failed to decrypt value: {str(e)}") from e
        return None


class Base64Type(TypeDecorator):
    impl = String
    cache_ok = True

    def __init__(self):
        super().__init__()
        self.protocol = dill.HIGHEST_PROTOCOL

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        """Serialize to base64 before storing"""
        if value is not None:
            try:
                serialized = dill.dumps(value, protocol=self.protocol)
                return base64.b64encode(serialized).decode("utf-8")
            except Exception as e:
                raise ValueError(f"Failed to serialize value: {str(e)}") from e
        return None

    def process_result_value(self, value: str | None, dialect: Any) -> Any:
        """Deserialize from base64 when retrieving"""
        if value is not None:
            try:
                serialized = base64.b64decode(value.encode("utf-8"))
                return dill.loads(serialized)
            except Exception as e:
                raise ValueError(f"Failed to deserialize value: {str(e)}") from e
        return None


class SecretModel(Base):
    __tablename__ = "secrets"

    name = Column(String, primary_key=True, unique=True, nullable=False)
    value = Column(
        EncryptedType(),
        nullable=False,
    )


class WorkerRuntimeModel(Base):
    __tablename__ = "worker_runtimes"

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: uuid4().hex)
    os_name = Column(String, nullable=False)
    os_version = Column(String, nullable=False)
    python_version = Column(String, nullable=False)

    worker_name = Column(String, ForeignKey("workers.name"), nullable=False)
    worker = relationship("WorkerModel", back_populates="runtime", uselist=False)

    def __init__(self, os_name: str, os_version: str, python_version: str):
        self.os_name = os_name
        self.os_version = os_version
        self.python_version = python_version


class WorkerResourcesGPUModel(Base):
    __tablename__ = "worker_resources_gpus"

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: uuid4().hex)
    name = Column(String, nullable=False)
    memory_total = Column(Integer, nullable=False)
    memory_available = Column(Integer, nullable=False)

    resources_id = Column(String, ForeignKey("worker_resources.id"), nullable=False)
    resources = relationship("WorkerResourcesModel", back_populates="gpus")

    def __init__(self, name: str, memory_total: int, memory_available: int):
        self.name = name
        self.memory_total = memory_total
        self.memory_available = memory_available


class WorkerResourcesModel(Base):
    __tablename__ = "worker_resources"

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: uuid4().hex)
    cpu_total = Column(Integer, nullable=False)
    cpu_available = Column(Integer, nullable=False)
    memory_total = Column(Integer, nullable=False)
    memory_available = Column(Integer, nullable=False)
    disk_total = Column(Integer, nullable=False)
    disk_free = Column(Integer, nullable=False)

    worker_name = Column(String, ForeignKey("workers.name"), nullable=False)
    worker = relationship("WorkerModel", back_populates="resources", uselist=False)

    gpus = relationship("WorkerResourcesGPUModel", back_populates="resources")

    def __init__(
        self,
        cpu_total: int,
        cpu_available: int,
        memory_total: int,
        memory_available: int,
        disk_total: int,
        disk_free: int,
        gpus: list[WorkerResourcesGPUModel] | None = None,
    ):
        self.cpu_total = cpu_total
        self.cpu_available = cpu_available
        self.memory_total = memory_total
        self.memory_available = memory_available
        self.disk_total = disk_total
        self.disk_free = disk_free
        self.gpus = gpus or []


class WorkerPackageModel(Base):
    __tablename__ = "worker_packages"

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: uuid4().hex)
    name = Column(String, nullable=False)
    version = Column(String, nullable=False)

    worker_name = Column(String, ForeignKey("workers.name"), nullable=False)
    worker = relationship("WorkerModel", back_populates="packages")

    def __init__(self, name: str, version: str):
        self.name = name
        self.version = version


class WorkerModel(Base):
    __tablename__ = "workers"

    name = Column(
        String,
        primary_key=True,
        unique=True,
        nullable=False,
        default=lambda: f"worker-{uuid4().hex}",
    )
    session_token = Column(String, nullable=False, default=lambda: uuid4().hex)

    runtime = relationship("WorkerRuntimeModel", back_populates="worker", uselist=False)
    packages = relationship("WorkerPackageModel", back_populates="worker")
    resources = relationship("WorkerResourcesModel", back_populates="worker", uselist=False)

    executions = relationship(
        "ExecutionContextModel",
        back_populates="worker",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )

    def __init__(
        self,
        name: str,
        runtime: WorkerRuntimeModel | None = None,
        packages: list[WorkerPackageModel] | None = None,
        resources: WorkerResourcesModel | None = None,
    ):
        self.name = name
        self.runtime = runtime
        self.packages = packages or []
        self.resources = resources


class WorkflowModel(Base):
    __tablename__ = "workflows"

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: uuid4().hex)
    name = Column(String, nullable=False)
    version = Column(Integer, nullable=False)
    imports = Column(Base64Type(), nullable=True)
    source = Column(Base64Type(), nullable=False)
    requests = Column(Base64Type(), nullable=True)

    # Add a uniqueness constraint on name and version
    __table_args__ = (UniqueConstraint("name", "version", name="uix_workflow_name_version"),)

    # Relationship to executions
    executions = relationship(
        "ExecutionContextModel",
        back_populates="workflow",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )

    def __init__(
        self,
        id: str,
        name: str,
        version: int,
        imports: list[str],
        source: bytes,
        requests: ResourceRequest | None = None,
    ):
        self.id = id
        self.name = name
        self.version = version
        self.imports = imports
        self.source = source
        self.requests = requests


class ExecutionContextModel(Base):
    __tablename__ = "executions"

    execution_id = Column(
        String,
        primary_key=True,
        unique=True,
        nullable=False,
    )
    workflow_id = Column(String, ForeignKey("workflows.id"), nullable=False)
    workflow_name = Column(String, nullable=False)
    input = Column(PickleType(pickler=dill), nullable=True)
    output = Column(PickleType(pickler=dill), nullable=True)
    state = Column(SqlEnum(ExecutionState), nullable=False)
    worker_name = Column(String, ForeignKey("workers.name"), nullable=True)

    # Relationship to events
    events = relationship(
        "ExecutionEventModel",
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="ExecutionEventModel.id",
    )

    # Relationship to workflow
    workflow = relationship("WorkflowModel", back_populates="executions")
    worker = relationship("WorkerModel", back_populates="executions")

    def __init__(
        self,
        execution_id: str,
        workflow_id: str,
        workflow_name: str,
        input: Any,
        events: list[ExecutionEventModel] | None = None,
        output: Any | None = None,
        state: ExecutionState = ExecutionState.CREATED,
        worker_name: str | None = None,
    ):
        self.execution_id = execution_id
        self.workflow_id = workflow_id
        self.workflow_name = workflow_name
        self.input = input
        self.events = events or []
        self.output = output
        self.state = state
        self.worker_name = worker_name

    def to_plain(self) -> ExecutionContext:
        return ExecutionContext(
            workflow_id=self.workflow_id,
            workflow_name=self.workflow_name,
            input=self.input,
            execution_id=self.execution_id,
            events=[e.to_plain() for e in self.events],
            state=self.state,
            current_worker=self.worker_name,
        )

    @classmethod
    def from_plain(cls, obj: ExecutionContext) -> ExecutionContextModel:
        return cls(
            execution_id=obj.execution_id,
            workflow_id=obj.workflow_id,
            workflow_name=obj.workflow_name,
            input=obj.input,
            output=obj.output,
            events=[ExecutionEventModel.from_plain(obj.execution_id, e) for e in obj.events],
            state=obj.state,
            worker_name=obj.current_worker,
        )


class ExecutionEventModel(Base):
    __tablename__ = "execution_events"

    execution_id = Column(
        String,
        ForeignKey("executions.execution_id"),
        nullable=False,
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(String, nullable=False)
    event_id = Column(String, nullable=False)
    type = Column(SqlEnum(ExecutionEventType), nullable=False)
    name = Column(String, nullable=False)
    value = Column(PickleType(pickler=dill), nullable=True)
    time = Column(DateTime, nullable=False)
    execution = relationship(
        "ExecutionContextModel",
        back_populates="events",
    )

    def __init__(
        self,
        source_id: str,
        event_id: str,
        execution_id: str,
        type: ExecutionEventType,
        name: str,
        time: datetime,
        value: Any | None = None,
    ):
        self.source_id = source_id
        self.event_id = event_id
        self.execution_id = execution_id
        self.type = type
        self.name = name
        self.time = time
        self.value = value

    def to_plain(self) -> ExecutionEvent:
        return ExecutionEvent(
            type=self.type,
            id=self.event_id,
            source_id=self.source_id,
            name=self.name,
            time=self.time,
            value=self.value,
        )

    @classmethod
    def from_plain(cls, execution_id: str, obj: ExecutionEvent) -> ExecutionEventModel:
        return cls(
            execution_id=execution_id,
            source_id=obj.source_id,
            event_id=obj.id,
            type=obj.type,
            name=obj.name,
            time=obj.time,
            value=obj.value,
        )
