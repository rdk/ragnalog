"""Set report chart bins: the automatic bin, the bins menu, hour frames and fine ticks.

The ``// @bins``, ``// @zone``, ``// @hours`` and ``// @frames`` parts of ``report.js`` run in
node under ``TZ=Europe/Prague`` over 30 invented hours: the bin a span gets, the choices the
menu offers, an hour frame over the whole timeline (the bins the chart always drew), and the
ticks and tooltip range of a frame finer than an hour (24-hour times in the shown zone).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from ragnalog.report.render import asset_text

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

DASH = "\u2013"  # the en dash between the ends of a range

HARNESS = r"""
const fs = require("fs");
const [blockFile] = process.argv.slice(2);
process.env.TZ = "Europe/Prague"; // before any Date: the "browser's" zone
const block = fs.readFileSync(blockFile, "utf8");
const t0 = Date.parse("2026-10-29T00:00:00Z");
const HOURS = Array.from({ length: 30 },
  (_, i) => new Date(t0 + i * 36e5).toISOString().slice(0, 19) + "Z");
const NH = HOURS.length, T0MS = t0;
const page = new Function("HOURS", "NH", "T0MS", "ENTRIES_URL", "LS", "localStorage",
  block + "\nreturn { HOUR, autoBin, binChoices, binName, binShort, setZone, " +
    "hourFrame, fineFrame, fineTicks };",
)(HOURS, NH, T0MS, "/api/x", { id: "abc" }, { getItem: () => null });
const H = page.HOUR, min = 6e4;
page.setZone(true);
const hf = page.hourFrame(0, NH, 3);
// 16:05 to 16:35 CET
const fine = page.fineFrame(t0 + 15 * H + 5 * min, t0 + 15 * H + 35 * min, 3e4);
const secs = page.fineFrame(t0 + 15 * H + 5 * min, t0 + 15 * H + 7 * min, 1e3);
process.stdout.write(JSON.stringify({
  week: page.autoBin(168 * H, 1100, H),
  twoHours: page.autoBin(2 * H, 1100, 1e3),
  twoHoursOffline: page.autoBin(2 * H, 1100, H),
  tenMinutes: page.autoBin(10 * min, 1100, 1e3),
  choices: page.binChoices(2 * H, 1100, 1e3).map(page.binShort),
  offline: page.binChoices(30 * H, 1100, H).map(page.binShort),
  names: [6e4, 3e5, H, 3 * H, 24 * H, 168 * H, 1e4].map(page.binName),
  hour: { nb: hf.nb, first: hf.hours(0), second: hf.hours(1), range0: hf.range(0) },
  fine: { nb: fine.nb, range0: fine.range(0), ticks: page.fineTicks(fine, 20) },
  secs: { range0: secs.range(0), ticks: page.fineTicks(secs, 10).slice(0, 3) },
}));
"""


def _run(tmp_path: Path) -> Any:
    js = asset_text("report.js")
    parts = []
    for name in ("bins", "zone", "hours", "frames"):
        start = js.index(f"  // @{name}:")
        parts.append(js[start : js.index(f"  // @{name}-end")])
    setzone = js[js.index("  const setZone = (local) => {") : js.index("  const ENTRIES = ")]
    (tmp_path / "block.js").write_text("".join(parts) + setzone, encoding="utf-8")
    (tmp_path / "harness.js").write_text(HARNESS, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(tmp_path / "harness.js"), str(tmp_path / "block.js")],
        capture_output=True,
        encoding="utf-8",  # the ranges have an en dash; not the locale code page
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_automatic_bin_fits_the_span(tmp_path: Path) -> None:
    out = _run(tmp_path)
    # bars of at least 10 px over 1100 px
    assert out["week"] == 7_200_000  # a week: 2-hour bins, 84 bars of 13 px (hours: 6.5 px)
    assert out["twoHours"] == 120_000  # zoomed into 2 hours: 2 min (minute bars would be 9 px)
    assert out["twoHoursOffline"] == 3_600_000  # the standalone file has hours only
    assert out["tenMinutes"] == 10_000


def test_bins_menu_offers_finer_and_coarser_bins(tmp_path: Path) -> None:
    out = _run(tmp_path)
    # finer than the automatic minute down to 2 px a bar; coarser up to 2 bars
    finer = ["30 s", "1 min", "2 min", "5 min", "10 min", "15 min", "30 min", "1 h"]
    assert out["choices"] == finer
    assert out["offline"] == ["1 h", "2 h", "3 h", "4 h", "6 h", "12 h", "1 d"]
    assert out["names"] == ["minute", "5 minutes", "hour", "3 hours", "day", "7 days", "10 seconds"]


def test_hour_frame_is_the_old_binning(tmp_path: Path) -> None:
    hour = _run(tmp_path)["hour"]
    # 3-hour bins start on the zone's clock: 00:00 UTC is 01:00 CET, so the first bin is 1 hour
    assert hour["nb"] == 11
    assert hour["first"] == [0, 2] and hour["second"] == [2, 5]
    assert hour["range0"] == f"2026-10-29 01:00 {DASH} 03:00 CET"


def test_fine_frame_ticks_are_24_hour_local_times(tmp_path: Path) -> None:
    out = _run(tmp_path)
    fine, secs = out["fine"], out["secs"]
    assert fine["nb"] == 60  # 30 minutes of 30 s bins
    assert fine["range0"] == f"2026-10-29 16:05:00 {DASH} 16:05:30 CET"
    labels = [label for _, label in fine["ticks"]]
    assert labels[:3] == ["16:06", "16:08", "16:10"]  # afternoon in 24-hour time, no AM/PM
    assert all(len(label) == 5 and label[2] == ":" for label in labels)
    assert all(0 <= pos <= fine["nb"] for pos, _ in fine["ticks"])
    assert secs["range0"] == f"2026-10-29 16:05:00 {DASH} 16:05:01 CET"
    assert [label for _, label in secs["ticks"]] == ["16:05:00", "16:05:10", "16:05:20"]
