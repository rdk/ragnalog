"""``error.v1`` responses shared by every route (same codes as the CLI)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import Response

from atlas_log_analyzer.store.store import AmbiguousLogSet, StoreError
from atlas_log_analyzer.webapp.responses import JSON

__all__ = ["ApiError", "error_doc", "install_handlers"]


class ApiError(Exception):
    """An error with an ``error.v1`` code, an HTTP status and an optional next-step hint."""

    def __init__(self, code: str, message: str, status: int = 400, hint: str | None = None) -> None:
        """Create the error."""
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.hint = hint


def error_doc(code: str, message: str, hint: str | None = None) -> dict[str, Any]:
    """Build the ``error.v1`` document."""
    error: dict[str, Any] = {"code": code, "message": message}
    if hint:
        error["hint"] = hint
    return {"schema": "error.v1", "error": error}


def _respond(status: int, code: str, message: str, hint: str | None = None) -> Response:
    return JSON(error_doc(code, message, hint), status_code=status)


def install_handlers(app: FastAPI) -> None:
    """Map library and validation exceptions to ``error.v1`` responses.

    ``LogSetNotFound`` and HTTP errors are handled in :func:`create_app` (HTML or JSON).
    """

    @app.exception_handler(ApiError)
    async def _api(_: Request, exc: ApiError) -> Response:
        return _respond(exc.status, exc.code, exc.message, exc.hint)

    @app.exception_handler(AmbiguousLogSet)
    async def _ambiguous(_: Request, exc: AmbiguousLogSet) -> Response:
        return _respond(409, "ambiguous", str(exc))

    @app.exception_handler(StoreError)
    async def _store(_: Request, exc: StoreError) -> Response:
        return _respond(500, "store_error", str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> Response:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p not in ("query", "body"))
        return _respond(400, "usage", f"{where}: {first.get('msg', 'invalid request')}")
