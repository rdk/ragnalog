"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from importlib import resources

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import RedirectResponse, Response

from atlas_log_analyzer import __version__
from atlas_log_analyzer.service import errors
from atlas_log_analyzer.service.errors import OpError
from atlas_log_analyzer.store.store import LogSetNotFound
from atlas_log_analyzer.webapp import api, openapi, pages
from atlas_log_analyzer.webapp.errors import error_response, install_handlers
from atlas_log_analyzer.webapp.responses import JSON
from atlas_log_analyzer.webapp.settings import Settings
from atlas_log_analyzer.webapp.state import AppState

__all__ = ["create_app"]


def create_app(settings: Settings | None = None, *, warm: bool = False) -> FastAPI:
    """Build the web app over the store in ``settings`` (default: from the environment).

    Routes: ``/`` sets index, ``/sets/{ref}`` interactive report, ``/sets/{ref}/triage``,
    ``/api/...`` JSON (OpenAPI at ``/docs``, with the document schemas). Errors are
    ``error.v1`` documents on ``/api`` and HTML pages elsewhere. With ``warm`` (``serve``)
    the newest capture's cluster page payload is built in the background at startup.
    """
    settings = settings or Settings.from_env()
    state = AppState(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        state.start()
        if warm:
            state.warm()
        try:
            yield
        finally:
            state.close()

    app = FastAPI(
        title="atlas-log",
        version=__version__,
        summary="Browse, compare and triage analysed log sets.",
        lifespan=lifespan,
        default_response_class=JSON,
    )
    app.state.atlas = state
    install_handlers(app, lambda r, err: pages.error_page(r, err) if _html(r) else None)
    upload_limit = settings.max_upload_mb * 1024 * 1024

    @app.middleware("http")
    async def _guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # before any route runs, so an upload body is not read when the token is wrong
        path = request.url.path
        if settings.token and not path.startswith(("/static/", "/assets/")):
            given = request.query_params.get("token")
            if given is not None and request.method == "GET" and not path.startswith("/api"):
                # a page opened with ?token=: keep it in a cookie and drop it from the URL
                response = RedirectResponse(str(request.url.remove_query_params("token")), 303)
                response.set_cookie(
                    api.TOKEN_COOKIE, given, httponly=True, samesite="strict", path="/"
                )
                return response
            if not api.token_ok(request):
                if _html(request):
                    return pages.unauthorized_page(request)
                return error_response(OpError("unauthorized", "missing or wrong API token"))
        if request.method == "POST" and path == "/api/ingest":
            length = request.headers.get("content-length", "")
            if length.isdigit() and int(length) > upload_limit + (1 << 20):
                message = f"upload exceeds {settings.max_upload_mb} MiB"
                return error_response(OpError("payload_too_large", message))
        return await call_next(request)

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
        hint = errors.hint("set_not_found", "api")
        return error_response(OpError.because("set_not_found", "api", message, hint=hint))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code == 404 and _html(request):
            return pages.not_found_page(request, f"There is nothing at {request.url.path}.")
        code = {404: "not_found", 405: "usage", 401: "unauthorized"}.get(exc.status_code, "http")
        return error_response(OpError(code, str(exc.detail)), exc.status_code)

    # large listings (groups?limit=0, entries) compress about tenfold; the report payloads
    # arrive gzipped already (responses.payload_response) and pass through
    app.add_middleware(GZipMiddleware, minimum_size=4096, compresslevel=5)
    app.include_router(api.router)
    app.include_router(openapi.router)  # GET /api/manifest
    app.include_router(pages.router)
    static = resources.files("atlas_log_analyzer.webapp") / "static"
    app.mount("/static", StaticFiles(directory=str(static)), name="static")
    openapi.install(app)  # document schemas in /openapi.json
    return app
