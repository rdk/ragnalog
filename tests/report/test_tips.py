"""Rich tooltips (``tips.js``): on every page before the page's own script; ``card`` escapes."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ragnalog.report import render_cluster_html, render_html
from ragnalog.report.render import asset_text, asset_version

HARNESS = r"""
const fs = require("fs");
const listeners = [];
const target = { addEventListener: (type) => listeners.push(type) };
globalThis.window = target;
globalThis.document = target;
new Function(fs.readFileSync(process.argv[2], "utf8"))();
const T = window.RagnalogTips;
const once = T;
new Function(fs.readFileSync(process.argv[2], "utf8"))();  // a second copy is a no-op
const out = {
  same: window.RagnalogTips === once,
  listeners: listeners.sort(),
  esc: T.esc(`<a href="x">'&'</a>`),
  card: T.card({
    h: "<b>head</b>",
    sub: { html: "<i>trusted</i>" },
    rows: [["count", "1,234", "e"], ["skipped", null], ["empty", ""], ["<k>", "<v>"]],
    note: "click & go",
  }),
  empty: T.card({}),
};
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_card_escapes_text_and_keeps_trusted_markup(tmp_path: Path) -> None:
    script = tmp_path / "tips.js"
    script.write_text(asset_text("tips.js"), encoding="utf-8")
    harness = tmp_path / "harness.cjs"
    harness.write_text(HARNESS, encoding="utf-8")
    done = subprocess.run(
        ["node", str(harness), str(script)], capture_output=True, text=True, check=True
    )
    out = json.loads(done.stdout)
    assert out["same"]
    # one set of listeners although the script ran twice
    assert out["listeners"].count("pointerover") == 1
    assert out["esc"] == "&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;"
    card = out["card"]
    assert '<div class="atip-h">&lt;b&gt;head&lt;/b&gt;</div>' in card
    assert '<div class="atip-sub"><i>trusted</i></div>' in card
    assert '<dt>count</dt><dd class="e">1,234</dd>' in card
    assert "skipped" not in card and "empty" not in card  # rows without a value are left out
    assert "<dt>&lt;k&gt;</dt><dd>&lt;v&gt;</dd>" in card
    assert '<div class="atip-note">click &amp; go</div>' in card
    assert out["empty"] == ""


def test_inlined_before_the_page_script() -> None:
    tips = asset_text("tips.js")
    marker = "window.RagnalogTips = {"
    report = render_html({"schema": "report.v1", "logset": {"name": "x"}})
    assert report.count(marker) == 1
    assert report.index(marker) < report.index(asset_text("report.js")[:200])
    cluster = render_cluster_html({"schema": "cluster-report.v1", "capture": {}})
    assert cluster.count(marker) == 1
    assert cluster.index(marker) < cluster.index(asset_text("cluster.js")[:200])
    assert tips.strip().startswith("/*")


def test_linked_before_the_page_script() -> None:
    report = render_html(data_url="/api/r", inline_assets=False, asset_base="/a/")
    v = asset_version()
    assert (
        f'<script src="/a/tips.js?v={v}"></script>\n<script src="/a/report.js?v={v}"></script>'
        in report
    )
    cluster = render_cluster_html(None, data_url="/api/c", asset_base="/a/")
    assert (
        f'<script src="/a/tips.js?v={v}"></script>\n<script src="/a/cluster.js?v={v}"></script>'
        in cluster
    )


def test_tooltip_styles_are_shared() -> None:
    css = asset_text("report.css")
    for rule in (".atip {", ".atip-h", ".atip-kv", ".atip-note", "--raised:"):
        assert rule in css
