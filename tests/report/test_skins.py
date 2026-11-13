"""Appearance defaults agree before paint and when the shared picker starts."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from atlas_log_analyzer.report.render import asset_text

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

HARNESS = r"""
const vm = require("node:vm");
const fs = require("node:fs");
const { html, shared, stored, blocked } = JSON.parse(fs.readFileSync(0, "utf8"));
const attrs = new Map([["data-skin", "aurora"]]);
const root = {
  setAttribute: (key, value) => attrs.set(key, value),
  getAttribute: (key) => attrs.get(key) ?? null,
  removeAttribute: (key) => attrs.delete(key),
};
let writes = 0;
const storage = {
  getItem: (key) => key === "atlas-log-skin" ? stored : null,
  setItem: () => writes++,
};
const context = {
  document: { documentElement: root, readyState: "loading", addEventListener() {} },
  location: { hash: "#theme=dark" },
  addEventListener() {},
};
context.window = context;
Object.defineProperty(context, "localStorage", {
  get() { if (blocked) throw new Error("Storage blocked"); return storage; },
});
vm.createContext(context);
vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], context);
const before = root.getAttribute("data-skin") ?? "classic";
vm.runInContext(shared, context);
console.log(JSON.stringify({ before, after: root.getAttribute("data-skin") ?? "classic", writes }));
"""


@pytest.mark.parametrize("template", ["template.html", "cluster.html", "base.html"])
@pytest.mark.parametrize(
    ("stored", "blocked", "expected"),
    [
        (None, False, "aurora"),
        ("invalid-skin", False, "aurora"),
        ("classic", False, "classic"),
        ("aurora", False, "aurora"),
        ("mineral", False, "mineral"),
        ("classic", True, "aurora"),
    ],
)
def test_default_before_paint_matches_picker(
    template: str, stored: str | None, blocked: bool, expected: str
) -> None:
    """Missing or unreadable preferences default to Aurora without changing saved choices."""
    if template == "base.html":
        path = (
            Path(__file__).resolve().parents[2]
            / "src/atlas_log_analyzer/webapp/templates/base.html"
        )
        html = path.read_text(encoding="utf-8")
    else:
        html = asset_text(template)
    assert re.search(r'<html\b[^>]*\bdata-skin="aurora"', html)
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        input=json.dumps(
            {"html": html, "shared": asset_text("skins.js"), "stored": stored, "blocked": blocked}
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"before": expected, "after": expected, "writes": 0}
