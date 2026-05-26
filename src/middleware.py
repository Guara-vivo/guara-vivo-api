import os
from starlette.responses import JSONResponse


MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", "10485760"))


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

        body = b""
        try:
            more_body = True
            while more_body:
                message = await receive()
                if message.get("type") != "http.request":
                    continue
                chunk = message.get("body", b"")
                if chunk:
                    body += chunk
                    if len(body) > MAX_REQUEST_BODY_BYTES:
                        response = JSONResponse(
                            status_code=413,
                            content={"detail": "Payload too large"},
                        )
                        await response(scope, receive, send)
                        return
                more_body = message.get("more_body", False)
        except Exception:
            response = JSONResponse(
                status_code=400,
                content={"detail": "Error reading request body"},
            )
            await response(scope, receive, send)
            return

        async def receive_with_body():
            nonlocal body
            if body is None:
                return {"type": "http.request", "body": b"", "more_body": False}
            chunk = body
            body = None
            return {"type": "http.request", "body": chunk, "more_body": False}

        await self.app(scope, receive_with_body, send)
