"""Table mode of the CLI: default columns and the renderers of the documents that need one.

Each renderer takes the rich console and the finished document (the same one JSON
mode prints); rich is imported inside them. Documents without a renderer here are
printed by :meth:`atlas_log_analyzer.cli.output.Output.emit` as one table of
``items`` or as key/value tables.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from atlas_log_analyzer.cli.output import _cell, mapping_tables, rows_table
from atlas_log_analyzer.service.compare import DIFF_SECTIONS

if TYPE_CHECKING:
    from rich.console import Console

__all__ = [
    "ANCHOR_MARK",
    "CAPTURE_COLUMNS",
    "CAPTURE_SET_COLUMNS",
    "DIFF_COLUMNS",
    "ENTRY_COLUMNS",
    "GREW_COLUMNS",
    "GROUP_COLUMNS",
    "NEW_COLUMNS",
    "SET_COLUMNS",
    "SPARK",
    "UPGRADE_COLUMNS",
    "VANISHED_COLUMNS",
    "captures",
    "context",
    "diff",
    "frames",
    "hour_days",
    "show",
    "stats",
    "triage",
]

type Doc = dict[str, Any]

GROUP_COLUMNS = (
    "signature",
    "count",
    "level_name",
    "logger",
    "exception_type",
    "template",
    "first_seen",
    "last_seen",
)
ENTRY_COLUMNS = ("ts", "level_name", "thread", "user", "logger", "message", "variables")
SET_COLUMNS = (
    "id",
    "name",
    "format",
    "entry_count",
    "group_count",
    "first_ts",
    "last_ts",
    "created_at",
    "stale",
)
DIFF_COLUMNS = (
    "signature",
    "count_a",
    "count_b",
    "ratio",
    "rate_ratio",
    "level_name",
    "logger",
    "exception_type",
    "template",
)
NEW_COLUMNS = (
    "signature",
    "level_name",
    "count_b",
    "rate_per_hour",
    "logger",
    "exception_type",
    "template",
)
GREW_COLUMNS = ("signature", "level_name", "count_a", "count_b", "rate_ratio", "logger", "template")
VANISHED_COLUMNS = ("signature", "level_name", "count_a", "logger", "exception_type", "template")
CAPTURE_COLUMNS = ("capture", "cluster", "nodes", "entry_count", "first_ts", "last_ts")
CAPTURE_SET_COLUMNS = ("node", "id", "name", "entry_count", "group_count", "first_ts", "last_ts")
UPGRADE_COLUMNS = ("id", "name", "status", "reason", "recipe_key", "previous_recipe_key")
ANCHOR_MARK = "▶"
"""Marks the anchor row of `context` in table mode."""
SPARK = " ▁▂▃▄▅▆▇█"


def show(console: Console, doc: Doc, columns: Sequence[str] | None, *, color: bool) -> None:
    """``show``: the group's template and counts, then its entries."""
    from rich.text import Text

    group, items = doc["group"], doc["items"]
    if group is not None:
        exc = f"  exception {group['exception_type']}" if group.get("exception_type") else ""
        console.print(Text(str(group.get("template", "")), style="bold"))
        console.print(f"logger {group.get('logger')}  level {group.get('level_name')}{exc}")
        console.print(
            f"count {group.get('count')}  first {_cell(group.get('first_seen'))}"
            f"  last {_cell(group.get('last_seen'))}"
        )
    cols = columns or [c for c in ENTRY_COLUMNS if not items or c in items[0]]
    console.print(rows_table(items, cols, title=f"entries of {doc['signature']}", color=color))


def context(
    console: Console, doc: Doc, at: int, columns: Sequence[str] | None, *, color: bool
) -> None:
    """``context``: the entries around the anchor, which is marked with ▶."""
    title = f"context of {doc['anchor']}" + (
        "" if doc["any_thread"] else f" (thread {doc['thread']})"
    )
    rows = [{"": ANCHOR_MARK if i == at else "", **r} for i, r in enumerate(doc["items"])]
    default = ["ts", "level_name", "thread", "user", "logger", "message"]
    console.print(rows_table(rows, ["", *(columns or default)], title=title, color=color))


def frames(console: Console, doc: Doc, *, all_frames: bool) -> None:
    """``frames``: each exception of the chain and its (application) frames as text."""
    from rich.text import Text

    console.print(
        Text(f"{doc['level_name']} {doc['logger']}", style="bold")
        + f"  {_cell(doc['ts'])}  entry {doc['entry']}  group {doc['signature']}"
    )
    for i, it in enumerate(doc["items"]):
        prefix = "Caused by: " if i else ""
        message = f": {it['message']}" if it["message"] else ""
        console.print(Text(f"{prefix}{it['type']}{message}", style="bold" if not i else ""))
        for f in it["frames"] if all_frames else it["app_frames"]:
            where = f"{f['file']}:{f['line']}" if f.get("line") is not None else f["file"]
            style = "bold" if f.get("app", True) else "dim"
            console.print(Text(f"    at {f['location']}({where or ''})", style=style))
        console.print(
            Text(
                f"    [{len(it['app_frames'])} of {it['app_frames_total']} app frames, "
                f"{it['frames_total']} frames, {it['omitted_frames']} omitted]",
                style="dim",
            )
        )


def hour_days(hours: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-day rows (day, count, max/h, 24-character sparkline) from ``stats`` hours."""
    by_day: dict[str, list[int]] = {}
    for h in hours:
        stamp = str(h.get("hour") or "")
        if len(stamp) < 13:
            continue
        by_day.setdefault(stamp[:10], [0] * 24)[int(stamp[11:13])] += int(h["count"])
    peak = max((max(c) for c in by_day.values()), default=0) or 1
    return [
        {
            "day": day,
            "count": sum(counts),
            "max/h": max(counts),
            "hours 00-23": "".join(
                SPARK[0] if c == 0 else SPARK[max(1, round(c / peak * 8))] for c in counts
            ),
        }
        for day, counts in sorted(by_day.items())
    ]


def captures(console: Console, doc: Doc, *, color: bool) -> None:
    """Print one row per capture with its node ids joined (``captures``)."""
    rows = [{**item, "nodes": ", ".join(n["node"] for n in item["nodes"])} for item in doc["items"]]
    console.print(rows_table(rows, CAPTURE_COLUMNS, title="captures", color=color))


def stats(console: Console, doc: Doc) -> None:
    """``stats``: key/value tables, and the hours as one row per day with a sparkline."""
    compact = {k: v for k, v in doc.items() if k not in ("schema", "hours")}
    for tbl in mapping_tables(compact, title=f"stats {doc['logset']}"):
        console.print(tbl)
    days = hour_days(doc.get("hours") or [])
    if days:
        console.print(rows_table(days, ("day", "count", "max/h", "hours 00-23"), title="hours"))


def diff(console: Console, doc: Doc, *, color: bool) -> None:
    """``diff``: one table per section (new, vanished, changed), then the unchanged count."""
    totals = doc["totals"]
    for name in DIFF_SECTIONS:
        rows = doc[name]
        if not rows:
            console.print(f"{name}: none")
            continue
        cols = [c for c in DIFF_COLUMNS if c in rows[0]]
        shown = f"{len(rows)} of {totals[name]}" if len(rows) < totals[name] else len(rows)
        console.print(rows_table(rows, cols, title=f"{name} ({shown})", color=color))
    console.print(f"unchanged: {doc['unchanged_count']}")


def triage(console: Console, doc: Doc, *, color: bool) -> None:
    """``triage``: the verdict, the summary, the coverage and one table per list."""
    from rich.text import Text

    me = doc["set"]
    if doc["baseline"] is None:
        console.print(f"verdict: no_baseline ({me['name'] or me['id']} has no baseline)")
        return
    b, s, cov = doc["baseline"], doc["summary"], doc["coverage"]
    console.print(
        Text(f"verdict: {doc['verdict']}", style="bold")
        + f"  {me['name'] or me['id']} vs {b['name'] or b['id']} ({b['resolved_by']})"
    )
    console.print(f"baseline: {b['reason']}")
    levels = ", ".join(f"{k} {v}" for k, v in s["new_by_level"].items())
    console.print(
        f"new {s['new']}"
        + (f" ({levels})" if levels else "")
        + f"  new exceptions {s['new_exceptions']}  grew {s['grew']} of {s['changed']}"
        f" changed  vanished {s['vanished']}  unchanged {s['unchanged']}"
    )
    console.print(
        f"coverage: set {cov['hours_set']} h, baseline {cov['hours_baseline']} h"
        f" (ratio {cov['ratio']})"
    )
    for name, cols in (
        ("new", NEW_COLUMNS),
        ("new_exceptions", NEW_COLUMNS),
        ("grew", GREW_COLUMNS),
        ("vanished", VANISHED_COLUMNS),
    ):
        rows = doc[name]
        if not rows:
            console.print(f"{name}: none")
            continue
        console.print(rows_table(rows, cols, title=name, color=color))
