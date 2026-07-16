"""Client-side access to worker-granted service sockets.

Airgapped workers can grant sealed executions access to long-lived local
sidecars (warm runtimes) over Unix domain sockets — see
``[flux.workers] airgapped_service_sockets``. The runner mounts each
service's socket directory into the container and publishes the map in
the ``FLUX_SERVICE_SOCKETS`` environment variable; these helpers are the
workflow-facing surface.

Calls made through :func:`service_client` should live inside ordinary
``@task`` functions: outputs are then checkpointed, replay short-circuits
without re-contacting the sidecar, and a down sidecar surfaces as a
normal task error (retry/fallback/rollback apply).
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from flux.errors import ExecutionError

SERVICE_SOCKETS_ENV = "FLUX_SERVICE_SOCKETS"


def _available_services() -> dict[str, str]:
    raw = os.environ.get(SERVICE_SOCKETS_ENV, "")
    if not raw:
        return {}
    try:
        services = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(services, dict):
        return {}
    return {str(name): str(path) for name, path in services.items()}


def service_socket(name: str) -> str:
    """Return the Unix-socket path for a granted service.

    Raises ExecutionError when the service is not available in this
    execution — either the worker does not grant it, or the workflow was
    not routed to a worker that does (target them with
    ``affinity={"flux.service.<name>": "true"}``).
    """
    services = _available_services()
    if name not in services:
        available = ", ".join(sorted(services)) or "none"
        raise ExecutionError(
            message=(
                f"Service '{name}' is not available in this execution "
                f"(available: {available}). Grant it on the worker via "
                f"[flux.workers] airgapped_service_sockets and route to "
                f"service-bearing workers with "
                f"affinity={{'flux.service.{name}': 'true'}}."
            ),
        )
    return services[name]


def service_client(name: str, **client_kwargs: Any) -> httpx.AsyncClient:
    """An ``httpx.AsyncClient`` speaking HTTP over the service's socket.

    The host in request URLs is ignored over a UDS transport; a stable
    placeholder base_url is set so plain paths work. Extra keyword
    arguments (timeout, headers, ...) pass through to the client. Works
    with OpenAI-compatible SDKs by handing them this client as their
    http client.
    """
    transport = httpx.AsyncHTTPTransport(uds=service_socket(name))
    client_kwargs.setdefault("base_url", "http://flux-service")
    return httpx.AsyncClient(transport=transport, **client_kwargs)
