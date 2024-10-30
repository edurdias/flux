from __future__ import annotations

import json
from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import cast
from typing import Literal

import dill


class OutputStorage(ABC):
    @abstractmethod
    def get(self, output: Any) -> Any:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def store(self, id: str, value: Any) -> Any:  # pragma: no cover
        raise NotImplementedError()


class InlineOutputStorage(OutputStorage):
    def get(self, output: Any) -> Any:
        return output

    def store(self, id: str, value: Any) -> Any:
        return value


@dataclass
class FileOutput:
    filename: str
    serializer: Literal["json", "pickle"]


class LocalFileStorage(OutputStorage):
    def __init__(self, base_path: str = ".data", serializer: Literal["json", "pickle"] = "json"):
        self.base_path = base_path
        self.serializer = serializer

    def get(self, output: Any) -> Any:
        file_output: FileOutput = cast(FileOutput, output)
        full_path = Path(self.base_path)
        file_path = full_path / file_output.filename
        content = file_path.read_bytes()
        return self.__deserialize(content, file_output.serializer or self.serializer)

    def store(self, id: str, value: Any) -> Any:
        full_path = Path(self.base_path)
        full_path.mkdir(parents=True, exist_ok=True)
        file_path = full_path / f"{id}.{self.serializer}"
        file_path.write_bytes(self.__serialize(value))
        return FileOutput(filename=file_path.name, serializer=self.serializer)

    def __serialize(self, value: Any) -> bytes:
        return json.dumps(value).encode("utf-8") if self.serializer == "json" else dill.dumps(value)

    def __deserialize(self, value: bytes, serializer: Literal["json", "pickle"]) -> Any:
        return json.loads(value) if self.serializer == "json" else dill.loads(value)
