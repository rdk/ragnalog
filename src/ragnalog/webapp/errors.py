"""``error.v1`` responses shared by every route (:mod:`ragnalog.service.errors`)."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import Response

from ragnalog.service.errors import OpError, from_exception
from ragnalog.service.params import MAX_COUNT
from ragnalog.service.views import AmbiguousSignature
from ragnalog.store.store import AmbiguousLogSet, StoreError
from ragnalog.webapp.responses import JSON

__all__ = ["error_doc", "error_response", "install_handlers", "scrub_paths"]

_ABS_PATH = re.compile(r"(?<![\w:/\\.])(?:[A-Za-z]:)?(?:[\\/]+[^\\/\s'\"]+)*[\\/]+([^\\/\s'\"]+)")
"""An absolute path (drive, UNC or POSIX, not inside a URL); its last part is kept."""


def scrub_paths(text: str) -> str:
    r"""``text`` with absolute file paths reduced to their file names (no server paths leak).

    Examples:
        >>> scrub_paths(r"cannot read C:\srv\store\.uploads\j1\bad.log: x")
        'cannot read bad.log: x'
        >>> scrub_paths("Permission denied: '/srv/store/.spool/1/x.log'")
        "Permission denied: 'x.log'"
        >>> scrub_paths("see http://host/a/b and a/b")
        'see http://host/a/b and a/b'

    """
    return _ABS_PATH.sub(r"\1", text)


_RANGE_ERRORS = frozenset({"greater_than", "greater_than_equal"})
"""Pydantic error types of a number below its lower bound (``invalid_value``, as in the CLI)."""


def _validation_code(error: Mapping[str, Any]) -> str:
    """``invalid_value`` for a number below its bound, as the CLI reports it; else ``usage``.

    A number past :data:`~ragnalog.service.params.MAX_COUNT` is ``invalid_value``
    too. Page caps stay ``usage`` (the CLI has none): a smaller upper bound, and
    ``limit=0``, which means "all" in the CLI but is below a page's ``limit`` of 1 here.
    """
    if error.get("type") in ("less_than", "less_than_equal"):
        # past what the store can count (MAX_COUNT), as the CLI says; a page cap is usage
        bound = (error.get("ctx") or {}).get("le", (error.get("ctx") or {}).get("lt"))
        return "invalid_value" if bound is not None and bound >= MAX_COUNT else "usage"
    if error.get("type") not in _RANGE_ERRORS:
        return "usage"
    loc = tuple(error.get("loc", ()))
    if loc[-1:] == ("limit",) and str(error.get("input", "")).strip() == "0":
        return "usage"
    return "invalid_value"


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

    Status codes come from :data:`~ragnalog.service.errors.SPEC`. ``page``
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
        return respond(
            request, OpError("internal_error", scrub_paths(f"{type(exc).__name__}: {exc}"))
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> Response:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p not in ("query", "body"))
        message = f"{where}: {first.get('msg', 'invalid request')}"
        return respond(request, OpError(_validation_code(first), message))
