"""``error.v1`` responses shared by every route (:mod:`atlas_log_analyzer.service.errors`)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import Response

from atlas_log_analyzer.service.errors import OpError, from_exception
from atlas_log_analyzer.service.views import AmbiguousSignature
from atlas_log_analyzer.store.store import AmbiguousLogSet, StoreError
from atlas_log_analyzer.webapp.responses import JSON

__all__ = ["error_doc", "error_response", "install_handlers"]


def error_doc(
    code: str, message: str, hint: str | None = None, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build the ``error.v1`` document."""
    return OpError(code, message, hint=hint, extra=extra).envelope()


def error_response(err: OpError, status: int | None = None) -> Response:
    """Return the ``error.v1`` response of ``err`` (its own HTTP status unless ``status``)."""
    return JSON(err.envelope(), status_code=status or err.status)


def install_handlers(app: FastAPI) -> None:
    """Map :class:`OpError`, library and validation exceptions to ``error.v1`` responses.

    Status codes come from :data:`~atlas_log_analyzer.service.errors.SPEC`.
    ``LogSetNotFound`` and HTTP errors are handled in :func:`create_app` (HTML or JSON).
    """

    @app.exception_handler(OpError)
    async def _op(_: Request, exc: OpError) -> Response:
        return error_response(exc)

    @app.exception_handler(AmbiguousSignature)
    @app.exception_handler(AmbiguousLogSet)
    @app.exception_handler(StoreError)
    async def _library(_: Request, exc: Exception) -> Response:
        err = from_exception(exc, "api")
        return error_response(err if err is not None else OpError("internal_error", str(exc)))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> Response:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p not in ("query", "body"))
        return error_response(OpError("usage", f"{where}: {first.get('msg', 'invalid request')}"))
