from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BaseConfig(BaseModel):
    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class EncryptionConfig(BaseConfig):
    encryption_key: str | None = Field(
        default=None,
        description="Encryption key for sensitive data",
    )
