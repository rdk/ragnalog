"""``report.render.render_html``: inlining, escaping, no external references, valid JS."""

from __future__ import annotations

import copy
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from atlas_log_analyzer.report import build_payload, build_report, render_html, write_report
from atlas_log_analyzer.report.render import embed_json
from atlas_log_analyzer.store import Store

from .conftest import report_data

EVIL = 'x</script><script>alert(1)</script> <!-- y --> </SCRIPT> "q" & <b>'


@pytest.fixture(scope="module")
def payload(store: Store) -> dict[str, Any]:
    return build_payload(store.open("sample"), compare=store.open("edge"))


@pytest.fixture(scope="module")
def html(payload: dict[str, Any]) -> str:
    return render_html(payload)


def test_assets_are_inlined(html: str) -> None:
    assert html.startswith("<!doctype html>")
    for marker in (
        "__REPORT_TITLE__",
        "/*__REPORT_CSS__*/",
        "/*__REPORT_JS__*/",
        "__REPORT_DATA__",
    ):
        assert marker not in html
    assert "--lv-error: #d03b3b" in html  # CSS
    assert '"use strict"' in html and '$("report-data")' in html  # JS
    assert "<title>sample · atlas-log report</title>" in html


def test_payload_round_trips(html: str, payload: dict[str, Any]) -> None:
    assert report_data(html) == payload


def test_no_external_references(html: str) -> None:
    assert not re.search(r"""\b(?:src|href|action)\s*=\s*["']?\s*(?:https?:)?//""", html, re.I)
    assert not re.search(r"<link\b", html, re.I)
    assert not re.search(r"<script\b[^>]*\bsrc=", html, re.I)
    assert not re.search(r"@import|url\(\s*['\"]?(?:https?:)?//", html, re.I)


def test_script_breakers_are_escaped(payload: dict[str, Any]) -> None:
    p = copy.deepcopy(payload)
    entry_id = next(iter(p["entries"]))
    p["entries"][entry_id]["raw"] = EVIL
    p["groups"][0]["template"] = EVIL
    p["logset"]["name"] = "<b>set</b>"
    page = render_html(p)
    assert "alert(1)</script>" not in page
    assert "<!-- y" not in page
    assert "</SCRIPT>" not in page
    assert "<title>&lt;b&gt;set&lt;/b&gt; · atlas-log report</title>" in page
    data = report_data(page)
    assert isinstance(data, dict)
    assert data["entries"][entry_id]["raw"] == EVIL
    assert data["groups"][0]["template"] == EVIL
    # outside the data element only the template's own script elements remain
    start = page.index('id="report-data">')
    outside = page[:start] + page[page.index("</script>", start) :]
    assert len(re.findall(r"<script\b", outside, re.I)) == 3


def test_embed_json_is_valid_json() -> None:
    text = embed_json({"a": "</script><!--x", "b": ["</", "<!--"]})
    assert "</" not in text and "<!--" not in text
    import json

    assert json.loads(text) == {"a": "</script><!--x", "b": ["</", "<!--"]}


def test_placeholder_text_in_payload_is_not_substituted(payload: dict[str, Any]) -> None:
    p = copy.deepcopy(payload)
    p["groups"][0]["template"] = "/*__REPORT_JS__*/ __REPORT_TITLE__"
    data = report_data(render_html(p))
    assert isinstance(data, dict)
    assert data["groups"][0]["template"] == "/*__REPORT_JS__*/ __REPORT_TITLE__"


def test_renders_without_groups_merges_or_compare(store: Store) -> None:
    p = build_payload(store.open("sample"), max_groups=0)
    assert p["groups"] == [] and p["entries"] == {} and p["merges"] == []
    data = report_data(render_html(p))
    assert isinstance(data, dict)
    assert data["groups"] == [] and data["compare"] is None


def test_build_and_write_report(store: Store, tmp_path: Path) -> None:
    ls = store.open("sample")
    out = tmp_path / "r.html"
    info = write_report(ls, out, examples=1)
    assert info.path == out
    assert info.bytes == out.stat().st_size
    assert info.groups == ls.manifest.group_count
    assert info.compare is False
    assert 0 < info.entries <= info.groups
    assert build_report(ls, examples=1).encode("utf-8") != b""


def _inline_script(page: str) -> str:
    scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
    assert len(scripts) == 2  # theme bootstrap + report.js
    return scripts[-1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_inline_script_parses_with_node(html: str, tmp_path: Path) -> None:
    js = tmp_path / "report.js"
    js.write_text(_inline_script(html), encoding="utf-8")
    proc = subprocess.run(
        ["node", "--check", str(js)], capture_output=True, text=True, timeout=60, check=False
    )
    assert proc.returncode == 0, proc.stderr
