from __future__ import annotations

from pathlib import Path
from typing import Any

import dill

from flux.config import Configuration
from flux.security.integrity import IntegrityError, sign, verify


class CacheManager:
    @staticmethod
    def get(key: str) -> Any:
        cache_file = CacheManager._get_file_name(key)
        if cache_file.exists():
            raw = cache_file.read_bytes()
            try:
                payload = verify(raw)
            except IntegrityError:
                # A tampered or unverifiable cache entry is treated as a miss so
                # it is recomputed rather than loaded (dill.loads executes code).
                return None
            return dill.loads(payload)
        return None

    @staticmethod
    def set(key: str, value: Any) -> None:
        cache_file = CacheManager._get_file_name(key)
        cache_file.write_bytes(sign(dill.dumps(value)))

    @staticmethod
    def _get_file_name(key):
        settings = Configuration.get().settings
        cache_path = Path(settings.home) / settings.cache_path
        cache_path.mkdir(parents=True, exist_ok=True)
        cache_file = cache_path / f"{key}.pkl"
        return cache_file
