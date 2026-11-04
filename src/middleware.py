import os
from starlette.responses import JSONResponse


MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", "10485760"))


class RequestBodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """
    ASGI middleware to enforce request body size limits.

    A declared Content-Length over the limit is refused before reading anything. The stream is
    counted as well, because Content-Length can be missing (chunked) or wrong.

    FastAPI turns any exception raised while it reads the body into a generic 400, so raising
    from `receive` is not enough: once the limit is crossed, whatever the app answers is
    discarded and a 413 is sent instead.
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

        if self._declared_length(scope) > MAX_REQUEST_BODY_BYTES:
            await self._too_large(scope, receive, send)
            return

        total_size = 0
        exceeded = False

        async def limited_receive():
            nonlocal total_size, exceeded
            message = await receive()
            if message.get("type") == "http.request":
                total_size += len(message.get("body", b""))
                if total_size > MAX_REQUEST_BODY_BYTES:
                    exceeded = True
                    raise RequestBodyTooLarge
            return message

        async def guarded_send(message):
            if exceeded:
                return  # the app's own (400) answer to the aborted read
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except RequestBodyTooLarge:
            pass

        if exceeded:
            await self._too_large(scope, receive, send)

    @staticmethod
    def _declared_length(scope) -> int:
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    return int(value)
                except ValueError:
                    return 0
        return 0

    @staticmethod
    async def _too_large(scope, receive, send):
        response = JSONResponse(status_code=413, content={"detail": "Payload too large"})
        await response(scope, receive, send)
