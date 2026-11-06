"""HTML pages: sets index, report and cluster page (reusing ``report/assets``), triage, 404."""

from __future__ import annotations

import html
import re
from datetime import datetime
from importlib import resources
from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.responses import HTMLResponse, Response

from atlas_log_analyzer import __version__
from atlas_log_analyzer.report import asset_text, render_cluster_html, render_html
from atlas_log_analyzer.service import queries
from atlas_log_analyzer.service.errors import OpError
from atlas_log_analyzer.store.logset import LogSet
from atlas_log_analyzer.webapp import api
from atlas_log_analyzer.webapp.state import AppState, get_state

if TYPE_CHECKING:
    from atlas_log_analyzer.service.cluster import Capture

__all__ = ["index_rows", "not_found_page", "router", "templates"]

router = APIRouter(default_response_class=HTMLResponse)
StateDep = Annotated[AppState, Depends(get_state)]
templates = Jinja2Templates(
    directory=str(resources.files("atlas_log_analyzer.webapp") / "templates")
)
templates.env.globals["tool_version"] = __version__

REPORT_ASSETS = ("report.css", "report.js", "cluster.css", "cluster.js")


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


def _fmt_range(first: Any, last: Any) -> str:
    """``first → last`` to the minute; the end drops the year (and date) it shares."""
    a, b = _fmt_ts(first), _fmt_ts(last)
    if a == "-" or b == "-":
        return f"{a} → {b}"
    if a[:10] == b[:10]:
        return f"{a} → {b[11:]}"
    return f"{a} → {b[5:]}" if a[:4] == b[:4] else f"{a} → {b}"


_PLACEHOLDER = re.compile(r"&lt;([A-Z][A-Z0-9_]*|\*)&gt;")


def _fmt_template(value: Any) -> Markup:
    """Render a template as HTML, its ``<NUM>`` placeholders as chips (as in the report)."""
    text = html.escape(str(value or ""), quote=False)
    return Markup(_PLACEHOLDER.sub(lambda m: f'<span class="ph">{m[1].lower()}</span>', text))


templates.env.filters.update(
    num=_fmt_int, ts=_fmt_ts, size=_fmt_bytes, tpl=_fmt_template, range=_fmt_range
)


def index_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows of the sets index: a set, or a capture with its member sets under it.

    A capture row takes the place of its newest member (``items`` are newest first)
    and sums its members' counts; members are ordered by node.
    """
    members: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if item.get("capture"):
            members.setdefault(item["capture"], []).append(item)
    rows: list[dict[str, Any]] = []
    for item in items:
        capture = item.get("capture")
        if not capture:
            rows.append({"set": item})
            continue
        sets = members.pop(capture, None)
        if sets is None:
            continue  # its capture row is placed already
        sets.sort(key=lambda s: (s.get("node") or s.get("name") or s["id"], s["id"]))
        firsts = [s["first_ts"] for s in sets if s.get("first_ts")]
        lasts = [s["last_ts"] for s in sets if s.get("last_ts")]
        summaries = [s["summary"] for s in sets if s.get("summary")]
        clusters = sorted({s["cluster"] for s in sets if s.get("cluster")})
        rows.append(
            {
                "capture": {
                    "id": capture,
                    "cluster": ", ".join(clusters) or None,
                    "nodes": len(sets),
                    "first_ts": min(firsts, key=_instant) if firsts else None,
                    "last_ts": max(lasts, key=_instant) if lasts else None,
                    "entry_count": sum(int(s["entry_count"]) for s in sets),
                    "errors": sum(x["errors"] for x in summaries) if summaries else None,
                    "warnings": sum(x["warnings"] for x in summaries) if summaries else None,
                    "bytes": sum(s["bytes"] for s in sets),
                },
                "members": sets,
            }
        )
    return rows


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text)


@router.get("/")
def index(request: Request, state: StateDep) -> Response:
    """Render the sets index with upload form and per-set triage verdicts (loaded by the page).

    Sets of one capture are listed under a capture row (cluster, capture id, nodes, time
    range, the worst verdict of its members) that links to the capture page.
    """
    doc = queries.sets_doc(
        state,
        brief=False,
        store=state.store.root.name,
        summary=lambda m: state.summary(state.open(m.id)),
    )
    for item in doc["items"]:
        item["bytes"] = sum(int(s.get("size") or 0) for s in item.get("sources") or [])
    return templates.TemplateResponse(
        request,
        "index.html",
        {"sets": doc["items"], "rows": index_rows(doc["items"]), "store": doc["store"]},
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


def _capture_href(capture_id: str) -> str:
    return "/captures/" + quote(f"capture:{capture_id}", safe=":@")


def _node_switch(state: AppState, ls: LogSet) -> str:
    """Node switcher and cluster link of a set's report when the set belongs to a capture."""
    capture = ls.manifest.capture
    if capture is None:
        return ""
    members = [m for m in state.manifests() if m.capture == capture]
    members.sort(key=lambda m: (m.node or m.name or m.id, m.id))
    options = []
    for m in members:
        label = html.escape(m.node or m.name or m.id)
        selected = " selected" if m.id == ls.manifest.id else ""
        options.append(f'<option value="{m.id}"{selected}>{label}</option>')
    return (
        '<label class="shell-switch">Node <select id="shell-node">'
        + "".join(options)
        + "</select></label>"
        + f'<a href="{_capture_href(capture)}">Open cluster view</a>'
    )


def _capture_shell(capture: Capture) -> str:
    """Navigation bar of the cluster page: back to the sets, the capture, its node reports."""
    title = html.escape(capture.cluster or "Capture") + (
        f" · capture {html.escape(capture.id)}" if capture.id else ""
    )
    nodes = "".join(
        f'<a href="/sets/{quote(m.logset.id)}" title="Report of '
        f'{html.escape(m.logset.manifest.name or m.logset.id)}">{html.escape(m.node)}</a>'
        for m in capture.nodes
    )
    return (
        '<nav class="shell" aria-label="atlas-log">'
        '<a class="shell-home" href="/">atlas-log</a>'
        '<a href="/">Sets</a>'
        f'<span class="shell-capture">{title}</span>'
        f'<span class="shell-nodes">Node reports: {nodes}</span>'
        '<span class="shell-gap"></span>'
        '<a href="/#upload">Ingest</a><a href="/docs">API</a></nav>'
    )


_HEAD = (
    '<link rel="stylesheet" href="/static/webapp.css">'
    '<script defer src="/static/webapp.js"></script>'
)
_BODY = re.compile(r"<body[^>]*>")


def _with_shell(page: str, shell: str) -> str:
    """Add the web app's CSS/JS and navigation bar to a report page."""
    page = page.replace("</head>", _HEAD + "</head>", 1)
    return _BODY.sub(lambda m: m.group(0) + shell, page, count=1)


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
    shell = _shell(state, ls.manifest.id, _node_switch(state, ls) + triage)
    return HTMLResponse(_with_shell(page, shell))


@router.get("/captures/{ref}")
async def capture_page(
    ref: str, request: Request, state: StateDep, window: str | None = None
) -> Response:
    """Render the cluster page of a capture (``docs/cluster.md``, section 8).

    ``ref`` is a capture reference or a set (its capture). The page fetches its
    payload from ``/api/captures/{ref}/report``; ``window`` (``union``) passes through.
    """
    from atlas_log_analyzer.service.capture_queries import capture_from_ref

    try:
        capture = await run_in_threadpool(lambda: capture_from_ref(state, ref, front="api"))
    except OpError as exc:
        if exc.status == 404:
            return not_found_page(request, exc.message)
        raise
    target = capture.ref if capture.id is not None else capture.nodes[0].logset.id
    query = urlencode({"window": window}) if window else ""
    data_url = f"/api/captures/{quote(target, safe=':@')}/report" + (f"?{query}" if query else "")
    page = render_cluster_html(None, data_url=data_url, asset_base="/assets/report/")
    return HTMLResponse(_with_shell(page, _capture_shell(capture)))


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
