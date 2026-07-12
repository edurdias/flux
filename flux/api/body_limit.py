"""Global request body-size limit (SEC5).

Checkpoint, run-input, and progress bodies are dill payloads read into
memory; without a cap a single request can exhaust the server. Pure ASGI
middleware (not BaseHTTPMiddleware, which buffers): declared
Content-Length beyond the limit is rejected up front with 413, and
chunked/streamed bodies are counted as they arrive so a lying or absent
Content-Length cannot bypass the cap.
"""

from __future__ import annotations

import json

from starlette.exceptions import HTTPException


class BodyTooLarge(HTTPException):
    """Raised from the wrapped receive when a streamed body exceeds the cap.

    An HTTPException subclass on purpose: FastAPI's body parsing re-raises
    HTTPExceptions from middleware (anything else becomes a generic 400), so
    in-app reads convert to a proper 413 via the normal exception handlers.
    The middleware's own catch covers reads outside that machinery.
    """

    def __init__(self, max_body_size: int):
        super().__init__(
            status_code=413,
            detail=f"Request body too large: limit is {max_body_size} bytes",
        )


class BodySizeLimitMiddleware:
    def __init__(self, app, max_body_size: int):
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.max_body_size <= 0:
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers") or []:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    break
                if declared > self.max_body_size:
                    await self._send_413(send)
                    return
                break

        received = 0
        response_started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_size:
                    raise BodyTooLarge(self.max_body_size)
            return message

        async def tracking_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyTooLarge:
            # Only answer if the app hadn't started responding; otherwise
            # closing the connection mid-response is the only option left.
            if not response_started:
                await self._send_413(send)

    async def _send_413(self, send):
        body = json.dumps(
            {"detail": f"Request body too large: limit is {self.max_body_size} bytes"},
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            },
        )
        await send({"type": "http.response.body", "body": body})
