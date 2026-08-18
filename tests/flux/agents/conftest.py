"""Console/agent-UI tests must not touch the network.

These tests build consoles pointed at placeholder server URLs and drive
them with real Bearer tokens. A route that reaches its service unstubbed
therefore resolves that placeholder for real and sends the token wherever
a wildcard-DNS resolver points -- which is how a unit test both leaks a
credential and becomes flaky offline (#245). Resolution of anything but a
loopback name fails loudly here, so the next such test fails in review
rather than silently dialling out.
"""

from __future__ import annotations

import socket

import pytest

_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "", None}


@pytest.fixture(autouse=True)
def no_outbound_dns(monkeypatch):
    real_getaddrinfo = socket.getaddrinfo

    def guarded(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if name in _LOOPBACK_NAMES:
            return real_getaddrinfo(host, *args, **kwargs)
        raise AssertionError(
            f"test attempted to resolve '{name}' -- stub the console service "
            "instead of letting a unit test reach the network",
        )

    monkeypatch.setattr(socket, "getaddrinfo", guarded)
