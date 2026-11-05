"""HTML pages: sets index, report (reusing ``report/assets``), triage, 404."""

from __future__ import annotations

import html
from importlib import resources
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.templating import Jinja2Templates
from starlette.responses import HTMLResponse, Response

from atlas_log_analyzer import __version__
from atlas_log_analyzer.report import asset_text, render_html
from atlas_log_analyzer.service import queries
from atlas_log_analyzer.webapp import api
from atlas_log_analyzer.webapp.state import AppState, get_state

__all__ = ["not_found_page", "router", "templates"]

router = APIRouter(default_response_class=HTMLResponse)
StateDep = Annotated[AppState, Depends(get_state)]
templates = Jinja2Templates(
    directory=str(resources.files("atlas_log_analyzer.webapp") / "templates")
)
templates.env.globals["tool_version"] = __version__

REPORT_ASSETS = ("report.css", "report.js")


def _fmt_int(value: Any) -> str:
    return f"{int(value):,}" if isinstance(value, int | float) else "-"


def _fmt_ts(value: Any) -> str:
    text = str(value or "")
    return text[:16].replace("T", " ") if text else "-"


def _fmt_bytes(value: Any) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


templates.env.filters.update(num=_fmt_int, ts=_fmt_ts, size=_fmt_bytes)


@router.get("/")
def index(request: Request, state: StateDep) -> Response:
    """Render the sets index with upload form and per-set triage verdicts (loaded by the page)."""
    doc = queries.sets_doc(
        state,
        brief=False,
        store=state.store.root.name,
        summary=lambda m: state.summary(state.open(m.id)),
    )
    for item in doc["items"]:
        item["bytes"] = sum(int(s.get("size") or 0) for s in item.get("sources") or [])
    return templates.TemplateResponse(
        request, "index.html", {"sets": doc["items"], "store": doc["store"]}
    )


@router.get("/assets/report/{name}")
def report_asset(name: str) -> Response:
    """Serve the report's own CSS/JS so the web app and the static file share one code base."""
    if name not in REPORT_ASSETS:
        return Response(status_code=404)
    media = "text/css" if name.endswith(".css") else "text/javascript"
    return Response(
        asset_text(name),
        media_type=f"{media}; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )


def _shell(state: AppState, ref_id: str, extra: str = "") -> str:
    options = []
    for m in state.manifests():
        label = html.escape(m.name or m.id)
        selected = " selected" if m.id == ref_id else ""
        options.append(f'<option value="{m.id}"{selected}>{label}</option>')
    return (
        '<nav class="shell" aria-label="atlas-log">'
        '<a class="shell-home" href="/">atlas-log</a>'
        '<label class="shell-switch">Set <select id="shell-set">'
        + "".join(options)
        + "</select></label>"
        + extra
        + '<span class="shell-gap"></span>'
        '<a href="/#upload">Ingest</a><a href="/docs">API</a></nav>'
    )


@router.get("/sets/{ref}")
async def report_page(
    ref: str,
    request: Request,
    state: StateDep,
    compare: str | None = None,
    min_level: str | None = None,
    examples: int = 3,
) -> Response:
    """Render the interactive report.

    The page fetches its payload from ``/api/sets/{id}/report``.
    """
    ls = await run_in_threadpool(state.open, ref)
    query = {"compare": compare, "min_level": min_level, "examples": examples}
    data_url = f"/api/sets/{ls.manifest.id}/report?" + urlencode(
        {k: v for k, v in query.items() if v not in (None, "")}
    )
    page = render_html(None, data_url=data_url, inline_assets=False, asset_base="/assets/report/")
    base = None if compare in (None, "", "none") else compare
    triage = f'<a href="/sets/{ls.manifest.id}/triage">Triage</a>' + (
        f'<a href="/sets/{ls.manifest.id}">Without compare</a>'
        if base
        else f'<a href="/sets/{ls.manifest.id}?compare=auto">Compare with previous</a>'
    )
    head = (
        '<link rel="stylesheet" href="/static/webapp.css">'
        '<script defer src="/static/webapp.js"></script>'
    )
    page = page.replace("</head>", head + "</head>", 1)
    page = page.replace("<body>", "<body>" + _shell(state, ls.manifest.id, triage), 1)
    return HTMLResponse(page)


@router.get("/sets/{ref}/triage")
async def triage_page(
    ref: str, request: Request, state: StateDep, baseline: str = "auto", min_level: str = "WARN"
) -> Response:
    """Triage view: new error types first, then growth and vanished groups."""
    doc = await run_in_threadpool(
        lambda: api.triage(ref, state, baseline=baseline, min_level=min_level, limit=0)
    )
    sets = [m for m in state.manifests() if m.id != doc["set"]["id"]]
    return templates.TemplateResponse(
        request,
        "triage.html",
        {"t": doc, "sets": sets, "baseline_param": baseline, "min_level": min_level},
    )


def not_found_page(request: Request, message: str) -> Response:
    """Render the HTML 404 page."""
    return templates.TemplateResponse(request, "404.html", {"message": message}, status_code=404)
