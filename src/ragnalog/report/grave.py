"""Grave errors: the entries a chart marks loudly (out of memory, a stack overflow, FATAL …).

A grave entry is one whose exception chain or message names a JVM failure or an exhausted
resource, or one logged at FATAL. Matching reads the exception chain's types and messages
and the template (never ``raw``), so the startup banner's JVM options
(``-XX:+HeapDumpOnOutOfMemoryError``) do not count: a name must stand as a word.

Entries of one kind within :data:`FOLD_MS` of the previous fold into one marker, so an
out-of-memory storm of thousands of lines is one marker with its first and last time and
its count. The report page (``report.v1`` ``grave``) and the cluster page
(``cluster-report.v1`` ``grave``, per node) draw them over their timelines.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

import polars as pl

from ragnalog.store.filters import has_time

if TYPE_CHECKING:
    from ragnalog.store.logset import LogSet

__all__ = ["FOLD_MS", "GRAVE_KINDS", "MAX_MARKERS", "grave_markers"]

GRAVE_KINDS: Final = ("oom", "stack_overflow", "jvm_error", "resource", "fatal")
"""Kinds, most grave first: the first that matches an entry decides its kind.

- ``oom``: ``java.lang.OutOfMemoryError`` in the chain, or a message naming it, "GC
  overhead limit exceeded" or "unable to create new native thread";
- ``stack_overflow``: ``StackOverflowError``;
- ``jvm_error``: another ``VirtualMachineError`` (``InternalError``, ``UnknownError``);
- ``resource``: "Too many open files", "No space left on device", "Disk quota exceeded";
- ``fatal``: any other entry logged at FATAL.
"""

FOLD_MS: Final = 60_000
"""Entries of one kind at most this far apart fold into one marker."""

MAX_MARKERS: Final = 500
"""Markers kept per set: the ones with the most entries when there are more."""

_SEP: Final = "\x1f"
"""Joins the chain's types (and its messages) into one string per entry."""

_TYPES: Final = {
    "oom": r"OutOfMemoryError",
    "stack_overflow": r"StackOverflowError",
    "jvm_error": r"(?:VirtualMachineError|java\.lang\.InternalError|java\.lang\.UnknownError)",
}
_TEXT: Final = {
    "oom": (
        r"\bOutOfMemoryError\b|GC overhead limit exceeded"
        r"|unable to create (?:new )?native thread"
    ),
    "stack_overflow": r"\bStackOverflowError\b",
    "resource": r"Too many open files|No space left on device|Disk quota exceeded",
}


def _chain(field: str) -> pl.Expr:
    return (
        pl.col("exception_chain")
        .list.eval(pl.element().struct.field(field).fill_null(""))
        .list.join(_SEP)
        .fill_null("")
    )


def _kind() -> pl.Expr:
    types = _chain("type")
    text = pl.concat_str([pl.col("template").fill_null(""), _chain("message")], separator="\n")
    # built from the least grave up, so the most grave match wins
    expr: pl.Expr = pl.when(pl.col("level") >= 60).then(pl.lit("fatal")).otherwise(pl.lit(None))
    for kind in reversed(GRAVE_KINDS[:-1]):
        cond = pl.lit(False)
        if kind in _TYPES:  # a whole type of the chain: ends with the name
            cond = cond | types.str.contains(_TYPES[kind] + f"(?:{_SEP}|$)")
        if kind in _TEXT:
            cond = cond | text.str.contains(_TEXT[kind])
        expr = pl.when(cond).then(pl.lit(kind)).otherwise(expr)
    return expr.alias("kind")


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _text(kind: str, types: str, messages: str, template: str | None) -> str:
    """Return the grave exception's type and its own message, else the template's text."""
    ts, ms = types.split(_SEP), messages.split(_SEP)
    pattern = _TYPES.get(kind)
    for i, name in enumerate(ts):
        if name and (pattern is None or re.search(pattern + "$", name)):
            msg = ms[i] if i < len(ms) else ""
            return f"{name}: {msg}" if msg else name
    return template or ""


def grave_markers(
    logset: LogSet,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = MAX_MARKERS,
) -> list[dict[str, Any]]:
    """Return the grave-error markers of ``logset`` in [since, until), by time.

    Each is ``{"kind", "ts", "last_ts", "count", "signature", "entry_id", "text"}``: the
    first entry's time, signature, id and text (the grave exception's type and its message,
    else the first exception's, else the template; at most 200 characters), the last entry's
    time and how many entries folded into it (:data:`FOLD_MS`). At most ``limit``, those
    with the most entries.
    """
    lf = logset.entries_lazy().filter(pl.col("ts").is_not_null() & has_time())
    if since is not None:
        lf = lf.filter(pl.col("ts") >= since)
    if until is not None:
        lf = lf.filter(pl.col("ts") < until)
    df = (
        lf.select(
            "ts",
            "id",
            "signature",
            "template",
            _chain("type").alias("types"),
            _chain("message").alias("messages"),
            _kind(),
        )
        .filter(pl.col("kind").is_not_null())
        .sort("ts")
        .collect()
    )
    last: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for ts, entry_id, sig, tpl, types, messages, kind in df.iter_rows():
        cur = last.get(kind)
        if cur is not None and (ts - cur["_last"]).total_seconds() * 1000 <= FOLD_MS:
            cur["_last"] = ts
            cur["count"] += 1
            continue
        cur = {
            "kind": kind,
            "ts": _iso(ts),
            "count": 1,
            "signature": sig,
            "entry_id": entry_id,
            "text": _text(kind, types, messages, tpl)[:200],
            "_last": ts,
        }
        last[kind] = cur
        out.append(cur)
    for m in out:
        m["last_ts"] = _iso(m.pop("_last"))
    if len(out) > limit:
        keep = sorted(out, key=lambda m: -m["count"])[:limit]
        out = sorted(keep, key=lambda m: m["ts"])
    keys = ("kind", "ts", "last_ts", "count", "signature", "entry_id", "text")
    return [{k: m[k] for k in keys} for m in out]
