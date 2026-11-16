"""Answer a cancelled database statement with 503 instead of a bare 500.

`DATABASE_STATEMENT_TIMEOUT_SECONDS` makes Postgres cancel a statement that runs too long
(SQLSTATE 57014). It is a temporary condition, so clients get 503 + `Retry-After` and can retry,
and the log gets one line instead of a traceback. Any other database error is left alone and
still ends as a 500.
"""
import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

logger = logging.getLogger(__name__)

QUERY_CANCELED_SQLSTATE = "57014"
RETRY_AFTER_SECONDS = 5


def _sqlstate(error: BaseException | None) -> str | None:
    # SQLAlchemy keeps the driver error in `orig`; asyncpg's own exception may sit one level below.
    for candidate in (error, getattr(error, "__cause__", None)):
        code = getattr(candidate, "sqlstate", None)
        if code:
            return str(code)
    return None


def is_statement_timeout(error: DBAPIError) -> bool:
    return _sqlstate(error.orig) == QUERY_CANCELED_SQLSTATE


async def database_error_handler(request: Request, exc: DBAPIError) -> JSONResponse:
    if not is_statement_timeout(exc):
        raise exc
    # Method and path only: never the SQL, its parameters or the query string.
    logger.warning("statement timeout on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Database took too long to answer. Try again."},
        headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
    )


def register_database_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DBAPIError, database_error_handler)
