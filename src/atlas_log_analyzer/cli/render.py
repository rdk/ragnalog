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
    "NODE_ENTRY_COLUMNS",
    "SET_COLUMNS",
    "SPARK",
    "UPGRADE_COLUMNS",
    "VANISHED_COLUMNS",
    "capture",
    "captures",
    "cluster_timeline",
    "context",
    "diff",
    "events",
    "frames",
    "hour_days",
    "interactions",
    "matrix",
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
NODE_ENTRY_COLUMNS = ("ts", "node", "level_name", "thread", "logger", "message")
"""Default columns of entries of several nodes (a capture's merged entries, context)."""
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


# --- captures


def _window(doc: Doc) -> str:
    w = doc["window"]
    return f"window {w['mode']} {_cell(w['since'])} .. {_cell(w['until'])}"


def _duration(ms: int | None) -> str:
    """``+3 h``, ``+40 s``: a lead or lag in the largest whole unit."""
    if ms is None:
        return ""
    sign = "-" if ms < 0 else "+"
    ms = abs(ms)
    for unit, size in (("d", 86_400_000), ("h", 3_600_000), ("min", 60_000), ("s", 1000)):
        if ms >= size:
            return f"{sign}{ms / size:.3g} {unit}"
    return f"{sign}{ms} ms"


def capture(console: Console, doc: Doc) -> None:
    """``capture``: the window, one row per node, then the clock skew estimate."""
    from rich.text import Text

    name = doc["cluster"] or "capture"
    console.print(
        Text(f"{name} {doc['capture'] or ''}".strip(), style="bold") + f"  {_window(doc)}"
    )
    rows = []
    for n in doc["nodes"]:
        cover = n["coverage"]
        top = n["top_error"]
        base = n["baseline"]
        rows.append(
            {
                "node": n["node"],
                "set": n["set"]["name"] or n["set"]["id"],
                "entries": n["entries"],
                "errors": n["errors"],
                "warnings": n["warnings"],
                "errors/h": n["errors_per_hour"],
                "coverage": f"{_cell(cover['since'])} .. {_cell(cover['until'])}" if cover else "",
                "gaps": len(n["gaps"]),
                "verdict": n["verdict"] or "",
                "baseline": (base["name"] or base["id"]) if base else "",
                "events": ", ".join(f"{k} {v}" for k, v in n["events"].items()),
                "top error": f"{top['count']}x {top['template']}" if top else "",
            }
        )
    console.print(rows_table(rows, title="nodes"))
    for s in doc["skew"]:
        console.print(
            f"clock skew {s['b']} - {s['a']}: {_duration(s['offset_ms'])} "
            f"({s['pairs']} event pairs; an estimate, not applied)"
        )


def cluster_timeline(console: Console, doc: Doc) -> None:
    """``timeline`` of a capture: one row per bucket, the entries of each node (and errors)."""
    nodes = doc["nodes"]
    one = "signature" in doc
    rows = []
    for i, bucket in enumerate(doc["buckets"]):
        row: dict[str, Any] = {"bucket": bucket}
        for node, t in nodes.items():
            levels = t["levels"]
            cover = t["coverage"]
            inside = cover is not None and cover[0] <= i <= cover[1]
            row[node] = sum(v[i] for v in levels.values()) if inside else None
            if not one:
                row[f"{node} ERROR"] = levels["ERROR"][i] if inside else None
        rows.append(row)
    what = f"group {doc['signature']}" if one else "entries"
    console.print(rows_table(rows, title=f"{what} per {doc['bucket']} and node (blank: no logs)"))


def matrix(console: Console, doc: Doc, *, color: bool) -> None:
    """``matrix``: one row per group with a count column per node, spread and lead."""
    rows = []
    for r in doc["items"]:
        row: dict[str, Any] = {"signature": r["signature"], "level_name": r["level_name"]}
        row["total"] = r["total"]
        row.update({node: c["count"] for node, c in r["per_node"].items()})
        row["spread"] = r["spread"]
        row["skew"] = r["skew"]
        row["first"] = r["first_node"]
        row["lead"] = _duration(r["lead_ms"])
        row["template"] = r["template"]
        rows.append(row)
    shown = (
        f"{doc['count']} of {doc['total']}" if doc["truncated"] or doc["offset"] else doc["total"]
    )
    title = f"groups by node ({shown}); {_window(doc)}"
    if not rows:
        console.print(f"{title}: none")
        return
    console.print(rows_table(rows, title=title, color=color))


def events(console: Console, doc: Doc) -> None:
    """``events``: the events in time order, then the pairs as sentences."""
    rows = [
        {
            "ts": e["ts"],
            "node": e["node"],
            "kind": e["kind"],
            "subject": e["subject"],
            "count": e["count"],
            "text": e["text"],
        }
        for e in doc["items"]
    ]
    if rows:
        cols = ("ts", "node", "kind", "subject", "count", "text")
        console.print(rows_table(rows, cols, title=f"events ({doc['count']})"))
    else:
        console.print("events: none")
    for p in doc["pairs"]:
        a, b = p["a"], p["b"]
        console.print(
            f"{a['kind']} on {a['node']} is followed by {b['kind']} on {b['node']} within 60 s, "
            f"{p['count']} of {p['of']} times (median {_duration(p['median_lag_ms'])}, "
            f"lift x{p['lift']:.3g})"
        )


def interactions(console: Console, doc: Doc) -> None:
    """``interactions``: mentions, co-bursts as sentences, concurrent jobs, sessions, jobs."""
    if doc["mentions"]:
        rows = [{**m, "groups": len(m["groups"])} for m in doc["mentions"]]
        console.print(rows_table(rows, ("from", "to", "count", "groups"), title="mentions"))
    else:
        console.print("mentions: none")
    minutes = doc["delta_s"] // 60
    for c in doc["co_bursts"]:
        a, b = c["a"], c["b"]
        console.print(
            f"when {a['template']} [{a['signature']}] bursts on {a['node']}, "
            f"{b['template']} [{b['signature']}] bursts on {b['node']} within {minutes} min, "
            f"{c['hits']} of {c['of']} times (median {_duration(c['median_lag_ms'])}, "
            f"lift x{c['lift']:.3g})"
        )
    if not doc["co_bursts"]:
        console.print("co-bursts: none")
    for key, cols in (
        ("concurrent_jobs", ("job", "nodes", "slots", "examples")),
        ("sessions", ("nodes", "count")),
    ):
        if doc[key]:
            console.print(rows_table(doc[key], cols, title=key.replace("_", " ")))
        else:
            console.print(f"{key.replace('_', ' ')}: none")
    if doc["jobs"]:
        rows = [{"job": j["job"], **j["runs"]} for j in doc["jobs"]]
        console.print(rows_table(rows, title="job runs per node"))
