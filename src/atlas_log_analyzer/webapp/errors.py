"""``error.v1`` responses shared by every route (:mod:`atlas_log_analyzer.service.errors`)."""

from __future__ import annotations

from collections.abc import Callable
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


type ErrorPage = Callable[[Request, OpError], Response | None]
"""Renders the error of a page route as HTML; ``None`` for an API request (``error.v1``)."""


def install_handlers(app: FastAPI, page: ErrorPage | None = None) -> None:
    """Map :class:`OpError`, library and validation exceptions to ``error.v1`` responses.

    Status codes come from :data:`~atlas_log_analyzer.service.errors.SPEC`. ``page``
    answers a browser's page request with HTML instead. ``LogSetNotFound`` and HTTP
    errors are handled in :func:`create_app` (HTML or JSON).
    """

    def respond(request: Request, err: OpError) -> Response:
        html = page(request, err) if page is not None else None
        return html if html is not None else error_response(err)

    @app.exception_handler(OpError)
    async def _op(request: Request, exc: OpError) -> Response:
        return respond(request, exc)

    @app.exception_handler(AmbiguousSignature)
    @app.exception_handler(AmbiguousLogSet)
    @app.exception_handler(StoreError)
    async def _library(request: Request, exc: Exception) -> Response:
        err = from_exception(exc, "api")
        return respond(request, err if err is not None else OpError("internal_error", str(exc)))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> Response:
        # still logged by the server; the client gets error.v1 like the CLI's internal_error
        return respond(request, OpError("internal_error", f"{type(exc).__name__}: {exc}"))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> Response:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p not in ("query", "body"))
        message = f"{where}: {first.get('msg', 'invalid request')}"
        return respond(request, OpError("usage", message))
