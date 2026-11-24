"""Render a ``report.v1`` payload into an HTML page.

``template.html`` carries four placeholders that are replaced exactly once:
``__REPORT_TITLE__`` (HTML-escaped), ``/*__REPORT_CSS__*/``, ``/*__REPORT_JS__*/``
and ``__REPORT_DATA__`` (the payload JSON inside
``<script type="application/json" id="report-data">``).

By default the page is self-contained (assets inlined, payload embedded). The web app
instead renders a shell: ``payload=None`` with ``data_url`` (the page fetches the
payload from ``<html data-report-url>``) and ``inline_assets=False`` (``<link>`` /
``<script src>`` under ``asset_base``, served from :func:`asset_text`).

The cluster page (:func:`render_cluster_html`, ``cluster-report.v1``) works the same way
with ``cluster.html``: ``report.css`` then ``cluster.css`` (``/*__CLUSTER_CSS__*/``) and
``cluster.js`` (``/*__CLUSTER_JS__*/``), the payload in ``#cluster-data``.

Both pages load ``skins.css`` after their page CSS and run ``entry-card.js``, ``skins.js``
(the appearance picker) and ``tips.js`` before their own script: inlined ahead of it in one
``<script>``, or linked as their own ``<script src>``. The web app shares these assets.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
from functools import cache
from importlib.resources import files
from typing import Any

__all__ = ["CLUSTER_SCHEMA", "asset_text", "embed_json", "render_cluster_html", "render_html"]

PACKAGE = "ragnalog.report"
TITLE = "__REPORT_TITLE__"
CSS = "/*__REPORT_CSS__*/"
JS = "/*__REPORT_JS__*/"
DATA = "__REPORT_DATA__"
CLUSTER_SCHEMA = "cluster-report.v1"
"""Schema of the cluster page's payload (``docs/cluster.md`` §11.3)."""
CLUSTER_CSS = "/*__CLUSTER_CSS__*/"
CLUSTER_JS = "/*__CLUSTER_JS__*/"
TIPS_JS = "tips.js"
ENTRY_JS = "entry-card.js"
SKINS_JS = "skins.js"
SKINS_CSS = "skins.css"
SHARED_JS = (ENTRY_JS, SKINS_JS, TIPS_JS)
"""Scripts both pages run before their own: entry cards, appearance and tooltips."""
ASSETS = (
    "template.html",
    "report.css",
    SKINS_CSS,
    SKINS_JS,
    TIPS_JS,
    ENTRY_JS,
    "report.js",
    "cluster.html",
    "cluster.css",
    "cluster.js",
)

_CSS_BLOCK = re.compile(r"<style>\s*/\*__REPORT_CSS__\*/\s*</style>")
_JS_BLOCK = re.compile(r"<script>\s*/\*__REPORT_JS__\*/\s*</script>")
_CLUSTER_CSS_BLOCK = re.compile(r"<style>\s*/\*__CLUSTER_CSS__\*/\s*</style>")
_CLUSTER_JS_BLOCK = re.compile(r"<script>\s*/\*__CLUSTER_JS__\*/\s*</script>")


def asset_text(name: str) -> str:
    """Return the text of a report asset (one of :data:`ASSETS`).

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


def _title(payload: dict[str, Any] | None, name: str | None = None) -> str:
    logset = (payload or {}).get("logset") or {}
    name = logset.get("name") or logset.get("id") or name or "log set"
    return f"{name} · ragnalog report"


def _cluster_title(payload: dict[str, Any] | None, name: str | None = None) -> str:
    capture = (payload or {}).get("capture") or {}
    name = capture.get("cluster") or capture.get("capture") or name or "cluster"
    return f"{name} · ragnalog cluster"


def _inline(page: str, marker: str, *names: str) -> str:
    texts = []
    for name in names:
        text = asset_text(name)
        if "</" + "script" in text.lower() or "</style" in text.lower():
            raise ValueError(f"{name} must not contain a closing script/style tag")
        texts.append(text)
    return _replace_once(page, marker, "\n".join(texts))


def _replace_once(text: str, marker: str, value: str) -> str:
    before, found, after = text.partition(marker)
    if not found:
        raise ValueError(f"report template has no {marker} placeholder")
    return before + value + after


def _put_title(page: str, title: str) -> str:
    """Fill the ``<title>`` element (its first occurrence: the head precedes the data)."""
    marker = f"<title>{TITLE}</title>"
    return _replace_once(page, marker, f"<title>{html.escape(title)}</title>")


def _sub_once(text: str, pattern: re.Pattern[str], value: str) -> str:
    out, n = pattern.subn(lambda _m: value, text, count=1)
    if n != 1:
        raise ValueError(f"report template has no {pattern.pattern} block")
    return out


def _asset_stamp() -> tuple[int, ...]:
    """Return the assets' modification times (empty when they are not plain files)."""
    stamps = []
    for name in ASSETS:
        try:
            stamps.append(os.stat(str(files(PACKAGE).joinpath("assets", name))).st_mtime_ns)
        except OSError:
            return ()
    return tuple(stamps)


@cache
def _asset_hash(stamp: tuple[int, ...]) -> str:
    digest = hashlib.sha256()
    for name in ASSETS:
        digest.update(asset_text(name).encode("utf-8"))
    return digest.hexdigest()[:10]


def asset_version() -> str:
    """Return a short hash of the report assets, the ``?v=`` of their links.

    A linked asset's URL changes with its content, so a browser fetches a new version at
    once instead of running a cached script against a newer server. The hash is recomputed
    when an asset file changes (``serve --reload`` while editing ``report.js``).
    """
    return _asset_hash(_asset_stamp())


def _href(base: str, name: str) -> str:
    """Return ``base`` (escaped) and ``name`` with the assets' version."""
    return f"{base}{name}?v={asset_version()}"


def _script_tags(base: str, page_js: str) -> str:
    """``<script src>`` of the shared scripts and ``page_js`` under the escaped ``base``."""
    return "\n".join(
        f'<script src="{_href(base, name)}"></script>' for name in (*SHARED_JS, page_js)
    )


def render_html(
    payload: dict[str, Any] | None = None,
    *,
    data_url: str | None = None,
    inline_assets: bool = True,
    asset_base: str = "/static/",
    name: str | None = None,
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
        name: The set's name for the page title when ``payload`` is ``None``.

    Raises:
        ValueError: Neither ``payload`` nor ``data_url`` given, or a broken template/asset.

    """
    if payload is None and data_url is None:
        raise ValueError("render_html needs a payload or a data_url")
    page = asset_text("template.html")
    if data_url is not None:
        attr = f' data-report-url="{html.escape(data_url, quote=True)}"'
        page = _replace_once(page, "<html", "<html" + attr)
    if inline_assets:
        css = "\n".join(asset_text(n) for n in ("report.css", SKINS_CSS))
        js = "\n".join(asset_text(n) for n in (*SHARED_JS, "report.js"))
        for name, text in (("report.css", css), ("report.js", js)):
            if "</" + "script" in text.lower() or "</style" in text.lower():
                raise ValueError(f"{name} must not contain a closing script/style tag")
        page = _replace_once(page, CSS, css)
        page = _replace_once(page, JS, js)
    else:
        base = html.escape(asset_base, quote=True)
        page = _sub_once(
            page,
            _CSS_BLOCK,
            f'<link rel="stylesheet" href="{_href(base, "report.css")}">\n'
            f'<link rel="stylesheet" href="{_href(base, SKINS_CSS)}">',
        )
        page = _sub_once(
            page,
            _JS_BLOCK,
            _script_tags(base, "report.js"),
        )
    # Data and title last, so text inside them is never taken for a placeholder.
    page = _replace_once(page, DATA, "" if payload is None else embed_json(payload))
    return _put_title(page, _title(payload, name))


def render_cluster_html(
    payload: dict[str, Any] | None = None,
    *,
    data_url: str | None = None,
    asset_base: str | None = None,
    name: str | None = None,
) -> str:
    """Return the cluster page of a capture (``cluster-report.v1``).

    Args:
        payload: The ``cluster-report.v1`` payload to embed; ``None`` embeds an empty data
            element (requires ``data_url``).
        data_url: URL the page fetches the payload from; set as ``data-report-url`` on
            the ``<html>`` element.
        asset_base: ``None`` inlines ``report.css``, ``cluster.css`` and ``cluster.js``
            (a self-contained file); otherwise they are linked as ``{asset_base}<name>``.
        name: The cluster or capture for the page title when ``payload`` is ``None``.

    Raises:
        ValueError: Neither ``payload`` nor ``data_url`` given, or a broken template/asset.

    """
    if payload is None and data_url is None:
        raise ValueError("render_cluster_html needs a payload or a data_url")
    page = asset_text("cluster.html")
    if data_url is not None:
        attr = f' data-report-url="{html.escape(data_url, quote=True)}"'
        page = _replace_once(page, "<html", "<html" + attr)
    if asset_base is None:
        page = _inline(page, CSS, "report.css")
        page = _inline(page, CLUSTER_CSS, "cluster.css", SKINS_CSS)
        page = _inline(page, CLUSTER_JS, *SHARED_JS, "cluster.js")
    else:
        base = html.escape(asset_base, quote=True)
        page = _sub_once(
            page, _CSS_BLOCK, f'<link rel="stylesheet" href="{_href(base, "report.css")}">'
        )
        page = _sub_once(
            page,
            _CLUSTER_CSS_BLOCK,
            f'<link rel="stylesheet" href="{_href(base, "cluster.css")}">\n'
            f'<link rel="stylesheet" href="{_href(base, SKINS_CSS)}">',
        )
        page = _sub_once(
            page,
            _CLUSTER_JS_BLOCK,
            _script_tags(base, "cluster.js"),
        )
    # Data and title last, so text inside them is never taken for a placeholder.
    page = _replace_once(page, DATA, "" if payload is None else embed_json(payload))
    return _put_title(page, _cluster_title(payload, name))
