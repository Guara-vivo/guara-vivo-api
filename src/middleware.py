import os
from starlette.responses import JSONResponse


MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", "10485760"))


class RequestBodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """
    ASGI middleware to enforce request body size limits by reading the stream.
    Works regardless of Content-Length header (which can be missing or manipulated).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        if scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        total_size = 0

        async def limited_receive():
            nonlocal total_size
            message = await receive()
            if message.get("type") == "http.request":
                total_size += len(message.get("body", b""))
                if total_size > MAX_REQUEST_BODY_BYTES:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            response = JSONResponse(
                status_code=413,
                content={"detail": "Payload too large"},
            )
            await response(scope, receive, send)
