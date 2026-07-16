"""Tests for the service-socket client helpers.

The round-trip tests run a real HTTP server on a Unix domain socket
(asyncio.start_unix_server with hand-rolled responses) — the same wire a
sealed execution sees, minus the container.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from flux.errors import ExecutionError
from flux.tasks import service_client, service_socket
from flux.tasks.service_socket import SERVICE_SOCKETS_ENV


def _set_services(monkeypatch, services: dict[str, str]) -> None:
    monkeypatch.setenv(SERVICE_SOCKETS_ENV, json.dumps(services))


class TestServiceSocket:
    def test_returns_granted_path(self, monkeypatch):
        _set_services(monkeypatch, {"inference": "/run/flux/services/inference/service.sock"})
        assert service_socket("inference") == "/run/flux/services/inference/service.sock"

    def test_missing_service_names_the_fix(self, monkeypatch):
        _set_services(monkeypatch, {"other": "/run/flux/services/other/service.sock"})
        with pytest.raises(ExecutionError) as excinfo:
            service_socket("inference")
        message = str(excinfo.value)
        assert "available: other" in message
        assert "airgapped_service_sockets" in message
        assert "flux.service.inference" in message

    def test_no_env_means_no_services(self, monkeypatch):
        monkeypatch.delenv(SERVICE_SOCKETS_ENV, raising=False)
        with pytest.raises(ExecutionError, match="available: none"):
            service_socket("inference")

    def test_malformed_env_treated_as_empty(self, monkeypatch):
        monkeypatch.setenv(SERVICE_SOCKETS_ENV, "not-json")
        with pytest.raises(ExecutionError, match="available: none"):
            service_socket("inference")


async def _serve_http_over_uds(socket_path: str, body: bytes, *, chunked: bool = False):
    """Minimal HTTP/1.1 server on a UDS; enough for httpx round-trips."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        while True:
            line = await reader.readline()
            if not line or line == b"\r\n":
                break
        if chunked:
            writer.write(
                b"HTTP/1.1 200 OK\r\ncontent-type: text/plain\r\n"
                b"transfer-encoding: chunked\r\n\r\n",
            )
            for piece in (body[: len(body) // 2], body[len(body) // 2 :]):
                writer.write(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
                await writer.drain()
            writer.write(b"0\r\n\r\n")
        else:
            writer.write(
                b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n"
                b"content-length: " + str(len(body)).encode() + b"\r\n\r\n" + body,
            )
        await writer.drain()
        writer.close()

    return await asyncio.start_unix_server(handle, path=socket_path)


class TestServiceClient:
    async def test_round_trip_over_uds(self, monkeypatch, tmp_path):
        socket_path = str(tmp_path / "service.sock")
        server = await _serve_http_over_uds(socket_path, b'{"ok": true}')
        _set_services(monkeypatch, {"echo": socket_path})
        try:
            async with service_client("echo") as client:
                response = await client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"ok": True}
        finally:
            server.close()
            await server.wait_closed()

    async def test_streamed_response_over_uds(self, monkeypatch, tmp_path):
        socket_path = str(tmp_path / "service.sock")
        server = await _serve_http_over_uds(socket_path, b"hello sealed world", chunked=True)
        _set_services(monkeypatch, {"echo": socket_path})
        try:
            chunks: list[bytes] = []
            async with service_client("echo") as client:
                async with client.stream("GET", "/generate") as response:
                    async for chunk in response.aiter_bytes():
                        chunks.append(chunk)
            assert b"".join(chunks) == b"hello sealed world"
            assert len(chunks) >= 2  # arrived as a stream, not one buffer
        finally:
            server.close()
            await server.wait_closed()

    async def test_down_sidecar_is_a_connect_error(self, monkeypatch, tmp_path):
        import httpx

        _set_services(monkeypatch, {"echo": str(tmp_path / "absent.sock")})
        async with service_client("echo") as client:
            with pytest.raises(httpx.ConnectError):
                await client.get("/health")

    async def test_client_kwargs_pass_through(self, monkeypatch, tmp_path):
        _set_services(monkeypatch, {"echo": str(tmp_path / "service.sock")})
        async with service_client("echo", timeout=42.0, base_url="http://custom") as client:
            assert client.timeout.read == 42.0
            assert str(client.base_url) == "http://custom"
