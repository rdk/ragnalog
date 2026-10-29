"""Render a ``report.v1`` payload into an HTML page.

``template.html`` carries four placeholders that are replaced exactly once:
``__REPORT_TITLE__`` (HTML-escaped), ``/*__REPORT_CSS__*/``, ``/*__REPORT_JS__*/``
and ``__REPORT_DATA__`` (the payload JSON inside
``<script type="application/json" id="report-data">``).

By default the page is self-contained (assets inlined, payload embedded). The web app
instead renders a shell: ``payload=None`` with ``data_url`` (the page fetches the
payload from ``<html data-report-url>``) and ``inline_assets=False`` (``<link>`` /
``<script src>`` under ``asset_base``, served from :func:`asset_text`).
"""

from __future__ import annotations

import html
import json
import re
from importlib.resources import files
from typing import Any

__all__ = ["asset_text", "embed_json", "render_html"]

PACKAGE = "atlas_log_analyzer.report"
TITLE = "__REPORT_TITLE__"
CSS = "/*__REPORT_CSS__*/"
JS = "/*__REPORT_JS__*/"
DATA = "__REPORT_DATA__"
ASSETS = ("template.html", "report.css", "report.js")

_CSS_BLOCK = re.compile(r"<style>\s*/\*__REPORT_CSS__\*/\s*</style>")
_JS_BLOCK = re.compile(r"<script>\s*/\*__REPORT_JS__\*/\s*</script>")


def asset_text(name: str) -> str:
    """Return the text of a report asset (``template.html``, ``report.css`` or ``report.js``).

    Raises:
        ValueError: ``name`` is not one of the report assets.

    """
    if name not in ASSETS:
        raise ValueError(f"unknown report asset: {name!r}")
    return files(PACKAGE).joinpath("assets", name).read_text(encoding="utf-8")


def embed_json(payload: Any) -> str:
    r"""Compact JSON that is safe inside a ``<script>`` element.

    ``</`` becomes ``<\\/`` and ``<!--`` becomes ``\\u003c!--`` (both are valid JSON
    string escapes, and ``<`` can only occur inside strings), so neither a
    ``</script>`` nor an HTML comment opener in log text can end or alter the element.
    """
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return text.replace("<!--", "\\u003c!--").replace("</", "<\\/")


def _title(payload: dict[str, Any] | None) -> str:
    logset = (payload or {}).get("logset") or {}
    name = logset.get("name") or logset.get("id") or "log set"
    return f"{name} · atlas-log report"


def _replace_once(text: str, marker: str, value: str) -> str:
    before, found, after = text.partition(marker)
    if not found:
        raise ValueError(f"report template has no {marker} placeholder")
    return before + value + after


def _sub_once(text: str, pattern: re.Pattern[str], value: str) -> str:
    out, n = pattern.subn(lambda _m: value, text, count=1)
    if n != 1:
        raise ValueError(f"report template has no {pattern.pattern} block")
    return out


def render_html(
    payload: dict[str, Any] | None = None,
    *,
    data_url: str | None = None,
    inline_assets: bool = True,
    asset_base: str = "/static/",
) -> str:
    """Return the report page.

    Args:
        payload: The ``report.v1`` payload to embed; ``None`` embeds an empty data element
            (requires ``data_url``).
        data_url: URL the page fetches the payload from; set as ``data-report-url`` on
            the ``<html>`` element.
        inline_assets: Inline ``report.css``/``report.js``; ``False`` links them as
            ``{asset_base}report.css`` and ``{asset_base}report.js``.
        asset_base: URL prefix of the assets when not inlined.

    Raises:
        ValueError: Neither ``payload`` nor ``data_url`` given, or a broken template/asset.

    """
    if payload is None and data_url is None:
        raise ValueError("render_html needs a payload or a data_url")
    page = asset_text("template.html")
    if data_url is not None:
        attr = f' data-report-url="{html.escape(data_url, quote=True)}"'
        page = _replace_once(page, "<html", "<html" + attr)
    page = _replace_once(page, TITLE, html.escape(_title(payload)))
    if inline_assets:
        css, js = asset_text("report.css"), asset_text("report.js")
        for name, text in (("report.css", css), ("report.js", js)):
            if "</" + "script" in text.lower() or "</style" in text.lower():
                raise ValueError(f"{name} must not contain a closing script/style tag")
        page = _replace_once(page, CSS, css)
        page = _replace_once(page, JS, js)
    else:
        base = html.escape(asset_base, quote=True)
        page = _sub_once(page, _CSS_BLOCK, f'<link rel="stylesheet" href="{base}report.css">')
        page = _sub_once(page, _JS_BLOCK, f'<script src="{base}report.js"></script>')
    # Data last, so text inside the payload is never taken for a placeholder.
    return _replace_once(page, DATA, "" if payload is None else embed_json(payload))
