"""HTML pages: sets index, report and cluster page (reusing ``report/assets``), triage, 404."""

from __future__ import annotations

import hashlib
import html
import math
import os
import re
from datetime import UTC, datetime, timedelta
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.responses import HTMLResponse, Response

from ragnalog import __version__
from ragnalog.build import build
from ragnalog.report import asset_text, render_cluster_html, render_html
from ragnalog.report.payload import MAX_TIMELINE_HOURS, densest_hours
from ragnalog.report.render import asset_version, embed_json
from ragnalog.service import capture_queries, queries
from ragnalog.service.errors import OpError
from ragnalog.service.params import DEFAULT_MAX_RAW, EntryQuery, Page, Select, TextCap
from ragnalog.store.logset import LogSet
from ragnalog.webapp import api
from ragnalog.webapp.params import query_model
from ragnalog.webapp.state import AppState, get_state

if TYPE_CHECKING:
    from ragnalog.service.cluster import Capture

__all__ = [
    "error_page",
    "index_rows",
    "not_found_page",
    "router",
    "templates",
    "unauthorized_page",
]

router = APIRouter(default_response_class=HTMLResponse)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
StateDep = Annotated[AppState, Depends(get_state)]
EntryQ = Annotated[EntryQuery, Depends(query_model(EntryQuery))]
templates = Jinja2Templates(directory=str(resources.files("ragnalog.webapp") / "templates"))
templates.env.globals["tool_version"] = __version__
templates.env.globals["build"] = build


def _static_stamp() -> tuple[tuple[str, int], ...]:
    """Return the names and modification times of ``webapp/static``'s files."""
    static = resources.files("ragnalog.webapp") / "static"
    out = []
    for item in sorted(static.iterdir(), key=lambda p: p.name):
        if item.is_file():
            try:
                out.append((item.name, os.stat(str(item)).st_mtime_ns))
            except OSError:
                out.append((item.name, 0))
    return tuple(out)


@cache
def _asset_v_for(report_v: str, stamp: tuple[tuple[str, int], ...]) -> str:
    digest = hashlib.sha256(report_v.encode())
    static = resources.files("ragnalog.webapp") / "static"
    for name, _ in stamp:
        digest.update(name.encode() + static.joinpath(name).read_bytes())
    return digest.hexdigest()[:10]


def asset_v() -> str:
    """Return a short hash of the report assets and ``webapp/static``, their links' ``?v=``.

    It changes with any asset, so a browser never runs a cached script against a newer
    server; recomputed when a file changes (``serve --reload``).
    """
    return _asset_v_for(asset_version(), _static_stamp())


templates.env.globals["asset_v"] = asset_v

REPORT_ASSETS = (
    "report.css",
    "skins.css",
    "skins.js",
    "tips.js",
    "entry-card.js",
    "report.js",
    "cluster.css",
    "cluster.js",
)


def _fmt_int(value: Any) -> str:
    return f"{int(value):,}" if isinstance(value, int | float) else "-"


def _ts_text(value: Any) -> str:
    text = str(value or "")
    return text[:16].replace("T", " ") if text else "-"


def _fmt_ts(value: Any) -> Markup | str:
    """Return a UTC time to the minute as ``<time>``, so the page can show the viewer's zone."""
    text = _ts_text(value)
    if text == "-":
        return text
    return Markup(
        f'<time class="tz-ts" datetime="{html.escape(str(value))}">{html.escape(text)}</time>'
    )


def _fmt_bytes(value: Any) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _range_text(a: str, b: str) -> str:
    if a == "-" or b == "-":
        return f"{a} → {b}"
    if a[:10] == b[:10]:
        return f"{a} → {b[11:]}"
    return f"{a} → {b[5:]}" if a[:4] == b[:4] else f"{a} → {b}"


def _fmt_range(first: Any, last: Any) -> Markup:
    """``first → last`` (UTC) to the minute; the end drops the year (and date) it shares.

    The ISO ends ride along (``data-a``/``data-b``) so the page can show them in the
    viewer's time zone, the default unless UTC is chosen (``webapp.js``, as in the report).
    """
    a, b = _ts_text(first), _ts_text(last)
    text = html.escape(_range_text(a, b))
    if a == "-" or b == "-":
        return Markup(text)
    ends = f'data-a="{html.escape(str(first))}" data-b="{html.escape(str(last))}"'
    return Markup(f'<span class="tz-range" {ends}>{text}</span>')


_PLACEHOLDER = re.compile(r"&lt;([A-Z][A-Z0-9_]*|\*)&gt;")


def _fmt_template(value: Any) -> Markup:
    """Render a template as HTML, its ``<NUM>`` placeholders as chips (as in the report)."""
    text = html.escape(str(value or ""), quote=False)
    return Markup(_PLACEHOLDER.sub(lambda m: f'<span class="ph">{m[1].lower()}</span>', text))


templates.env.filters.update(
    num=_fmt_int, ts=_fmt_ts, size=_fmt_bytes, tpl=_fmt_template, range=_fmt_range
)


_STRIP_BUCKETS = 480
"""At most this many bars across the index's shared time axis (hours are merged above it)."""


def _bars(path: list[str], x: float, base: int, height: float, width: float = 1) -> None:
    """Append a bar from ``base`` (negative ``height`` grows up), one bucket wide by default."""
    if height:
        path.append(f"M{x:g} {base}h{width:g}v{height:.2f}h{-width:g}z")


def _scale(value: int, top: int) -> float:
    """``value`` against ``top`` on a square-root scale (0..1)."""
    return math.sqrt(value / top) if value > 0 and top > 0 else 0.0


def activity_strips(
    activity: dict[str, list[tuple[int, int, int]]],
) -> tuple[dict[str, Markup], dict[str, Any] | None]:
    """Inline SVG strips of the sets index, one per key, on one shared time axis.

    ``activity`` maps a row key (a set id, ``capture:<id>``) to its
    :func:`~ragnalog.service.views.set_activity` hours. Each strip spans the
    union of all hours: a faint band covers the key's own time range; grey bars grow up
    from its midline with the entries per bucket against the row's busiest bucket, red
    bars down with the errors against the row's most errors in a bucket (a steady error
    rate draws flat, a burst of errors stands out). A set much shorter than the axis gets
    wider bars so it does not vanish into a sliver. The axis is the densest
    :data:`~ragnalog.report.payload.MAX_TIMELINE_HOURS` of all hours. Returns the
    strips and the axis (``lo``/``hi`` epoch milliseconds, ``bucket`` hours) for the page
    script, or ``({}, None)`` without hours.
    """
    per_hour: dict[int, int] = {}
    for rows in activity.values():
        for h, n, _ in rows:
            per_hour[h] = per_hour.get(h, 0) + n
    if not per_hour:
        return {}, None
    # a few entries stamped years off would squeeze every strip into a sliver: densest span
    lo, hi = densest_hours(per_hour, MAX_TIMELINE_HOURS)
    activity = {k: [r for r in rows if lo <= r[0] <= hi] for k, rows in activity.items()}
    size = max(1, -(-(hi - lo + 1) // _STRIP_BUCKETS))
    count = (hi - lo) // size + 1
    binned: dict[str, dict[int, list[int]]] = {}
    for key, rows in activity.items():
        bins: dict[int, list[int]] = {}
        for h, n, err in rows:
            cell = bins.setdefault((h - lo) // size, [0, 0])
            cell[0] += n
            cell[1] += err
        binned[key] = bins
    strips: dict[str, Markup] = {}
    for key, bins in binned.items():
        if not bins:
            continue
        first, last = min(bins), max(bins)
        top = max(c[0] for c in bins.values()) or 1
        top_err = max(c[1] for c in bins.values())
        # under 1/40 of the axis: bars of count/120 buckets, centred on their bucket
        width = max(1.0, count / 120) if last - first + 1 < count / 40 else 1.0
        volume: list[str] = []
        errors: list[str] = []
        for i, (n, err) in sorted(bins.items()):
            x = min(max(0.0, i + 0.5 - width / 2), count - width) if width > 1 else i
            _bars(volume, x, 14, -13 * _scale(n, top), width)
            _bars(errors, x, 15, 12 * max(0.1, _scale(err, top_err)) if err else 0, width)
        data = ";".join(f"{i},{n},{err}" for i, (n, err) in sorted(bins.items()))
        strips[key] = Markup(
            f'<svg class="strip" viewBox="0 0 {count} 28" preserveAspectRatio="none"'
            f' data-bins="{data}" aria-hidden="true">'
            f'<rect class="strip-cov" x="{first}" y="0" width="{last - first + 1}" height="28"/>'
            f'<path class="strip-vol" d="{"".join(volume)}"/>'
            f'<path class="strip-err" d="{"".join(errors)}"/></svg>'
        )
    start = _EPOCH + timedelta(hours=lo)  # not fromtimestamp: OSError before 1970 on Windows
    end = _EPOCH + timedelta(hours=lo + count * size)
    short = "%H:%M" if end - start <= timedelta(days=1) else "%m-%d"
    axis = {
        "lo": lo * 3_600_000,
        "hi": (lo + count * size) * 3_600_000,
        "bucket": size,
        "lo_text": start.strftime(short),
        "hi_text": end.strftime(short),
    }
    return strips, axis


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


@router.head("/", include_in_schema=False)  # uptime and link checkers
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
    activity: dict[str, list[tuple[int, int, int]]] = {}
    for item in doc["items"]:
        item["bytes"] = sum(int(s.get("size") or 0) for s in item.get("sources") or [])
        hours = state.activity(state.open(item["id"]))
        activity[item["id"]] = hours
        if item.get("capture"):
            merged = activity.setdefault(f"capture:{item['capture']}", [])
            merged.extend(hours)
    strips, axis = activity_strips(activity)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "sets": doc["items"],
            "rows": index_rows(doc["items"]),
            "store": doc["store"],
            "strips": strips,
            "axis": axis,
            "unreadable": state.store.unreadable(),
        },
    )


FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" '
    'height="64" rx="14" fill="#1f2126"/><path d="M45.08 18.92A18.5 18.5 0 1 1 13.64 '
    '34.25" fill="none" stroke="#7d9fd8" stroke-width="6.5"/><polygon points="37.64,14.38 '
    "37.89,13.78 38.25,13.22 38.73,12.73 39.32,12.32 40.02,12 40.8,11.8 41.67,11.72 "
    "42.6,11.78 43.55,11.97 44.51,12.31 45.45,12.78 46.33,13.37 47.13,14.07 47.82,14.85 "
    "48.39,15.7 48.83,16.57 49.12,17.45 49.26,18.31 49.27,19.12 49.15,19.87 48.9,20.55 "
    "48.55,21.14 48.1,21.63 47.58,22.03 47.01,22.34 46.38,22.56 45.73,22.7 45.06,22.76 "
    "44.39,22.76 43.72,22.7 43.07,22.59 42.44,22.42 41.84,22.21 41.27,21.95 40.73,21.65 "
    "40.23,21.3 39.76,20.9 39.33,20.46 38.94,19.98 38.58,19.45 38.26,18.88 37.98,18.28 "
    '37.75,17.65 37.58,17 37.47,16.34 37.44,15.68 37.49,15.02" fill="#7d9fd8"/><polygon '
    'points="42.36,16.91 36.67,10.91 35.41,16.57" fill="#1f2126"/><polygon '
    'points="10.46,35.03 10.35,34.01 10.29,32.99 10.28,31.97 10.32,30.95 10.41,29.93 '
    "10.55,28.92 10.74,27.92 10.98,26.93 11.27,25.96 11.61,25 11.99,24.06 12.41,23.14 "
    "12.88,22.24 13.39,21.37 13.94,20.53 14.53,19.71 15.16,18.93 15.83,18.18 16.53,17.47 "
    "17.26,16.79 18.02,16.14 18.81,15.54 19.63,14.98 20.47,14.46 21.33,13.98 22.22,13.54 "
    "23.12,13.15 24.04,12.81 24.97,12.51 25.91,12.25 26.86,12.04 27.82,11.88 28.78,11.77 "
    "29.74,11.7 30.7,11.68 31.66,11.7 32.61,11.78 33.55,11.89 34.49,12.05 35.41,12.26 "
    "36.32,12.51 37.22,12.8 38.09,13.14 38.95,13.51 39.78,13.93 40.59,14.38 41.37,14.87 "
    "42.13,15.39 41.14,17.02 40.39,16.67 39.62,16.36 38.85,16.09 38.07,15.85 37.28,15.65 "
    "36.49,15.49 35.69,15.37 34.89,15.28 34.09,15.23 33.3,15.22 32.5,15.24 31.72,15.3 "
    "30.94,15.4 30.16,15.53 29.4,15.69 28.65,15.89 27.91,16.13 27.19,16.39 26.48,16.69 "
    "25.78,17.02 25.11,17.38 24.45,17.76 23.82,18.18 23.21,18.62 22.62,19.09 22.05,19.58 "
    "21.51,20.1 20.99,20.64 20.5,21.2 20.04,21.78 19.61,22.38 19.2,23 18.83,23.63 "
    "18.48,24.28 18.16,24.94 17.88,25.61 17.62,26.29 17.4,26.99 17.21,27.69 17.05,28.39 "
    "16.92,29.11 16.82,29.82 16.76,30.54 16.72,31.26 16.72,31.98 16.75,32.69 16.81,33.41 "
    '16.9,34.12" fill="#7d9fd8"/><circle cx="45.72" cy="16.37" r="1.15" fill="#1f2126"/>'
    '<rect x="23" y="23" width="14" height="4" rx="2" fill="#a3a29b"/><rect x="23" y="30" '
    'width="20" height="4" rx="2" fill="#f2b632"/><rect x="23" y="37" width="11" '
    'height="4" rx="2" fill="#e5605a"/></svg>'
)
"""The tab icon: the logo, Jormungandr round three log lines (an SVG, also at ``/favicon.ico``)."""


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    """Serve the tab icon (browsers ask for ``/favicon.ico`` on every page)."""
    return Response(FAVICON, media_type="image/svg+xml", headers={"Cache-Control": "max-age=86400"})


@router.get("/assets/report/{name}")
def report_asset(name: str, v: str = "") -> Response:
    """Serve the report's own CSS/JS so the web app and the static file share one code base.

    A URL with the current version (``?v=``, the pages' links) is cached for good: a new
    version has another URL. Without one, or with an older one, five minutes.
    """
    if name not in REPORT_ASSETS:
        return Response(f"no report asset {name!r}\n", 404, media_type="text/plain")
    media = "text/css" if name.endswith(".css") else "text/javascript"
    # for good only when ?v= is a current version (an old link gets five minutes)
    current = v in (asset_version(), asset_v())
    cache = "public, max-age=31536000, immutable" if current else "public, max-age=300"
    return Response(
        asset_text(name), media_type=f"{media}; charset=utf-8", headers={"Cache-Control": cache}
    )


_SEP = '<span class="crumb-sep" aria-hidden="true">/</span>'
_MENU = (
    '<details class="crumb-menu"><summary aria-label="ragnalog menu" '
    'title="Menu: all log sets, ingest new logs, the API">'
    '</summary><div class="crumb-pop">'
    '<a href="/" title="The start page: every stored log set and cluster capture">Log sets</a>'
    '<a href="/#upload" title="Upload log files or a ZIP to build a new log set">Ingest</a>'
    '<a href="/docs" title="The JSON API and its OpenAPI documentation, for scripts">API</a>'
    "</div></details>"
)
_HOME = '<a class="crumb-home" href="/">ragnalog</a>' + _MENU
templates.env.globals["crumb_root"] = Markup(_HOME)


def _crumbs(*parts: str, views: str = "") -> str:
    """Build the navigation row: ragnalog (with its menu), ``parts``, then the view tabs."""
    trail = '<div class="crumb-trail">' + _HOME + "".join(parts) + "</div>"
    end = f'<div class="crumb-end">{views}</div>' if views else ""
    return '<nav class="crumbs" aria-label="Breadcrumb">' + trail + end + "</nav>"


def _here(text: str, extra: str = "") -> str:
    return _SEP + f'<span class="crumb-here" aria-current="page">{text}{extra}</span>'


_ICONS = {
    "report": '<path d="M3.5 13V8.5M8 13V3M12.5 13V6"/>',
    "triage": '<path d="M4 14V2.5M4 3h8l-1.8 2.8L12 8.6H4"/>',
    "entries": '<path d="M6 4h7.5M6 8h7.5M6 12h7.5M2.5 4h.01M2.5 8h.01M2.5 12h.01"/>',
    "cluster": (
        '<circle cx="8" cy="3.8" r="1.7"/><circle cx="3.6" cy="12" r="1.7"/>'
        '<circle cx="12.4" cy="12" r="1.7"/>'
        '<path d="M7.1 5.3 4.5 10.5M8.9 5.3l2.6 5.2M5.3 12h5.4"/>'
    ),
    "compare": '<path d="M2.5 5.5h10M10 3l2.5 2.5L10 8M13.5 10.5h-10M6 8l-2.5 2.5L6 13"/>',
}
"""Stroke icons of the view tabs (16 x 16 viewBox, drawn by :func:`_views`)."""


_VIEW_TIPS = {
    "Report": "Every group of this set (entries that share one message template) with its "
    "count, the timeline, filters and a detail panel per group.",
    "Triage": "What needs attention first, against a baseline (by default the previous set "
    "of the same node): new error types, new exceptions, groups whose rate at least "
    "doubled, and groups that vanished.",
    "Entries": "The individual log entries in time order, with a histogram and filters by "
    "level, thread, user or text.",
    "Cluster view": "All nodes of the capture side by side: each group's count per node, "
    "which node logs it first or more often, and events across nodes.",
    "Compare with previous": "Adds the previous set of the same node to this report: "
    "which groups are new, grew, shrank or vanished, and by how much.",
    "Without compare": "Shows this set alone again, without the comparison.",
}
"""What each view tab opens (its rich tooltip), keyed by the tab's text."""


def _view_tip(text: str, current: bool) -> str:
    """Return the ``data-tip`` card of a view tab: its name, what it shows, "you are here"."""
    desc = _VIEW_TIPS.get(text, "")
    card = f'<div class="atip-h">{html.escape(text)}</div>'
    if desc:
        card += f'<div class="atip-body">{html.escape(desc)}</div>'
    if current:
        card += '<div class="atip-note">The page you are on.</div>'
    return html.escape(card, quote=True)


def _views(*items: tuple[str, str, str | None], label: str = "Views of this set") -> str:
    """Build the view tabs from ``(icon, text, href)``; no ``href`` marks the current one."""
    out = []
    for icon, text, href in items:
        body = (
            '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" '
            'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f'{_ICONS[icon]}</svg><span class="v-l">{text}</span>'
        )
        tip = f'aria-label="{text}" data-tip="{_view_tip(text, href is None)}"'
        if href is None:
            out.append(f'<span class="view" aria-current="page" {tip}>{body}</span>')
        else:
            out.append(f'<a class="view" href="{href}" {tip}>{body}</a>')
    return f'<nav class="views" aria-label="{label}">' + "".join(out) + "</nav>"


templates.env.globals["view_tabs"] = lambda *items, **kw: Markup(_views(*items, **kw))


def _set_crumb(state: AppState, ref_id: str) -> str:
    """Build the set switcher crumb: a dropdown of every set, the current one selected."""
    options = []
    for m in state.manifests():
        label = html.escape(m.name or m.id)
        selected = " selected" if m.id == ref_id else ""
        options.append(f'<option value="{m.id}"{selected}>{label}</option>')
    return (
        _SEP
        + '<span class="crumb-sel"><select id="shell-set" aria-label="Log set" '
        + 'title="Switch to another log set; the filters in the address stay">'
        + "".join(options)
        + "</select></span>"
    )


def _capture_href(capture_id: str) -> str:
    return "/captures/" + quote(f"capture:{capture_id}", safe=":@")


def _capture_url(capture: Capture) -> str:
    """Return the cluster page address (the base of its entries page)."""
    if capture.id is not None:
        return _capture_href(capture.id)
    return f"/captures/{quote(capture.nodes[0].logset.id)}"


def _node_switch(state: AppState, ls: LogSet) -> str:
    """Node crumb (a switcher) of a set's report when the set belongs to a capture."""
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
        _SEP
        + '<span class="crumb-sel"><select id="shell-node" aria-label="Cluster node" '
        + 'title="Switch to the same view of another node of this capture">'
        + "".join(options)
        + "</select></span>"
    )


def _node_link(i: int, node: str, set_id: str, name: str) -> str:
    """Chip linking a node's report: a dot in the node's cluster page colour and its name."""
    colour = f"nc{i}" if i < 8 else "ncx"
    return (
        f'<a class="node-link" href="/sets/{quote(set_id)}" '
        f'title="Report of node {html.escape(name)}: its own log set">'
        f'<span class="nd {colour}"></span>{html.escape(node)}</a>'
    )


def _capture_shell(capture: Capture) -> str:
    """Navigation row of the cluster page: a framed group linking each node's report, the views."""
    name = html.escape(capture.cluster or "Capture")
    full = name + (f" · capture {html.escape(capture.id)}" if capture.id else "")
    base = _capture_url(capture)
    nodes = "".join(
        _node_link(i, m.node, m.logset.id, m.logset.manifest.name or m.logset.id)
        for i, m in enumerate(capture.nodes)
    )
    views = _views(
        ("cluster", "Cluster view", None),
        ("entries", "Entries", base + "/entries"),
        label="Views of this capture",
    )
    if nodes:
        views = (
            '<nav class="node-links" aria-label="Node reports">'
            f'<span class="views-label">Nodes</span>{nodes}</nav>' + views
        )
    crumb = _SEP + f'<span class="crumb-here" aria-current="page" title="{full}">{name}</span>'
    return _crumbs(crumb, views=views)


def _head() -> str:
    """Return the web app's own head tags on the report and cluster pages."""
    v = asset_v()
    b = build()
    # the build, for the pages' footers (their scripts add it next to the version)
    meta = (
        f'<meta name="ragnalog-build" content="{html.escape(b.label())}"'
        f' data-title="{html.escape(b.title())}">'
        if b.label()
        else ""
    )
    return (
        meta + '<link rel="icon" href="/favicon.ico" type="image/svg+xml">'
        f'<link rel="stylesheet" href="/static/webapp.css?v={v}">'
        f'<script defer src="/static/webapp.js?v={v}"></script>'
    )


_BODY = re.compile(r"<body[^>]*>")


def _with_shell(page: str, crumbs: str) -> str:
    """Add the web app's CSS/JS and the navigation row (crumbs, view tabs) to a report page."""
    page = page.replace("</head>", _head() + "</head>", 1)
    return _BODY.sub(lambda m: m.group(0) + crumbs, page, count=1)


@router.head("/sets/{ref}", include_in_schema=False)  # uptime and link checkers
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
    examples = max(0, min(examples, 50))  # the report API's own bounds
    query = {"compare": compare, "min_level": min_level, "examples": examples}
    data_url = f"/api/sets/{ls.manifest.id}/report?" + urlencode(
        {k: v for k, v in query.items() if v not in (None, "")}
    )
    page = render_html(
        None,
        data_url=data_url,
        inline_assets=False,
        asset_base="/assets/report/",
        name=ls.manifest.name or ls.manifest.id,
    )
    base = None if compare in (None, "", "none") else compare
    sid = ls.manifest.id
    views = _views(
        ("report", "Report", None),
        ("triage", "Triage", f"/sets/{sid}/triage"),
        ("entries", "Entries", f"/sets/{sid}/entries"),
        *(
            (("cluster", "Cluster view", _capture_href(ls.manifest.capture)),)
            if ls.manifest.capture
            else ()
        ),
        *(
            (("compare", "Without compare", f"/sets/{sid}"),)
            if base
            else (("compare", "Compare with previous", f"/sets/{sid}?compare=auto"),)
        ),
    )
    # the switchers read every manifest (a stat each): off the event loop, as the open above
    crumbs = await run_in_threadpool(
        lambda: _crumbs(
            _set_crumb(state, sid), _node_switch(state, ls), _here("Report"), views=views
        )
    )
    return HTMLResponse(_with_shell(page, crumbs))


def _overlaps(capture: Capture) -> bool:
    """Whether the default ``overlap`` window of ``capture`` is not empty."""
    from ragnalog.service.cluster import resolve_window

    try:
        resolve_window(capture, "overlap")
    except OpError:
        return False
    return True


@router.head("/captures/{ref}", include_in_schema=False)  # uptime and link checkers
@router.get("/captures/{ref}")
async def capture_page(
    ref: str, request: Request, state: StateDep, window: str | None = None
) -> Response:
    """Render the cluster page of a capture (``docs/cluster.md``, section 8).

    ``ref`` is a capture reference or a set (its capture). The page fetches its
    payload from ``/api/captures/{ref}/report``; ``window`` (``union``) passes through.
    """
    from ragnalog.service.capture_queries import capture_from_ref

    try:
        capture = await run_in_threadpool(lambda: capture_from_ref(state, ref, front="api"))
    except OpError as exc:
        if exc.status == 404:
            return not_found_page(request, exc.message)
        raise
    target = capture.ref if capture.id is not None else capture.nodes[0].logset.id
    if window is None and not await run_in_threadpool(_overlaps, capture):
        window = "union"  # the nodes' logs do not overlap: show them all, not an error
    query = urlencode({"window": window}) if window else ""
    data_url = f"/api/captures/{quote(target, safe=':@')}/report" + (f"?{query}" if query else "")
    page = render_cluster_html(
        None, data_url=data_url, asset_base="/assets/report/", name=capture.cluster or capture.ref
    )
    return HTMLResponse(_with_shell(page, _capture_shell(capture)))


@router.head("/sets/{ref}/triage", include_in_schema=False)  # uptime and link checkers
@router.get("/sets/{ref}/triage")
async def triage_page(
    ref: str, request: Request, state: StateDep, baseline: str = "auto", min_level: str = "WARN"
) -> Response:
    """Triage view: new error types first, then growth and vanished groups."""
    try:
        doc = await run_in_threadpool(
            lambda: api.triage(ref, state, baseline=baseline, min_level=min_level, limit=0)
        )
    except OpError as exc:  # an HTML page, not the API's error.v1
        if exc.status == 404:
            return not_found_page(request, exc.message)
        return templates.TemplateResponse(
            request,
            "404.html",
            {"title": "Cannot triage", "message": exc.message, "back": f"/sets/{ref}/triage"},
            status_code=exc.status,
        )
    listed = await run_in_threadpool(state.manifests)
    sets = [m for m in listed if m.id != doc["set"]["id"]]
    return templates.TemplateResponse(
        request,
        "triage.html",
        {"t": doc, "sets": sets, "baseline_param": baseline, "min_level": min_level},
    )


ENTRIES_PAGE = 100
"""Rows the entries page loads at a time (and embeds as its first page)."""


def _entries_page(
    request: Request,
    doc: dict[str, Any],
    *,
    kind: str,
    name: str,
    ref: str,
    api_url: str,
    **more: Any,
) -> Response:
    """Render ``entries.html`` around the first page ``doc`` (``entries.v1``) of the query."""
    page = {"kind": kind, "api": api_url, "limit": ENTRIES_PAGE, "ref": ref, "name": name, **more}
    return templates.TemplateResponse(
        request,
        "entries.html",
        {"name": name, "page": page, "first": Markup(embed_json(doc)), "kind": kind, "ref": ref},
    )


@router.head("/sets/{ref}/entries", include_in_schema=False)  # uptime and link checkers
@router.get("/sets/{ref}/entries")
async def set_entries_page(ref: str, request: Request, state: StateDep, q: EntryQ) -> Response:
    """Table of the entries of a set, filtered by the query (``signature``, ``since``, ...).

    The filters are those of ``GET /api/sets/{ref}/entries``; the page embeds the first
    :data:`ENTRIES_PAGE` rows (so a bad filter is an error page, not an empty table) and
    loads further rows from that API.
    """

    def build() -> tuple[dict[str, Any], LogSet]:
        cap = TextCap.parse(None, default=DEFAULT_MAX_RAW, front="api")
        doc = queries.entries(state, ref, q, Page(ENTRIES_PAGE, 0), Select(), cap, front="api")
        return doc, state.open(ref)

    doc, ls = await run_in_threadpool(build)
    sid = ls.manifest.id
    return _entries_page(
        request,
        doc,
        kind="set",
        name=ls.manifest.name or sid,
        ref=sid,
        api_url=f"/api/sets/{sid}/entries",
        back=f"/sets/{sid}",
        nodes={},
    )


@router.get("/sets/{ref}/entries/{entry_id}/raw")
async def entry_raw_page(ref: str, entry_id: str, request: Request, state: StateDep) -> Response:
    """One entry's exact text (banner lines first) as a page: the stack trace highlighted.

    The cards' "Open raw" link: application frames marked, library frames dimmed (all of
    them shown), as in the cards; the plain text stays at ``GET /api/sets/{ref}/entries/
    {entry_id}/raw``.
    """

    def build() -> tuple[tuple[str | None, str], LogSet]:
        return queries.entry_parts(state, ref, entry_id, front="api"), state.open(ref)

    (preamble, raw), ls = await run_in_threadpool(build)
    sid = ls.manifest.id
    plain = f"/api/sets/{quote(sid)}/entries/{quote(entry_id)}/raw"
    data = {"id": entry_id, "raw": raw, "preamble": preamble, "plain": plain}
    return templates.TemplateResponse(
        request,
        "entry_raw.html",
        {
            "name": ls.manifest.name or sid,
            "sid": sid,
            "entry_id": entry_id,
            "plain": plain,
            "data": Markup(embed_json(data)),
        },
    )


@router.head("/captures/{ref}/entries", include_in_schema=False)  # uptime and link checkers
@router.get("/captures/{ref}/entries")
async def capture_entries_page(ref: str, request: Request, state: StateDep, q: EntryQ) -> Response:
    """Table of the entries of a capture's nodes interleaved by time (as the set's page).

    Rows carry their node; the window is the union of the nodes' logs unless ``since`` /
    ``until`` give one. ``signature`` may be ``NODE:SIGNATURE`` and ``node`` may name nodes.
    """

    def build() -> tuple[dict[str, Any], Capture]:
        cap = TextCap.parse(None, default=DEFAULT_MAX_RAW, front="api")
        doc = capture_queries.entries(
            state, ref, q, Page(ENTRIES_PAGE, 0), Select(), cap, window="union", front="api"
        )
        return doc, capture_queries.capture_from_ref(state, ref, front="api")

    doc, capture = await run_in_threadpool(build)
    target = capture.ref if capture.id is not None else capture.nodes[0].logset.id
    return _entries_page(
        request,
        doc,
        kind="capture",
        name=capture.cluster or capture.ref,
        ref=target,
        api_url=f"/api/captures/{quote(target, safe=':@')}/entries",
        back=_capture_url(capture),
        nodes={m.node: m.logset.id for m in capture.nodes},
    )


def unauthorized_page(request: Request) -> Response:
    """Render the HTML 401 page of a server started with ``--token``."""
    return templates.TemplateResponse(request, "401.html", {}, status_code=401)


def not_found_page(request: Request, message: str) -> Response:
    """Render the HTML 404 page."""
    return templates.TemplateResponse(request, "404.html", {"message": message}, status_code=404)


_ERROR_TITLES = {404: "Not found", 409: "Ambiguous reference", 500: "Something went wrong"}


def error_page(request: Request, err: OpError) -> Response:
    """Render an :class:`OpError` of a page route as HTML, with its status and hint."""
    title = _ERROR_TITLES.get(err.status, "Cannot show this page")
    return templates.TemplateResponse(
        request,
        "404.html",
        {"title": title, "message": err.message, "hint": err.hint},
        status_code=err.status,
    )
