"""Triage: "are there new kinds of errors since the baseline?" in one document (``triage.v1``).

Shared by ``atlas-log triage`` and ``GET /api/sets/{ref}/triage``; the web app
adds its ``report_url`` links on top of :func:`triage`'s result.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

import polars as pl

from atlas_log_analyzer.store.logset import LogSet, LogSetManifest, to_records
from atlas_log_analyzer.store.manifest import current_recipe_key
from atlas_log_analyzer.store.query import (
    _span_text,
    coverage_warning,
    diff,
    manifest_span_hours,
    span_hours,
    with_rates,
)

__all__ = [
    "MAX_LISTED_CANDIDATES",
    "MAX_SPAN_RATIO",
    "MIN_ENTRY_SHARE",
    "REJECTION_REASONS",
    "VERDICTS",
    "BaselineChoice",
    "Rejection",
    "explicit_baseline",
    "rank_baselines",
    "triage",
]

VERDICTS = ("new_errors", "new_warnings", "new_exceptions", "clean", "no_baseline")
"""Values of ``triage.v1`` ``verdict``."""

MIN_ENTRY_SHARE = 0.01
"""A baseline needs at least this share of the set's entries."""
MAX_SPAN_RATIO = 16.0
"""A baseline's span may differ from the set's by at most this factor either way."""
MAX_LISTED_CANDIDATES = 5
"""Rejected sets listed in ``baseline.candidates``."""

REJECTION_REASONS = (
    "stale",
    "recipe",
    "other_node",
    "superset",
    "subset",
    "later",
    "tiny",
    "span",
)
"""Values of ``baseline.candidates[].reason``, in the order the checks run."""


@dataclass(frozen=True, slots=True)
class Rejection:
    """A set that cannot serve as the baseline of another, and why."""

    manifest: LogSetManifest
    reason: str
    """One of :data:`REJECTION_REASONS`."""
    detail: str
    """One sentence for people."""
    hint: str | None = None
    """A command that would make the set usable (``atlas-log upgrade …``)."""

    def item(self) -> dict[str, Any]:
        """Return the ``baseline.candidates`` entry: ``{id, name, reason, detail[, hint]}``."""
        out: dict[str, Any] = {
            "id": self.manifest.id,
            "name": self.manifest.name,
            "reason": self.reason,
            "detail": self.detail,
        }
        if self.hint is not None:
            out["hint"] = self.hint
        return out


@dataclass(frozen=True, slots=True)
class BaselineChoice:
    """The baseline of a set, with the reasons that ``triage.v1`` reports."""

    manifest: LogSetManifest | None
    """The chosen baseline; ``None`` when no set qualifies (verdict ``no_baseline``)."""
    resolved_by: Literal["auto", "param"]
    reason: str | None = None
    """Why this set was chosen, in one line."""
    shared_sources: int = 0
    """Source files (by digest) the baseline shares with the set."""
    rejected: tuple[Rejection, ...] = ()
    """Sets of the same format that were not eligible, newest first."""
    warnings: tuple[str, ...] = field(default=())


def _ref(m: LogSetManifest) -> str:
    return m.name or m.id


def _digests(m: LogSetManifest) -> set[str]:
    return {s.xxh3 for s in m.sources}


def _stale(m: LogSetManifest, current: dict[tuple[str, str], str | None]) -> bool:
    key = (m.format, m.signature_spec)
    if key not in current:
        current[key] = current_recipe_key(*key)
    cur = current[key]
    return cur is not None and cur != m.recipe_key


def _recipe_rejection(
    me: LogSetManifest, other: LogSetManifest, current: dict[tuple[str, str], str | None]
) -> Rejection | None:
    if other.recipe_key == me.recipe_key:
        return None
    from atlas_log_analyzer.core.pipeline import recipe_differences

    detail = "signed differently: " + "; ".join(recipe_differences(me.recipe_key, other.recipe_key))
    for stale in (other, me):
        if _stale(stale, current):
            who = "it" if stale is other else "this set"
            return Rejection(
                other,
                "stale",
                f"{detail} ({who} was built with an older recipe)",
                f"atlas-log upgrade {_ref(stale)}",
            )
    return Rejection(
        other,
        "recipe",
        detail,
        f"atlas-log regroup {_ref(other)} with this set's spec {me.signature_spec}",
    )


def _node_rejection(me: LogSetManifest, other: LogSetManifest) -> Rejection | None:
    if me.node is None or other.node is None or me.node == other.node:
        return None
    return Rejection(
        other, "other_node", f"logs of node {other.node}, while this set is node {me.node}"
    )


def _containment(me: LogSetManifest, other: LogSetManifest) -> Rejection | None:
    mine, theirs = _digests(me), _digests(other)
    if not mine or not theirs:
        return None
    if theirs >= mine:
        return Rejection(
            other,
            "superset",
            f"its sources include all {len(mine)} source file(s) of this set, so every group "
            "of this set is in it",
        )
    if theirs <= mine:
        return Rejection(
            other, "subset", f"all {len(theirs)} of its source file(s) are part of this set"
        )
    return None


def _size_and_span(me: LogSetManifest, other: LogSetManifest) -> Rejection | None:
    if other.entry_count == 0 or other.entry_count < MIN_ENTRY_SHARE * me.entry_count:
        return Rejection(
            other,
            "tiny",
            f"{other.entry_count:,} entries, under {MIN_ENTRY_SHARE:.0%} of this set's "
            f"{me.entry_count:,}",
        )
    h_me, h_other = manifest_span_hours(me), manifest_span_hours(other)
    if h_me > 0 and h_other > 0:
        ratio = h_other / h_me
        if not 1 / MAX_SPAN_RATIO <= ratio <= MAX_SPAN_RATIO:
            factor = max(ratio, 1 / ratio)
            return Rejection(
                other,
                "span",
                f"covers {_span_text(h_other)} against this set's {_span_text(h_me)} "
                f"({factor:.1f}x, more than {MAX_SPAN_RATIO:g}x)",
            )
    return None


def _rejection(
    me: LogSetManifest, other: LogSetManifest, current: dict[tuple[str, str], str | None]
) -> Rejection | None:
    """Return the first rule ``other`` fails as a baseline of ``me`` (``None``: eligible)."""
    found = (
        _recipe_rejection(me, other, current)
        or _node_rejection(me, other)
        or _containment(me, other)
    )
    if found is not None:
        return found
    if me.last_ts is not None and other.last_ts is not None and other.last_ts > me.last_ts:
        return Rejection(other, "later", "ends after this set ends")
    return _size_and_span(me, other)


def _log_ratio(me: LogSetManifest, other: LogSetManifest) -> float:
    h_me, h_other = manifest_span_hours(me), manifest_span_hours(other)
    if h_me <= 0 or h_other <= 0:
        return math.inf
    return abs(math.log(h_other / h_me))


def _no_baseline_warning(me: LogSetManifest, rejected: list[Rejection]) -> str:
    shown = "; ".join(
        f"{_ref(r.manifest)} ({r.reason}: {r.detail})" for r in rejected[:MAX_LISTED_CANDIDATES]
    )
    more = len(rejected) - MAX_LISTED_CANDIDATES
    tail = f"; and {more} more" if more > 0 else ""
    return f"No baseline: none of the other {me.format} sets qualifies: {shown}{tail}"


def rank_baselines(manifests: Iterable[LogSetManifest], me: LogSetManifest) -> BaselineChoice:
    """Choose the automatic baseline of the set ``me`` among ``manifests``.

    The rules, in order:

    1. Candidates are the other sets of the same format.
    2. A candidate must have the same ``recipe_key``; otherwise it is rejected as
       ``stale`` (one of the two was built with an older recipe; the hint names
       ``atlas-log upgrade``) or ``recipe`` (another spec or extra rules).
    3. When both sets have a ``node``, a candidate of another node is rejected
       (``other_node``): each node is compared with its own past.
    4. Containment: a candidate whose source digests are a superset or subset of
       this set's is rejected (``superset``/``subset``): an overlapping snapshot of
       the same files is no baseline. Partial overlap (rolling snapshots share most
       rotated files) is allowed and reported as ``shared_sources``.
    5. A candidate that ends after this set ends is rejected (``later``).
    6. A candidate with fewer than :data:`MIN_ENTRY_SHARE` of this set's entries is
       rejected (``tiny``), as is one whose span differs by more than
       :data:`MAX_SPAN_RATIO` either way (``span``).
    7. Of the rest, prefer a set that ends before this one starts, then (when this set
       has a node) one of the same node over one without, then the span ratio closest
       to 1 (``|log(h_a / h_b)|``, rounded to 0.1 so spans within about 10% count as
       equal), then the newest (``last_ts``, then ``created_at``).

    Returns:
        The choice; its ``manifest`` is ``None`` when nothing qualifies, and then
        ``warnings`` says which sets were rejected and why.

    """
    start = me.first_ts
    if start is None or me.last_ts is None:
        return BaselineChoice(None, "auto")
    current: dict[tuple[str, str], str | None] = {}
    rejected: list[Rejection] = []
    eligible: list[LogSetManifest] = []
    for other in manifests:
        if other.id == me.id or other.format != me.format:
            continue
        found = _rejection(me, other, current)
        if found is None:
            eligible.append(other)
        else:
            rejected.append(found)
    rejected.sort(key=lambda r: (str(r.manifest.last_ts), str(r.manifest.created_at)), reverse=True)
    if not eligible:
        warnings = (_no_baseline_warning(me, rejected),) if rejected else ()
        return BaselineChoice(None, "auto", rejected=tuple(rejected), warnings=warnings)

    def ends_before(m: LogSetManifest) -> bool:
        return m.last_ts is not None and m.last_ts <= start

    def same_node(m: LogSetManifest) -> bool:
        return me.node is not None and m.node == me.node

    def preference(m: LogSetManifest) -> tuple[int, int, float, float, float]:
        last = m.last_ts.timestamp() if m.last_ts is not None else float("-inf")
        ratio = round(_log_ratio(me, m), 1)
        node_rank = 0 if me.node is None or same_node(m) else 1
        return (0 if ends_before(m) else 1, node_rank, ratio, -last, -m.created_at.timestamp())

    best = min(eligible, key=preference)
    shared = len(_digests(me) & _digests(best))
    parts = [
        "same recipe",
        "ends before this set starts"
        if ends_before(best)
        else "overlaps this set (no earlier set qualifies)",
        *([f"same node {me.node}"] if same_node(best) else []),
        f"span {_span_text(manifest_span_hours(best))} vs {_span_text(manifest_span_hours(me))}",
    ]
    if shared:
        parts.append(f"shares {shared} of this set's {len(_digests(me))} source files")
    if len(eligible) > 1:
        parts.append(f"best of {len(eligible)} eligible sets")
    return BaselineChoice(
        best, "auto", reason="; ".join(parts), shared_sources=shared, rejected=tuple(rejected)
    )


def explicit_baseline(
    me: LogSetManifest,
    other: LogSetManifest,
    *,
    resolved_by: Literal["auto", "param"] = "param",
) -> BaselineChoice:
    """Describe a baseline given by the caller; warn when it is stale, contained or tiny.

    The comparison still runs (a set signed differently fails in :func:`diff`).
    """
    shared = len(_digests(me) & _digests(other))
    if other.id == me.id:
        return BaselineChoice(
            other, resolved_by, reason="given by the caller (the set itself)", shared_sources=shared
        )
    current: dict[tuple[str, str], str | None] = {}
    warnings: list[str] = []
    if _stale(other, current):
        warnings.append(
            f"Baseline {_ref(other)} was built with an older recipe ({other.recipe_key}); "
            f"rebuild it with `atlas-log upgrade {_ref(other)}`"
        )
    contained = _containment(me, other)
    if contained is not None:
        warnings.append(
            f"Baseline {_ref(other)} is a {contained.reason} of this set's sources "
            f"({contained.detail}): new and vanished groups mean little"
        )
    size = _size_and_span(me, other)
    if size is not None and size.reason == "tiny":
        warnings.append(f"Baseline {_ref(other)} is tiny ({size.detail}): most groups look new")
    return BaselineChoice(
        other,
        resolved_by,
        reason="given by the caller",
        shared_sources=shared,
        warnings=tuple(warnings),
    )


def _set_info(m: LogSetManifest) -> dict[str, Any]:
    """Identity and span of a set; timestamps as in its ``logset.v1`` manifest."""
    d = m.to_dict()
    return {
        "id": m.id,
        "name": m.name,
        "first_ts": d["first_ts"],
        "last_ts": d["last_ts"],
        "entries": m.entry_count,
    }


def _new_rows(frame: pl.DataFrame, hours: float) -> list[dict[str, Any]]:
    """Rows of a ``new`` frame, most severe first, then by count, with ``rate_per_hour``."""
    rate = (pl.col("count_b") / hours).round(3) if hours > 0 else pl.lit(None, pl.Float64)
    ordered = frame.sort(["level", "count_b", "signature"], descending=[True, True, False])
    return to_records(ordered.with_columns(rate.alias("rate_per_hour")))


def triage(
    ls: LogSet,
    baseline: LogSet | None,
    *,
    min_level: str | None = "WARN",
    grow: float = 2.0,
    limit: int | None = 50,
    resolved_by: Literal["auto", "param"] = "param",
    choice: BaselineChoice | None = None,
) -> dict[str, Any]:
    """Compare ``ls`` with ``baseline`` and summarise what is new, grew or vanished.

    Args:
        ls: The set to judge.
        baseline: The earlier set to compare with; ``None`` gives verdict ``no_baseline``.
        min_level: Only groups of this level or more severe count for ``new``, ``grew``
            and ``vanished`` (``None`` = all levels).
        grow: ``grew`` lists changed groups whose hourly rate rose at least this factor.
        limit: Maximum rows per list (``new``, ``new_exceptions``, ``grew``, ``vanished``);
            ``None`` or ``0`` = all. ``summary`` always counts all rows.
        resolved_by: Recorded as ``baseline.resolved_by`` (``auto`` or ``param``) when
            ``choice`` is not given.
        choice: How the baseline was chosen (:func:`rank_baselines` or
            :func:`explicit_baseline`); its reason, rejected candidates and warnings go
            into the document. Default: :func:`explicit_baseline` of ``baseline``.

    Returns:
        The JSON-ready ``triage.v1`` document (``set``/``baseline`` timestamps as in
        their manifests, row timestamps as in ``diff``; ``grew`` rows carry
        ``rate_a``, ``rate_b`` and ``rate_ratio`` as ``diff --rate``).
        ``verdict``: ``new_errors`` (a new ERROR/FATAL group),
        ``new_warnings`` (other new groups), ``new_exceptions`` (no new group at
        ``min_level`` but a new group of a lower level carrying an exception),
        ``clean`` or ``no_baseline``. ``coverage`` compares the spans of both sets
        and ``warnings`` explains when they differ by more than 4x; it also holds the
        choice's warnings (rejected candidates, a stale, contained or tiny baseline).
        ``baseline`` adds ``reason``, ``entries``, ``shared_sources`` and
        ``candidates`` (up to :data:`MAX_LISTED_CANDIDATES` rejected sets).

    Raises:
        ValueError: The sets were signed differently, or an unknown level.

    """
    me = ls.manifest
    if choice is None:
        choice = (
            explicit_baseline(me, baseline.manifest, resolved_by=resolved_by)
            if baseline is not None
            else BaselineChoice(None, resolved_by)
        )
    hours_b = span_hours(ls)
    head: dict[str, Any] = {"schema": "triage.v1", "set": _set_info(me), "min_level": min_level}
    if baseline is None:
        return {
            **head,
            "baseline": None,
            "verdict": "no_baseline",
            "summary": None,
            "coverage": {"hours_baseline": None, "hours_set": round(hours_b, 3), "ratio": None},
            "warnings": list(choice.warnings),
            "new": [],
            "new_exceptions": [],
            "grew": [],
            "vanished": [],
        }
    result = diff(baseline, ls, min_level=min_level)
    hours_a = span_hours(baseline)
    new = _new_rows(result.new, hours_b)
    new_exceptions: list[dict[str, Any]] = []
    if min_level is not None:
        known = set(result.new["signature"].to_list())
        everything = diff(baseline, ls).new
        extra = everything.filter(
            pl.col("exception_type").is_not_null() & ~pl.col("signature").is_in(list(known))
        )
        new_exceptions = _new_rows(extra, hours_b)
    changed = result.changed.height
    grew = to_records(
        with_rates(result.changed, hours_a, hours_b).filter(pl.col("rate_ratio") >= grow)
    )
    by_level: dict[str, int] = {}
    for row in new:
        by_level[row["level_name"]] = by_level.get(row["level_name"], 0) + 1
    errors = sum(n for k, n in by_level.items() if k in ("ERROR", "FATAL"))
    if errors:
        verdict = "new_errors"
    elif new:
        verdict = "new_warnings"
    elif new_exceptions:
        verdict = "new_exceptions"
    else:
        verdict = "clean"
    vanished = to_records(result.vanished)
    warning = coverage_warning(hours_a, hours_b, label_a="the baseline", label_b="the set")
    cut = limit or None
    info = _set_info(baseline.manifest)
    return {
        **head,
        "baseline": {
            "id": info["id"],
            "name": info["name"],
            "first_ts": info["first_ts"],
            "last_ts": info["last_ts"],
            "resolved_by": choice.resolved_by,
            "reason": choice.reason,
            "entries": info["entries"],
            "shared_sources": choice.shared_sources,
            "candidates": [r.item() for r in choice.rejected[:MAX_LISTED_CANDIDATES]],
        },
        "verdict": verdict,
        "summary": {
            "new": len(new),
            "new_by_level": by_level,
            "new_exceptions": len(new_exceptions),
            "grew": len(grew),
            "vanished": len(vanished),
            "changed": changed,
            "unchanged": result.unchanged_count,
        },
        "coverage": {
            "hours_baseline": round(hours_a, 3),
            "hours_set": round(hours_b, 3),
            "ratio": round(hours_b / hours_a, 3) if hours_a else None,
        },
        "warnings": [*choice.warnings, *([warning] if warning else [])],
        "new": new[:cut],
        "new_exceptions": new_exceptions[:cut],
        "grew": grew[:cut],
        "vanished": vanished[:cut],
    }
