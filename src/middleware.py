import os
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", "10485760"))


class BodyLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware to enforce request body size limits by reading the stream.
    Works regardless of Content-Length header (which can be missing or manipulated).
    """
    
    async def dispatch(self, request: Request, call_next):
        # Only check POST, PUT, PATCH
        if request.method in ["POST", "PUT", "PATCH"]:
            try:
                # Read body stream with size limit
                body = b""
                async for chunk in request.stream():
                    body += chunk
                    if len(body) > MAX_REQUEST_BODY_BYTES:
                        return JSONResponse(
                            status_code=413,
                            content={"detail": "Payload too large"},
                        )
                
                # Replace request stream with body we read
                async def receive():
                    return {"type": "http.request", "body": body, "more_body": False}
                
                request._receive = receive
            except Exception:
                # If anything goes wrong reading the stream, reject
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Error reading request body"},
                )
        
        return await call_next(request)
