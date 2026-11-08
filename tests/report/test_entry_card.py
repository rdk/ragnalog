"""Entry cards of the report and cluster pages: 500-line cap, "show all", banner preamble.

The ``// @entry-card`` … ``// @entry-card-end`` part of ``report.js`` and ``cluster.js``
(``entryHtml`` and its helpers) runs in node with stubs for the page's other helpers,
on entries from a real payload (``build_payload`` over an ingested log).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from atlas_log_analyzer.report import build_payload, render_html
from atlas_log_analyzer.report.render import asset_text
from atlas_log_analyzer.store import Store
from atlas_log_analyzer.store.pipeline import ingest_paths

from .conftest import report_data

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

LONG = 1200
"""Body lines of the long entry (the pages show 500 at first)."""
STARS = "*" * 60

HARNESS = r"""
const fs = require("fs");
const [blockFile, dataFile] = process.argv.slice(2);
const block = fs.readFileSync(blockFile, "utf8");
const data = JSON.parse(fs.readFileSync(dataFile, "utf8"));
const NF = new Intl.NumberFormat("en-US");
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const stubs = {
  esc: (s) => (s == null ? "" : String(s).replace(/[&<>"']/g, (c) => ESC[c])),
  fmt: (n) => NF.format(n),
  str: (x) => (x == null ? "" : String(x)),
  obj: (x) => (x && typeof x === "object" && !Array.isArray(x) ? x : {}),
  arr: (x) => (Array.isArray(x) ? x : []),
  badge: () => "", nodeBadge: () => "", tsFull: (t) => String(t), bucketOf: () => "ERROR",
  TZ: "UTC",
  levelNum: () => 50, srcPath: (s) => String(s), basename: (s) => String(s).split(/[\\/]/).pop(),
  ENTRIES_URL: null, URLS: null, ESIG: new Map(), GBY: new Map(), S: { sig: "" }, VIEW: [],
  ENTRIES: data.entries,
};
const page = new Function(...Object.keys(stubs), block + "\nreturn { entryHtml, showAllLines };")(
  ...Object.values(stubs));
const BANNER_RE = new RegExp(
  '<div class="e-pre"><span class="k">([^<]*)</span>((?:<div class="ln">[^<]*</div>)*)');
const bodyOf = (html) => {
  const m = /<pre class="e-body trace">([\s\S]*?)<\/pre>/.exec(html);
  return m ? (m[1].match(/<div class="(?:ln|fr|t-head)/g) || []).length : 0;
};
const out = {};
for (const [id, e] of Object.entries(data.cards)) {
  const first = page.entryHtml(id, e);
  const pre = { innerHTML: "" };
  const more = { removed: false, remove() { this.removed = true; } };
  const card = { querySelector: (sel) => (sel === ".e-body" ? pre : null) };
  const up = { ".entry": card, ".e-more": more };
  const btn = { dataset: { allLines: id }, closest: (sel) => up[sel] || null };
  const button = /data-all-lines="[^"]*">([^<]*)</.exec(first);
  const banner = BANNER_RE.exec(first);
  let expanded = null, again = null;
  if (button) {
    page.showAllLines(btn);
    expanded = (pre.innerHTML.match(/<div class="ln/g) || []).length;
    again = page.entryHtml(id, e);
  }
  out[id] = {
    lines: bodyOf(first),
    button: button ? button[1] : null,
    preamble: banner ? [banner[1], banner[2]] : null,
    copy_raw: first.includes(`data-copy-raw="${id}"`),
    expanded,
    more_removed: more.removed,
    again_lines: again ? bodyOf(again) : null,
    again_button: again ? again.includes("data-all-lines") : null,
  };
}
process.stdout.write(JSON.stringify(out));
"""


def _block(asset: str) -> str:
    text = asset_text(asset)
    m = re.search(r"^ *// @entry-card:.*?$(.*)^ *// @entry-card-end", text, re.S | re.M)
    assert m, f"no @entry-card block in {asset}"
    return m.group(1)


def _run(tmp_path: Path, asset: str, entries: dict[str, Any], cards: dict[str, Any]) -> Any:
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    block = tmp_path / f"{asset}.block.js"
    block.write_text(_block(asset), encoding="utf-8")
    data = tmp_path / "data.json"
    data.write_text(json.dumps({"entries": entries, "cards": cards}), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness), str(block), str(data)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def payload(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    root = tmp_path_factory.mktemp("cards")
    log = root / "atlassian-jira.log"
    head = "2026-10-29 10:00:00,{} exec-1 ERROR      [org.acme.Dump] {}"
    lines = [
        head.format("000+0100", STARS),
        head.format("002+0100", "Dumping the state of alice"),
        *(f"    step {i:04d} of the dump" for i in range(1, LONG + 1)),
        head.format("500+0100", "Short entry for bob"),
        "    only line",
    ]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ls = ingest_paths(Store(root / "store"), [log]).logset
    return build_payload(ls, max_raw=0)


def _ids(payload: dict[str, Any]) -> tuple[str, str]:
    by_message = {e["message"]: i for i, e in payload["entries"].items()}
    return by_message["Dumping the state of alice"], by_message["Short entry for bob"]


def test_payload_carries_the_preamble_and_the_whole_raw(payload: dict[str, Any]) -> None:
    long_id, short_id = _ids(payload)
    long, short = payload["entries"][long_id], payload["entries"][short_id]
    assert long["preamble"].endswith(STARS) and long["preamble"].startswith("2026-10-29 10:00:00")
    assert long["raw"].count("\n") == LONG and "raw_truncated" not in long
    assert "preamble" not in short  # omitted when empty
    page = render_html(payload)
    assert report_data(page)["entries"][long_id]["preamble"] == long["preamble"]


@pytest.mark.parametrize("asset", ["report.js", "cluster.js"])
def test_long_bodies_show_500_lines_until_show_all(
    payload: dict[str, Any], asset: str, tmp_path: Path
) -> None:
    long_id, short_id = _ids(payload)
    entries = payload["entries"]
    cards = {long_id: entries[long_id], short_id: entries[short_id]}
    out = _run(tmp_path, asset, entries, cards)
    long, short = out[long_id], out[short_id]
    assert long["lines"] == 500 and long["button"] == f"show all {LONG:,} lines"
    assert long["expanded"] == LONG and long["more_removed"]
    assert long["again_lines"] == LONG and long["again_button"] is False  # state per entry
    assert long["copy_raw"]  # "Copy raw" copies ENTRIES[id].raw: every line
    assert long["preamble"][0] == "banner line before this entry"
    assert STARS in long["preamble"][1]
    assert short["lines"] == 1 and short["button"] is None and short["preamble"] is None


def test_entries_v1_rows_show_context_preamble(payload: dict[str, Any], tmp_path: Path) -> None:
    long_id, _ = _ids(payload)
    e = payload["entries"][long_id]
    row = {k: v for k, v in e.items() if k != "preamble"}
    row |= {"context": {"preamble": f"{e['preamble']}\n{e['preamble']}"}, "node": "NODE1"}
    out = _run(tmp_path, "cluster.js", {}, {"r1": row})["r1"]
    assert out["preamble"][0] == "banner lines before this entry"
    assert out["preamble"][1].count('<div class="ln">') == 2
    assert out["lines"] == 500
