"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import resources

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from atlas_log_analyzer import __version__
from atlas_log_analyzer.store.store import LogSetNotFound
from atlas_log_analyzer.webapp import api, pages
from atlas_log_analyzer.webapp.errors import error_doc, install_handlers
from atlas_log_analyzer.webapp.responses import JSON
from atlas_log_analyzer.webapp.settings import Settings
from atlas_log_analyzer.webapp.state import AppState

__all__ = ["create_app"]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the web app over the store in ``settings`` (default: from the environment).

    Routes: ``/`` sets index, ``/sets/{ref}`` interactive report, ``/sets/{ref}/triage``,
    ``/api/...`` JSON (OpenAPI at ``/docs``). Errors are ``error.v1`` documents on
    ``/api`` and HTML pages elsewhere.
    """
    settings = settings or Settings.from_env()
    state = AppState(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        state.jobs.shutdown()

    app = FastAPI(
        title="atlas-log",
        version=__version__,
        summary="Browse, compare and triage analysed log sets.",
        lifespan=lifespan,
        default_response_class=JSON,
    )
    app.state.atlas = state
    install_handlers(app)

    def _html(request: Request) -> bool:
        return not request.url.path.startswith("/api") and "text/html" in request.headers.get(
            "accept", "text/html"
        )

    @app.exception_handler(LogSetNotFound)
    async def _missing(request: Request, exc: LogSetNotFound) -> Response:
        # the store's message ends with its directory; keep server paths out of responses
        message = str(exc).split(f" in {settings.store}", 1)[0]
        if _html(request):
            return pages.not_found_page(request, message)
        return JSON(error_doc("not_found", message, "list the sets with GET /api/sets"), 404)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code == 404 and _html(request):
            return pages.not_found_page(request, f"There is nothing at {request.url.path}.")
        code = {404: "not_found", 405: "usage", 401: "unauthorized"}.get(exc.status_code, "http")
        return JSON(error_doc(code, str(exc.detail)), exc.status_code)

    app.include_router(api.router)
    app.include_router(pages.router)
    static = resources.files("atlas_log_analyzer.webapp") / "static"
    app.mount("/static", StaticFiles(directory=str(static)), name="static")
    return app
