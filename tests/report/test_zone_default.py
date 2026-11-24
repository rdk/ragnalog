"""The time zone a viewer sees first: the browser's own, unless UTC was chosen.

Each page script reads the shared ``ragnalog-tz`` preference once at load. The line that
does it runs in node with a stub ``localStorage``: nothing saved (or storage blocked) means
the browser's zone, a saved ``"utc"`` means UTC.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest

from ragnalog.report.render import asset_text

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

HARNESS = r"""
const fs = require("fs");
const [decl, name] = process.argv.slice(2);
const read = new Function("localStorage", "TZ_KEY",
  fs.readFileSync(decl, "utf8") + `\nreturn ${name};`);
const stub = (v) => ({ getItem: (k) => (k === "ragnalog-tz" ? v : null) });
const blocked = { getItem: () => { throw new Error("SecurityError"); } };
process.stdout.write(JSON.stringify({
  unset: read(stub(null), "ragnalog-tz"),
  utc: read(stub("utc"), "ragnalog-tz"),
  local: read(stub("local"), "ragnalog-tz"),
  junk: read(stub("x"), "ragnalog-tz"),
  blocked: read(blocked, "ragnalog-tz"),
}));
"""

# (script, the declaration that reads the preference, the variable it sets)
SCRIPTS = [
    ("report.js", r"\n  let TZL = \(\(\) => \{.*?\}\)\(\);\n", "TZL"),
    ("cluster.js", r"\n  let TZL = \(\(\) => \{.*?\}\)\(\);\n", "TZL"),
    ("webapp.js", r"\n  let tzLocal = .*?;\n  try \{ tzLocal = .*?\n", "tzLocal"),
]


def _source(script: str) -> str:
    if script == "webapp.js":
        return files("ragnalog.webapp").joinpath("static", script).read_text(encoding="utf-8")
    return asset_text(script)


@pytest.mark.parametrize(("script", "pattern", "name"), SCRIPTS, ids=[s[0] for s in SCRIPTS])
def test_browser_zone_is_the_default(tmp_path: Path, script: str, pattern: str, name: str) -> None:
    m = re.search(pattern, _source(script), re.S)
    assert m, f"the {name} declaration was not found in {script}"
    (tmp_path / "decl.js").write_text(m.group(0), encoding="utf-8")
    (tmp_path / "harness.js").write_text(HARNESS, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(tmp_path / "harness.js"), str(tmp_path / "decl.js"), name],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    # true = the browser's zone, false = UTC; only a saved "utc" picks UTC
    assert json.loads(proc.stdout) == {
        "unset": True,
        "utc": False,
        "local": True,
        "junk": True,
        "blocked": True,
    }
