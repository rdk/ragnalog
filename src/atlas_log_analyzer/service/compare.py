"""Comparing sets: ``diff.v1`` and ``triage.v1`` (with the choice of a baseline).

Sets that were signed with different recipes are reason ``incompatible_sets``
(CLI ``invalid_value``, API ``incompatible_sets``); only ``diff`` takes ``force``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from atlas_log_analyzer.service.errors import Front, OpError, flag, hint
from atlas_log_analyzer.service.params import parse_level
from atlas_log_analyzer.service.sets import Sets, open_set
from atlas_log_analyzer.service.views import diff_warning

if TYPE_CHECKING:
    from atlas_log_analyzer.store.logset import LogSet
    from atlas_log_analyzer.store.triage import BaselineChoice

__all__ = ["DIFF_SECTIONS", "choose_baseline", "diff", "triage"]

type Doc = dict[str, Any]

DIFF_SECTIONS = ("new", "vanished", "changed")
"""The row lists of ``diff.v1``, in document order."""


def diff(
    sets: Sets,
    a: str,
    b: str,
    *,
    min_level: str | None = None,
    coarse: bool = False,
    force: bool = False,
    limit: int | None = 50,
    rate: bool = False,
    front: Front,
) -> Doc:
    """``diff.v1``: groups new in ``b``, vanished from ``a``, and changed in count.

    Each section holds at most ``limit`` rows (``None`` = all); ``totals`` counts
    them before the cut. ``rate`` adds hourly rates to ``changed`` and sorts it by
    their ratio. ``warnings`` holds the coverage warning when the spans differ by
    more than 4x.
    """
    from atlas_log_analyzer.store.logset import to_records
    from atlas_log_analyzer.store.query import IncompatibleSets, span_hours, with_rates
    from atlas_log_analyzer.store.query import diff as store_diff

    lvl = parse_level(min_level, "min_level", front=front)
    sa, sb = open_set(sets, a, front=front), open_set(sets, b, front=front)
    try:
        result = store_diff(sa, sb, min_level=lvl, coarse=coarse, force=force)
    except IncompatibleSets as exc:
        raise OpError.because(
            "incompatible_sets", front, str(exc), hint=hint("incompatible_sets", front)
        ) from exc
    changed = with_rates(result.changed, span_hours(sa), span_hours(sb)) if rate else result.changed
    frames = {"new": result.new, "vanished": result.vanished, "changed": changed}
    sections = {k: to_records(f if limit is None else f.head(limit)) for k, f in frames.items()}
    totals = {k: f.height for k, f in frames.items()}
    doc: Doc = {
        "schema": "diff.v1",
        "a": result.a,
        "b": result.b,
        **sections,
        "unchanged_count": result.unchanged_count,
        "totals": totals,
        "truncated": any(len(sections[k]) < totals[k] for k in totals),
    }
    warning = diff_warning(sa, sb)
    if warning is not None:
        doc["warnings"] = [warning]
    return doc


def choose_baseline(
    sets: Sets, ls: LogSet, ref: str | None, *, front: Front
) -> tuple[LogSet | None, BaselineChoice | None]:
    """Resolve a baseline reference: ``auto`` (ranked), ``none`` or blank (none), or a set."""
    from atlas_log_analyzer.store.triage import explicit_baseline, rank_baselines

    if ref is None or ref.strip() in ("", "none"):
        return None, None
    if ref == "auto":
        choice = rank_baselines(sets.manifests(), ls.manifest)
        found = choice.manifest
        return (open_set(sets, found.id, front=front) if found is not None else None), choice
    base = open_set(sets, ref, front=front)
    return base, explicit_baseline(ls.manifest, base.manifest)


def triage(
    sets: Sets,
    ref: str,
    *,
    baseline: str | None = "auto",
    min_level: str | None = "WARN",
    grow: float = 2.0,
    limit: int | None = 50,
    front: Front,
) -> Doc:
    """``triage.v1``: answer whether there are new kinds of errors since the baseline.

    ``baseline`` is ``auto`` (``store.triage.rank_baselines``), a set reference, or
    ``none``/blank for no baseline (verdict ``no_baseline``). The web API adds its
    ``report_url`` links after building.

    A capture reference is a ``usage`` error: per-node verdicts are part of
    ``capture.v1`` (docs/cluster.md, section 5).
    """
    from atlas_log_analyzer.service.captures import is_capture_ref
    from atlas_log_analyzer.store.query import IncompatibleSets
    from atlas_log_analyzer.store.triage import triage as store_triage

    if is_capture_ref(ref):
        raise OpError(
            "usage",
            f"triage judges one set, and {ref!r} is a capture",
            hint=(
                f"`atlas-log capture {ref} --triage` gives per-node verdicts"
                if front == "cli"
                else f"GET /api/captures/{ref}?triage=true gives per-node verdicts"
            ),
        )
    lvl = parse_level(min_level, "min_level", front=front)
    if not grow > 1:
        raise OpError("invalid_value", f"{flag('grow', front)} must be > 1")
    ls = open_set(sets, ref, front=front)
    base, choice = choose_baseline(sets, ls, baseline, front=front)
    try:
        return store_triage(ls, base, min_level=lvl, grow=grow, limit=limit, choice=choice)
    except IncompatibleSets as exc:
        raise OpError.because("incompatible_sets", front, str(exc)) from exc
