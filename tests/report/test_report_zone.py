"""Set report time zone: printed times, hour labels and bins in UTC or the browser's zone.

The ``// @zone`` and ``// @hours`` parts of ``report.js`` and its ``setZone`` run in node
under a fixed ``TZ`` (a half-hour zone, and one with a DST change) over 30 invented hours.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from ragnalog.report.render import asset_text

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

HARNESS = r"""
const fs = require("fs");
const [zone, blockFile, first] = process.argv.slice(2);
process.env.TZ = zone; // before any Date: the "browser's" zone
const block = fs.readFileSync(blockFile, "utf8");
const t0 = Date.parse(first);
const HOURS = Array.from({ length: 30 },
  (_, i) => new Date(t0 + i * 36e5).toISOString().slice(0, 19) + "Z");
const NH = HOURS.length;
const page = new Function("HOURS", "NH", "localStorage",
  block + "\nreturn { setZone, hourLabel, binRange, binOff, ts16, zone: () => TZ };",
)(HOURS, NH, { getItem: () => null });
const out = {};
for (const local of [false, true]) {
  page.setZone(local);
  out[local ? "local" : "utc"] = {
    zone: page.zone(),
    ts16: page.ts16(HOURS[0]),
    labels: HOURS.map((_, i) => page.hourLabel(i)),
    off3: page.binOff(3),
    bin0: page.binRange(0, 3, page.binOff(3)),
  };
}
process.stdout.write(JSON.stringify(out));
"""


def _run(zone: str, first: str, tmp_path: Path) -> Any:
    js = asset_text("report.js")
    parts = [
        js[js.index("  // @zone:") : js.index("  // @zone-end")],
        js[js.index("  // @hours:") : js.index("  // @hours-end")],
    ]
    m = re.search(r"\n  const setZone = \(local\) => \{.*?\n  \};\n", js, re.S)
    assert m, "setZone not found"
    (tmp_path / "block.js").write_text("".join(parts) + m.group(0), encoding="utf-8")
    (tmp_path / "harness.js").write_text(HARNESS, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(tmp_path / "harness.js"), zone, str(tmp_path / "block.js"), first],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_utc_by_default(tmp_path: Path) -> None:
    utc = _run("Asia/Kolkata", "2026-10-24T20:00:00Z", tmp_path)["utc"]
    assert utc["zone"] == "UTC" and utc["ts16"] == "2026-10-24 20:00"
    assert utc["labels"][:5] == ["20:00", "21:00", "22:00", "23:00", "10-25"]
    assert utc["off3"] == 2  # 20:00 is the third hour of the 18:00 bin
    assert utc["bin0"] == "2026-10-24 20:00 \u2013 21:00 UTC"  # a partial first bin


def test_half_hour_zone_labels_its_own_days(tmp_path: Path) -> None:
    local = _run("Asia/Kolkata", "2026-10-24T20:00:00Z", tmp_path)["local"]  # UTC+05:30
    assert local["ts16"] == "2026-10-25 01:30"
    assert local["labels"][:3] == ["01:30", "02:30", "03:30"]
    # the first hour of the next local day is labelled with its date (00:30 there)
    assert local["labels"][23] == "10-26"
    assert all(re.fullmatch(r"\d\d:30|\d\d-\d\d", t) for t in local["labels"])


def test_dst_change_names_both_zones(tmp_path: Path) -> None:
    local = _run("Europe/Prague", "2026-10-24T20:00:00Z", tmp_path)["local"]
    assert local["zone"] == "CEST/CET"  # the hours span the change of 2026-10-25
    assert local["labels"][2] == "10-25"  # 22:00 UTC is midnight CEST
    assert local["labels"][3:6] == ["01:00", "02:00", "02:00"]  # 02:00 CEST, then 02:00 CET
