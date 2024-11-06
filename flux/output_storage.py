from __future__ import annotations

import json
from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Literal

import dill

from flux.config import Configuration


@dataclass
class OutputStorageReference:
    """A reference to a stored task or workflow output.

    This class represents a reference to an output that has been persisted along with
    execution events in a storage system. It contains information about the type of storage,
    a reference ID to locate the output, and optional metadata.

    Attributes:
        storage_type (str): The type of storage system where the output is stored.
        reference_id (str): The unique identifier for the stored output.
        metadata (dict[str, Any]): Optional metadata associated with the stored output.

    Methods:
        to_dict() -> dict:
            Converts the reference to a dictionary representation.

        from_dict(data: dict) -> OutputStorageReference:
            Creates a new OutputStorageReference instance from a dictionary.

    Example:
        >>> ref = OutputStorageReference(
        ...     storage_type="s3",
        ...     reference_id="bucket/path/to/output",
        ...     metadata={"size": 1024}
        ... )
        >>> ref_dict = ref.to_dict()
        >>> new_ref = OutputStorageReference.from_dict(ref_dict)
    """

    storage_type: str
    reference_id: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "storage_type": self.storage_type,
            "reference_id": self.reference_id,
            "metadata": self.metadata or {},
        }

    @staticmethod
    def from_dict(data: dict) -> OutputStorageReference:
        return OutputStorageReference(
            storage_type=data["storage_type"],
            reference_id=data["reference_id"],
            metadata=data.get("metadata", {}),
        )


class OutputStorage(ABC):
    @abstractmethod
    def retrieve(self, reference: OutputStorageReference) -> Any:  # pragma: no cover
        """
        Retrieves the output data associated with the given reference.

        Args:
            reference (OutputStorageReference): A reference to the stored output data.

        Returns:
            Any: The output data associated with the reference.
        """
        raise NotImplementedError()

    @abstractmethod
    def store(self, reference_id: str, value: Any) -> OutputStorageReference:  # pragma: no cover
        """Stores a value with a reference ID in the output storage.

        Args:
            reference_id (str): The unique identifier for the stored value.
            value (Any): The value to be stored. Can be of any type.

        Returns:
            OutputStorageReference: A reference object containing information about the stored value.
        """

        raise NotImplementedError()

    @abstractmethod
    def delete(self, reference: OutputStorageReference) -> Any:  # pragma: no cover
        """
        Delete the stored output identified by the given reference.

        Args:
            reference (OutputStorageReference): Reference to the output to be deleted.

        Returns:
            Any: Result of the deletion operation.
        """

        raise NotImplementedError()


class InlineOutputStorage(OutputStorage):
    def retrieve(self, reference: OutputStorageReference) -> Any:
        return reference.metadata["value"]

    def store(self, reference_id: str, value: Any) -> OutputStorageReference:
        return OutputStorageReference(
            storage_type="inline",
            reference_id=reference_id,
            metadata={"value": value},
        )

    def delete(self, reference: OutputStorageReference):  # pragma: no cover
        pass


class LocalFileStorage(OutputStorage):
    def __init__(
        self,
        base_path: str | None = None,
        serializer: Literal["json", "pkl"] | None = None,
    ):
        settings = Configuration.get().settings.local_storage
        self.base_path = Path(base_path if base_path else settings.base_path)
        self.serializer = serializer if serializer else settings.serializer
        self.base_path.mkdir(parents=True, exist_ok=True)

    def retrieve(self, reference: OutputStorageReference) -> Any:
        self._verify_storage_type(reference)

        file_path = self._get_file_path(reference.reference_id)
        content = file_path.read_bytes()
        return self.__deserialize(content, reference.metadata["serializer"])

    def store(self, reference_id: str, value: Any) -> OutputStorageReference:
        file_path = self._get_file_path(reference_id)
        file_path.write_bytes(self.__serialize(value))
        return OutputStorageReference(
            storage_type="local_file",
            reference_id=reference_id,
            metadata={"serializer": self.serializer},
        )

    def delete(self, reference: OutputStorageReference):  # pragma: no cover
        self._verify_storage_type(reference)
        file_path = self._get_file_path(reference.reference_id)
        file_path.unlink()

    def _verify_storage_type(self, reference):
        if reference.storage_type != "local_file":
            raise ValueError(f"Invalid storage type: {reference.storage_type}")

    def _get_file_path(self, reference_id: str):
        return self.base_path / f"{reference_id}.{self.serializer}"

    def __serialize(self, value: Any) -> bytes:
        return json.dumps(value).encode("utf-8") if self.serializer == "json" else dill.dumps(value)

    def __deserialize(self, value: bytes, serializer: str) -> Any:
        _serializer = serializer or self.serializer
        return json.loads(value) if _serializer == "json" else dill.loads(value)
