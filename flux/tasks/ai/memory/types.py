from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MemoryEntry:
    agent: str
    scope: str
    key: str
    value: Any
