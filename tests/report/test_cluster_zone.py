"""Cluster page time zone: printed times and axis ticks in UTC or the browser's zone.

The ``// @zone`` … ``// @zone-end`` part of ``cluster.js`` (``zoned``, ``ts16`` …) and its
``xTicks`` run in node under a fixed ``TZ`` (a half-hour zone, and one with DST).
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
const [zone, blockFile] = process.argv.slice(2);
process.env.TZ = zone; // before any Date: the "browser's" zone
const block = fs.readFileSync(blockFile, "utf8");
const isNum = (x) => typeof x === "number" && Number.isFinite(x);
const str = (x) => (x == null ? "" : String(x));
const TA = Date.parse("2026-10-24T00:00:00Z"), TE = Date.parse("2026-10-27T00:00:00Z");
const page = new Function("isNum", "str", "TA", "TE",
  block + "\nreturn { zoned, ts16, tsSec, setLocal: (v) => { TZL = v; }, xTicks, zAt, wallToMs, " +
    "setTwo: (v) => { TZ2 = v; } };",
)(isNum, str, TA, TE);
const ticks = (pxPerHour) =>
  page.xTicks((ms) => ((ms - TA) / 36e5) * pxPerHour, pxPerHour / 36e5, 1e9, 0).map((t) => t[1]);
const out = {};
for (const local of [false, true]) {
  page.setLocal(local);
  out[local ? "local" : "utc"] = {
    ts16: page.ts16("2026-10-25T00:15:00.000Z"),
    tsSec: page.tsSec("2026-10-25T00:15:07.123456+00:00"),
    hourly: ticks(70).slice(0, 4), // 1 h steps
    hourlyAll: ticks(70),
    sixHourly: ticks(12), // 6 h steps
  };
}
page.setLocal(true);
page.setTwo(true);
out.zones = ["2026-10-25T00:30:00Z", "2026-10-25T01:30:00Z"].map((t) => page.zAt(Date.parse(t)));
// 02:30 on 2026-10-25 is shown twice in Prague (00:30Z CEST, 01:30Z CET): the first instant
out.repeated = new Date(page.wallToMs(Date.parse("2026-10-25T02:30:00Z"))).toISOString();
process.stdout.write(JSON.stringify(out));
"""


def _run(zone: str, tmp_path: Path) -> Any:
    js = asset_text("cluster.js")
    block = js[js.index("  // @zone:") : js.index("  // @zone-end")]
    m = re.search(r"\n  function xTicks\(.*?\n  }\n", js, re.S)
    assert m, "xTicks not found"
    (tmp_path / "block.js").write_text(block + m.group(0), encoding="utf-8")
    (tmp_path / "harness.js").write_text(HARNESS, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(tmp_path / "harness.js"), zone, str(tmp_path / "block.js")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_utc_is_the_default_and_unshifted(tmp_path: Path) -> None:
    utc = _run("Asia/Kolkata", tmp_path)["utc"]
    assert utc["ts16"] == "2026-10-25 00:15"
    assert utc["tsSec"] == "10-25 00:15:07"
    assert utc["hourly"] == ["10-24", "01:00", "02:00", "03:00"]


def test_half_hour_zone_shifts_times_and_aligns_ticks_to_its_hours(tmp_path: Path) -> None:
    local = _run("Asia/Kolkata", tmp_path)["local"]  # UTC+05:30
    assert local["ts16"] == "2026-10-25 05:45"
    assert local["tsSec"] == "10-25 05:45:07"
    # ticks fall on the zone's own whole hours, not on UTC's
    assert local["hourly"] == ["06:00", "07:00", "08:00", "09:00"]
    assert all(t.endswith(":00") or re.fullmatch(r"\d\d-\d\d", t) for t in local["sixHourly"])


def test_dst_change_keeps_ticks_on_local_wall_clock(tmp_path: Path) -> None:
    out = _run("Europe/Prague", tmp_path)
    local = out["local"]  # CEST -> CET on 2026-10-25 03:00
    assert local["ts16"] == "2026-10-25 02:15"
    # 02:00 CEST to 01:00 CET: after the change the ticks keep to 06/12/18 and midnight
    day = ["06:00", "12:00", "18:00"]
    assert local["sixHourly"] == [*day, "10-25", *day, "10-26", *day, "10-27"]
    # hourly: the repeated hour 02:00 gets one tick (not "02:00" twice), the date at midnight only
    hours = local["hourlyAll"]
    assert hours[hours.index("10-25") + 1 : hours.index("10-26")] == [
        f"{h:02d}:00" for h in range(1, 24)
    ]
    assert out["zones"] == ["CEST", "CET"]  # ranges name each end's zone across the change
    assert out["repeated"] == "2026-10-25T00:30:00.000Z"
