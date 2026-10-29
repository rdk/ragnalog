"""Render a ``report.v1`` payload into one self-contained HTML page.

``template.html`` carries four placeholders that are replaced exactly once:
``__REPORT_TITLE__`` (HTML-escaped), ``/*__REPORT_CSS__*/``, ``/*__REPORT_JS__*/``
and ``__REPORT_DATA__`` (the payload JSON inside
``<script type="application/json" id="report-data">``).
"""

from __future__ import annotations

import html
import json
from importlib.resources import files
from typing import Any

__all__ = ["embed_json", "render_html"]

PACKAGE = "atlas_log_analyzer.report"
TITLE = "__REPORT_TITLE__"
CSS = "/*__REPORT_CSS__*/"
JS = "/*__REPORT_JS__*/"
DATA = "__REPORT_DATA__"


def _asset(name: str) -> str:
    return files(PACKAGE).joinpath("assets", name).read_text(encoding="utf-8")


def embed_json(payload: Any) -> str:
    r"""Compact JSON that is safe inside a ``<script>`` element.

    ``</`` becomes ``<\\/`` and ``<!--`` becomes ``\\u003c!--`` (both are valid JSON
    string escapes, and ``<`` can only occur inside strings), so neither a
    ``</script>`` nor an HTML comment opener in log text can end or alter the element.
    """
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return text.replace("<!--", "\\u003c!--").replace("</", "<\\/")


def _title(payload: dict[str, Any]) -> str:
    logset = payload.get("logset") or {}
    name = logset.get("name") or logset.get("id") or "log set"
    return f"{name} · atlas-log report"


def _replace_once(text: str, marker: str, value: str) -> str:
    before, found, after = text.partition(marker)
    if not found:
        raise ValueError(f"report template has no {marker} placeholder")
    return before + value + after


def render_html(payload: dict[str, Any]) -> str:
    """Return the report page for ``payload`` with CSS, JS and data inlined."""
    css, js = _asset("report.css"), _asset("report.js")
    for name, text in (("report.css", css), ("report.js", js)):
        if "</" + "script" in text.lower() or "</style" in text.lower():
            raise ValueError(f"{name} must not contain a closing script/style tag")
    page = _asset("template.html")
    page = _replace_once(page, TITLE, html.escape(_title(payload)))
    page = _replace_once(page, CSS, css)
    page = _replace_once(page, JS, js)
    # Data last, so text inside the payload is never taken for a placeholder.
    return _replace_once(page, DATA, embed_json(payload))
