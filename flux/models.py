from __future__ import annotations

import base64
from datetime import datetime
from typing import Any

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
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import relationship
from sqlalchemy.orm import Session

import flux.decorators as decorators
from flux.config import Configuration
from flux.context import WorkflowExecutionContext
from flux.events import ExecutionEvent
from flux.events import ExecutionEventType


class Base(DeclarativeBase):
    pass


class SQLiteRepository:
    def __init__(self):
        settings = Configuration.get().settings.database_sqlite
        self._engine = create_engine(f"sqlite:///{settings.path}/{settings.filename}")
        Base.metadata.create_all(self._engine)

    def session(self) -> Session:
        return Session(self._engine)


class EncryptedType(TypeDecorator):
    impl = String
    cache_ok = True

    def __init__(self):
        super().__init__()
        settings = Configuration.get().settings.security
        self.key = settings.encryption_key
        self.protocol = dill.HIGHEST_PROTOCOL

    def _derive_key(self, salt: bytes) -> bytes:
        """Derive an encryption key using PBKDF2"""
        return PBKDF2(
            password=self.key.encode("utf-8"),
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


class SecretModel(Base):
    __tablename__ = "secrets"

    name = Column(String, primary_key=True, unique=True, nullable=False)
    value = Column(
        EncryptedType(),
        nullable=False,
    )  # TODO: replace static key with configuration


class WorkflowModel(Base):
    __tablename__ = "workflows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    version = Column(Integer, nullable=False)
    code = Column(PickleType(pickler=dill), nullable=False)

    def __init__(self, name: str, code: decorators.workflow, version: int = 1):
        self.name = name
        self.code = code
        self.version = version


class WorkflowExecutionContextModel(Base):
    __tablename__ = "workflow_executions"

    execution_id = Column(
        String,
        primary_key=True,
        unique=True,
        nullable=False,
    )
    name = Column(String, nullable=False)
    input = Column(PickleType(pickler=dill), nullable=True)
    output = Column(PickleType(pickler=dill), nullable=True)

    # Relationship to events
    events = relationship(
        "ExecutionEventModel",
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="ExecutionEventModel.id",
    )

    def __init__(
        self,
        execution_id: str,
        name: str,
        input: Any,
        events: list[ExecutionEventModel] = [],
        output: Any | None = None,
    ):
        self.execution_id = execution_id
        self.name = name
        self.input = input
        self.events = events
        self.output = output

    def to_plain(self) -> WorkflowExecutionContext:
        return WorkflowExecutionContext(
            self.name,
            self.input,
            self.execution_id,
            [e.to_plain() for e in self.events],
        )

    @classmethod
    def from_plain(cls, obj: WorkflowExecutionContext) -> WorkflowExecutionContextModel:
        return cls(
            execution_id=obj.execution_id,
            name=obj.name,
            input=obj.input,
            output=obj.output,
            events=[ExecutionEventModel.from_plain(obj.execution_id, e) for e in obj.events],
        )


class ExecutionEventModel(Base):
    __tablename__ = "workflow_execution_events"

    execution_id = Column(
        String,
        ForeignKey("workflow_executions.execution_id"),
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
        "WorkflowExecutionContextModel",
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
