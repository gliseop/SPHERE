"""ASGI-middleware для ограничения размера тела запроса."""

from __future__ import annotations

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BodySizeLimitMiddleware:
    """Ограничить размер тела HTTP-запроса, чтобы избежать DoS через большие JSON."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Обработать ASGI-запрос с проверкой размера тела.

        Args:
            scope: ASGI scope.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        for k, v in scope.get("headers", []):
            if k.lower() != b"content-length":
                continue
            try:
                if int(v) > self.max_bytes:
                    res = JSONResponse(
                        {"detail": "Request body too large"}, status_code=413
                    )
                    await res(scope, receive, send)
                    return
            except ValueError:
                break

        received = 0

        class _RequestBodyTooLarge(Exception):
            pass

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body") or b""
                received += len(body)
                if received > self.max_bytes:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            res = JSONResponse({"detail": "Request body too large"}, status_code=413)
            await res(scope, receive, send)
