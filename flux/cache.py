from __future__ import annotations

from pathlib import Path
from typing import Any

import dill

from flux.config import Configuration


class CacheManager:
    @staticmethod
    def get(key: str) -> Any:
        cache_file = CacheManager._get_file_name(key)
        if cache_file.exists():
            with open(cache_file, "rb") as f:
                return dill.load(f)
        return None

    @staticmethod
    def set(key: str, value: Any) -> None:
        cache_file = CacheManager._get_file_name(key)
        with open(cache_file, "wb") as f:
            dill.dump(value, f)

    @staticmethod
    def _get_file_name(key):
        settings = Configuration.get().settings
        cache_path = Path(settings.home) / settings.cache_path
        cache_path.mkdir(parents=True, exist_ok=True)
        cache_file = cache_path / f"{key}.pkl"
        return cache_file
