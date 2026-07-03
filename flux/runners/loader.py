"""Compile workflow source into modules, with a bounded source-hash cache.

Extracted from ``flux/worker.py`` so the in-process runner and the
subprocess child share one compile path.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import sys
import time
from collections import OrderedDict
from types import ModuleType

from flux import workflow
from flux.utils import get_logger

logger = get_logger(__name__)


def hash_source(source_b64: str) -> str:
    """Short digest of the (base64-encoded) workflow source as shipped."""
    return hashlib.sha256(source_b64.encode()).hexdigest()[:12]


def make_module_cache_key(namespace: str, name: str, version: int, source_hash: str) -> str:
    # Keyed by source hash so a same-version re-registration (delete +
    # register, catalog overwrite) recompiles immediately instead of serving
    # the previous source for up to the cache TTL.
    return f"{namespace}:{name}:{version}:{source_hash}"


def make_module_name(namespace: str, name: str, version: int, source_hash: str) -> str:
    # The hash suffix keeps sys.modules entries per source variant, so
    # evicting one cache entry can never remove a newer variant's module.
    safe_namespace = namespace.replace("-", "_")
    safe_name = name.replace("-", "_")
    return f"flux_workflow__{safe_namespace}__{safe_name}__v{version}__h{source_hash}"


def find_workflow(module: ModuleType, namespace: str, name: str) -> workflow | None:
    for obj in module.__dict__.values():
        if isinstance(obj, workflow) and obj.namespace == namespace and obj.name == name:
            return obj
    return None


class WorkflowModuleLoader:
    """TTL + LRU cache of compiled workflow modules.

    ``ttl=0`` disables caching entirely; ``max_size=0`` keeps the cache
    unbounded (the legacy behavior).
    """

    def __init__(self, ttl: int = 300, max_size: int = 64):
        self._ttl = ttl
        self._max_size = max_size
        self._cache: OrderedDict[str, tuple[ModuleType, float]] = OrderedDict()

    def load(self, namespace: str, name: str, version: int, source_b64: str) -> ModuleType:
        source_hash = hash_source(source_b64)
        cache_key = make_module_cache_key(namespace, name, version, source_hash)
        module_name = make_module_name(namespace, name, version, source_hash)

        from flux.observability import get_metrics

        m = get_metrics()

        if self._ttl > 0:
            cached = self._cache.get(cache_key)
            if cached:
                cached_module, cached_at = cached
                if time.monotonic() - cached_at < self._ttl:
                    self._cache.move_to_end(cache_key)
                    logger.debug(f"Module cache hit for {cache_key}")
                    if m:
                        m.record_module_cache("hit")
                    return cached_module
                del self._cache[cache_key]
                sys.modules.pop(module_name, None)

        if m and self._ttl > 0:
            m.record_module_cache("miss")

        source_code = base64.b64decode(source_b64).decode("utf-8")
        logger.debug(f"Decoded workflow source code ({len(source_code)} bytes)")

        # Drop any stale module under this name before exec'ing the new
        # source (only possible after TTL expiry of this exact variant —
        # the name includes the source hash).
        sys.modules.pop(module_name, None)

        logger.debug(f"Creating module: {module_name}")
        module_spec = importlib.util.spec_from_loader(module_name, loader=None)
        module = importlib.util.module_from_spec(module_spec)  # type: ignore
        sys.modules[module_name] = module

        logger.debug("Executing workflow source code")
        exec(source_code, module.__dict__)

        if self._ttl > 0:
            self._cache[cache_key] = (module, time.monotonic())
            while self._max_size > 0 and len(self._cache) > self._max_size:
                _, (evicted, _) = self._cache.popitem(last=False)
                sys.modules.pop(evicted.__name__, None)

        return module
