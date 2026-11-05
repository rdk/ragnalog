"""JSON-ready views of the service layer, shared by the CLI and the web API.

Both front ends return the same documents: column presets, raw-text capping
(``entries.v1`` ``body_truncated``/``raw_chars``), the ``frames.v1`` body and
the ``diff.v1`` coverage warning live here so they cannot drift apart.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from atlas_log_analyzer.core.java import is_app_frame
from atlas_log_analyzer.service.params import DEFAULT_MAX_RAW
from atlas_log_analyzer.store.schema import ENTRIES_SCHEMA, GROUPS_SCHEMA

if TYPE_CHECKING:
    from atlas_log_analyzer.store.logset import LogSet

# polars and the log-set reader are imported where used: the CLI imports this module
# for its column presets, and commands that read no set should not load polars.
# DEFAULT_MAX_RAW lives in service.params (TextCap) and is re-exported here.

__all__ = [
    "DEFAULT_MAX_RAW",
    "DETAIL_BRIEF",
    "ENTRY_BRIEF",
    "ENTRY_FIELDS",
    "GROUP_BRIEF",
    "GROUP_FIELDS",
    "SET_BRIEF",
    "SIGNATURE_PREFIX_MIN",
    "AmbiguousSignature",
    "cap_text",
    "cap_texts",
    "diff_warning",
    "frames_view",
    "resolve_signature",
    "select_columns",
    "set_item",
    "set_summary",
]

ENTRY_FIELDS: tuple[str, ...] = tuple(ENTRIES_SCHEMA.names)
"""Columns of entries.v1."""
GROUP_FIELDS: tuple[str, ...] = tuple(GROUPS_SCHEMA.names)
"""Columns of groups.v1 (plus ``signatures`` for coarse groups)."""
GROUP_BRIEF: tuple[str, ...] = (
    "signature",
    "template",
    "level_name",
    "logger",
    "exception_type",
    "count",
    "first_seen",
    "last_seen",
)
"""Columns of ``groups --brief`` / ``brief=true``."""
ENTRY_BRIEF: tuple[str, ...] = (
    "id",
    "ts",
    "level_name",
    "thread",
    "user",
    "request_id",
    "logger",
    "message",
    "exception_type",
    "signature",
)
"""Columns of ``entries --brief`` / ``brief=true``."""
DETAIL_BRIEF: tuple[str, ...] = (*ENTRY_BRIEF, "variables")
"""Columns of ``show --brief`` and ``context --brief`` (and their API twins): the entry
preset plus the values masking replaced in the template."""

SET_BRIEF: tuple[str, ...] = (
    "id",
    "name",
    "first_ts",
    "last_ts",
    "entry_count",
    "group_count",
    "summary",
    "stale",
    "node",
    "capture",
)
"""Keys of a ``sets --brief`` / ``brief=true`` item (no ``sources``, no versions)."""


def set_summary(ls: LogSet | Path) -> dict[str, int]:
    """Entry counts by severity of a set, from its groups table.

    ``errors`` counts ERROR and FATAL entries, ``warnings`` WARN entries and
    ``error_groups`` the groups at ERROR or above. The ``summary`` of ``sets`` /
    ``set`` items and of ``GET /api/sets`` (the web app caches it per set version).
    ``ls`` may also be the set's directory (:meth:`Store.set_dir`): reading two
    columns with pyarrow loads no polars, which keeps ``atlas-log sets`` fast.
    """
    import pyarrow.parquet as pq

    from atlas_log_analyzer.store.manifest import GROUPS_FILE

    path = ls / GROUPS_FILE if isinstance(ls, Path) else ls.groups_path
    with pq.ParquetFile(path) as pf:  # unlike read_table, loads no pyarrow.compute/dataset
        table = pf.read(columns=["level", "count"])
    errors = warnings = error_groups = 0
    for level, count in zip(table["level"].to_pylist(), table["count"].to_pylist(), strict=True):
        if level >= 50:
            errors += count
            error_groups += 1
        elif level == 40:
            warnings += count
    return {"errors": errors, "warnings": warnings, "error_groups": error_groups}


def set_item(
    manifest: dict[str, Any], summary: dict[str, int] | None, *, brief: bool
) -> dict[str, Any]:
    """One ``sets.v1`` item: the manifest without ``schema`` plus ``summary`` and ``stale``.

    ``stale`` is true when the set's ``recipe_key`` differs from the recipe this tool
    signs its format and spec with today (rebuild it with ``atlas-log upgrade``).
    With ``brief`` only the :data:`SET_BRIEF` keys are kept.
    """
    from atlas_log_analyzer.store.manifest import current_recipe_key

    item = {k: v for k, v in manifest.items() if k != "schema"}
    item["summary"] = summary
    current = current_recipe_key(str(item.get("format")), str(item.get("signature_spec")))
    item["stale"] = current is not None and current != item.get("recipe_key")
    return {k: item.get(k) for k in SET_BRIEF} if brief else item


def select_columns(
    values: Sequence[str] | None, valid: Sequence[str]
) -> tuple[list[str] | None, list[str]]:
    """Parse column names (comma-separated and/or repeated); return ``(columns, unknown)``.

    ``columns`` is ``None`` when nothing was given; duplicates are dropped and the
    order is kept. ``unknown`` lists names not in ``valid`` (callers report them).
    """
    if not values:
        return None, []
    chosen: list[str] = []
    unknown: list[str] = []
    for value in values:
        for part in value.split(","):
            name = part.strip()
            if not name:
                continue
            if name not in valid:
                unknown.append(name)
            elif name not in chosen:
                chosen.append(name)
    return chosen or None, unknown


def cap_text(text: str, limit: int, how: str = "use --max-raw 0") -> str:
    """Cut ``text`` to ``limit`` characters and say so: ``… [truncated: N chars total, <how>]``."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… [truncated: {len(text)} chars total, {how}]"


def cap_texts(
    items: list[dict[str, Any]], limit: int | None, *, how: str = "use --max-raw 0"
) -> list[dict[str, Any]]:
    """Cut ``raw`` and ``body`` to ``limit`` characters in place; mark the rows that were cut.

    A cut text ends with ``… [truncated: N chars total, <how>]``; its row gets
    ``"body_truncated": true`` and ``"raw_chars"`` (the original length of ``raw``,
    or of ``body`` when ``raw`` is not among the columns). ``None`` = no limit.
    """
    if limit is None:
        return items
    for item in items:
        sizes: dict[str, int] = {}
        for key in ("raw", "body"):
            value = item.get(key)
            if isinstance(value, str) and len(value) > limit:
                sizes[key] = len(value)
                item[key] = cap_text(value, limit, how)
        if sizes:
            item["body_truncated"] = True
            raw = item.get("raw")
            if "raw" in sizes:
                item["raw_chars"] = sizes["raw"]
            elif isinstance(raw, str):
                item["raw_chars"] = len(raw)
            else:
                item["raw_chars"] = sizes["body"]
    return items


_HEX16 = re.compile(r"^[0-9a-f]{16}$")
_FRAME_HEAD = ["id", "ts", "level_name", "logger", "message", "signature", "exception_chain"]

SIGNATURE_PREFIX_MIN = 4
"""Shortest signature prefix that is resolved via the groups table."""
MAX_CANDIDATES = 5
"""Candidate signatures listed by :class:`AmbiguousSignature`."""
_PREFIX = re.compile(rf"^[0-9a-f]{{{SIGNATURE_PREFIX_MIN},15}}$")


class AmbiguousSignature(LookupError):
    """A signature prefix matches more than one group."""

    def __init__(self, prefix: str, logset: str, candidates: Sequence[str], total: int) -> None:
        """Record the prefix, the set id and the first matching signatures (sorted)."""
        self.prefix = prefix
        self.logset = logset
        self.candidates = list(candidates[:MAX_CANDIDATES])
        self.total = total
        more = f", … ({total} in all)" if total > len(self.candidates) else ""
        super().__init__(
            f"signature prefix {prefix!r} is ambiguous in log set {logset}: "
            f"{', '.join(self.candidates)}{more}"
        )


def resolve_signature(ls: LogSet, ref: str, *, coarse: bool = False) -> str:
    """Return the signature that ``ref`` stands for: itself, or the group of a unique prefix.

    ``ref`` of 4 to 15 hex characters is looked up as a prefix of ``signature``
    (``coarse_signature`` with ``coarse``) in the groups table; anything else,
    including a full 16-character signature, is returned unchanged, as is a
    prefix that matches nothing (callers report "not found" as for any unknown
    signature).

    Raises:
        AmbiguousSignature: The prefix matches several groups.

    """
    if not _PREFIX.match(ref):
        return ref
    import polars as pl

    column = "coarse_signature" if coarse else "signature"
    found = (
        ls.groups_lazy()
        .select(pl.col(column))
        .filter(pl.col(column).str.starts_with(ref))
        .unique()
        .sort(column)
        .collect()[column]
        .to_list()
    )
    if len(found) > 1:
        raise AmbiguousSignature(ref, ls.manifest.id, found, len(found))
    return str(found[0]) if found else ref


def _frame_entry(ls: LogSet, ref: str) -> dict[str, Any] | None:
    """Return the entry ``ref`` (an entry id), else the first example of the group ``ref``.

    A group may be given by a unique signature prefix (:func:`resolve_signature`).
    """
    import polars as pl

    from atlas_log_analyzer.store.logset import to_records

    def by_id(entry_id: str) -> dict[str, Any] | None:
        lf = ls.entries_lazy().filter(pl.col("id") == entry_id).select(_FRAME_HEAD)
        rows = to_records(lf.head(1).collect())
        return rows[0] if rows else None

    found = by_id(ref)
    if found is not None:
        return found
    ref = resolve_signature(ls, ref)
    if not _HEX16.match(ref):
        return None
    group = ls.group(ref)
    if group is None:
        return None
    for example in group.get("examples") or []:
        if (found := by_id(str(example))) is not None:
            return found
    rows = to_records(ls.entries(signature=ref, limit=1, columns=list(_FRAME_HEAD)))
    return rows[0] if rows else None


def _frame_dict(frame: dict[str, Any]) -> dict[str, Any]:
    return {"location": frame["location"], "file": frame.get("file"), "line": frame.get("line")}


def frames_view(
    ls: LogSet,
    ref: str,
    *,
    limit: int | None = 10,
    all_frames: bool = False,
    app_packages: Sequence[str] | None = None,
) -> dict[str, Any] | None:
    """Return the ``frames.v1`` document (without ``schema``) of an entry or group.

    ``ref`` is an entry id or a group signature or unique signature prefix of at
    least 4 hex characters (entry ids are tried first; for a signature the group's
    first example entry is used). Each exception of the chain
    lists its first ``limit`` application frames (``None`` = all): frames outside
    :data:`~atlas_log_analyzer.core.java.LIBRARY_PACKAGES`, or inside
    ``app_packages`` when given. ``all_frames`` adds every frame with an ``app``
    flag and returns all application frames. ``None`` when ``ref`` matches nothing.
    """
    entry = _frame_entry(ls, ref)
    if entry is None:
        return None
    items: list[dict[str, Any]] = []
    for exc in entry.get("exception_chain") or []:
        frames = list(exc.get("frames") or [])
        flagged = [(f, is_app_frame(str(f["location"]), app_packages)) for f in frames]
        apps = [_frame_dict(f) for f, app in flagged if app]
        item: dict[str, Any] = {
            "type": exc["type"],
            "message": exc.get("message"),
            "app_frames": apps if all_frames or limit is None else apps[:limit],
            "app_frames_total": len(apps),
            "frames_total": len(frames),
            "omitted_frames": int(exc.get("omitted_frames") or 0),
        }
        if all_frames:
            item["frames"] = [{**_frame_dict(f), "app": app} for f, app in flagged]
        items.append(item)
    return {
        "logset": ls.manifest.id,
        "entry": entry["id"],
        "signature": entry["signature"],
        "ts": entry["ts"],
        "level_name": entry["level_name"],
        "logger": entry["logger"],
        "message": entry["message"],
        "app_packages": list(app_packages) if app_packages else None,
        "count": len(items),
        "items": items,
    }


def diff_warning(a: LogSet, b: LogSet) -> str | None:
    """Return the ``diff.v1`` coverage warning for baseline ``a`` and ``b``, if any."""
    from atlas_log_analyzer.store.query import coverage_warning, span_hours

    return coverage_warning(
        span_hours(a),
        span_hours(b),
        label_a=f"set A ({a.manifest.name or a.manifest.id})",
        label_b=f"set B ({b.manifest.name or b.manifest.id})",
    )
