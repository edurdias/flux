from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from flux.client import FluxClient, DEFAULT_TIMEOUT


class TestFluxClientInit:
    def test_creates_with_server_url(self):
        client = FluxClient("http://localhost:9000")
        assert client.server_url == "http://localhost:9000"

    def test_creates_http_client(self):
        client = FluxClient("http://localhost:8000")
        assert client._http_client is not None

    def test_default_timeout(self):
        client = FluxClient("http://localhost:8000")
        assert client._http_client.timeout.read == DEFAULT_TIMEOUT

    def test_custom_timeout(self):
        client = FluxClient("http://localhost:8000", timeout=30.0)
        assert client._http_client.timeout.read == 30.0

    def test_none_timeout_disables(self):
        client = FluxClient("http://localhost:8000", timeout=None)
        assert client._http_client.timeout.read is None
