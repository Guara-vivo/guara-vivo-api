"""HttpOnly cookie that carries the panel's refresh token, and the Origin check that guards it.

Only the web session kinds use the cookie; the mobile app keeps the token in the response body.
The cookie is host-only (no Domain) and scoped to /users, so it reaches only the auth endpoints.
"""
import os

from fastapi import HTTPException, Request, Response, status

from security import SESSION_KIND_WEB, WEB_SESSION_KINDS, refresh_ttl_seconds

REFRESH_COOKIE_NAME = "gv_refresh"
REFRESH_COOKIE_PATH = "/users"


def get_cors_origins() -> list[str]:
    origins = os.getenv("CORS_ORIGINS", "")
    return [origin.strip() for origin in origins.split(",") if origin.strip()]


def _secure() -> bool:
    # Secure by default; set REFRESH_COOKIE_SECURE=false only for local development over plain HTTP.
    return os.getenv("REFRESH_COOKIE_SECURE", "true").strip().lower() not in {"0", "false", "no", "off"}


def set_refresh_cookie(response: Response, token: str, kind: str) -> None:
    if kind not in WEB_SESSION_KINDS:
        raise ValueError(f"session kind {kind!r} does not use a cookie")
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        # Persistent only for "remember me"; web_session is a session cookie (no Max-Age).
        max_age=refresh_ttl_seconds(kind) if kind == SESSION_KIND_WEB else None,
        path=REFRESH_COOKIE_PATH,
        secure=_secure(),
        httponly=True,
        samesite="lax",
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        secure=_secure(),
        httponly=True,
        samesite="lax",
    )


def reject_foreign_origin(request: Request) -> None:
    """CSRF guard for cookie-authenticated requests: a present Origin must be in CORS_ORIGINS.

    A missing Origin is accepted (non-browser clients and the Vite dev proxy send none; browsers
    always send it on POST). SameSite=Lax already stops cross-site POSTs; this covers other
    origins inside the same site.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return
    if origin not in get_cors_origins():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origin not allowed")
