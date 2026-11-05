"""``store.triage.rank_baselines`` / ``explicit_baseline`` on hand-made manifests."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

from atlas_log_analyzer.store.manifest import LogSetManifest, SourceInfo
from atlas_log_analyzer.store.triage import (
    MAX_LISTED_CANDIDATES,
    explicit_baseline,
    rank_baselines,
)

T0 = datetime(2026, 11, 1, tzinfo=UTC)
CURRENT = "jira@2|L+G+E+F0|sig1|mask1"


def m(
    name: str,
    start_h: float,
    hours: float,
    *,
    files: tuple[str, ...] | None = None,
    entries: int = 1000,
    key: str = CURRENT,
    spec: str = "L+G+E+F0",
    fmt: str = "jira",
    created_h: float = 0,
) -> LogSetManifest:
    first = T0 + timedelta(hours=start_h)
    last = first + timedelta(hours=hours)
    return LogSetManifest(
        id=f"id-{name}",
        name=name,
        format=fmt,
        sources=tuple(SourceInfo(f"/{d}.log", 1, d, first, last, 1) for d in files or (name,)),
        first_ts=first,
        last_ts=last,
        entry_count=entries,
        group_count=1,
        created_at=T0 + timedelta(hours=created_h),
        tool_version="0",
        schema_version="1.0",
        signature_version=1,
        mask_rules_version=1,
        signature_spec=spec,
        recipe_key=key,
    )


ME = m("me", 100, 8, files=("a", "b", "c"))


def reasons(manifests: list[LogSetManifest]) -> dict[str, str]:
    return {r.manifest.name or "": r.reason for r in rank_baselines(manifests, ME).rejected}


def test_rules_in_order() -> None:
    sets = [
        ME,
        m("stale", 80, 8, key="jira@1|L+G+E+F0|sig1|mask0"),
        m("frames", 80, 8, key="jira@2|L+G+E+F2|sig1|mask1", spec="L+G+E+F2"),
        m("super", 60, 48, files=("a", "b", "c", "d")),
        m("sub", 104, 4, files=("c",)),
        m("later", 104, 8),
        m("tiny", 80, 8, entries=9),
        m("long", -40, 8 * 17),
        m("short", 90, 0.4),
        m("access", 80, 8, fmt="access"),
    ]
    assert reasons(sets) == {
        "stale": "stale",
        "frames": "recipe",
        "super": "superset",
        "sub": "subset",
        "later": "later",
        "tiny": "tiny",
        "long": "span",
        "short": "span",
    }
    choice = rank_baselines(sets, ME)
    assert choice.manifest is None and choice.resolved_by == "auto"
    [warning] = choice.warnings
    assert warning.startswith("No baseline: none of the other jira sets qualifies: ")
    assert warning.endswith(f"; and {len(sets) - 2 - MAX_LISTED_CANDIDATES} more")
    stale = next(r for r in choice.rejected if r.reason == "stale")
    assert stale.hint == "atlas-log upgrade stale" and "mask_rules_version" in stale.detail
    assert stale.item() == {
        "id": "id-stale",
        "name": "stale",
        "reason": "stale",
        "detail": stale.detail,
        "hint": "atlas-log upgrade stale",
    }
    recipe = next(r for r in choice.rejected if r.reason == "recipe")
    assert recipe.hint is not None and "regroup frames" in recipe.hint


def test_a_stale_set_names_itself_in_the_hint() -> None:
    me = m("old", 100, 8, key="jira@1|L+G+E+F0|sig0|mask1")
    [rejection] = rank_baselines([me, m("new", 80, 8)], me).rejected
    assert rejection.reason == "stale" and rejection.hint == "atlas-log upgrade old"
    assert "this set was built with an older recipe" in rejection.detail


def test_partial_overlap_is_allowed_and_reported() -> None:
    rolling = m("rolling", 60, 8, files=("x", "a", "b"))
    choice = rank_baselines([ME, rolling], ME)
    assert choice.manifest is rolling and choice.shared_sources == 2
    assert choice.reason == (
        "same recipe; ends before this set starts; span 8.0 h vs 8.0 h; "
        "shares 2 of this set's 3 source files"
    )


def test_preference_before_then_span_then_newest() -> None:
    overlap = m("overlap", 99, 8.5)  # ends inside this set: eligible, but not "before"
    far = m("far", 10, 8.2)
    near = m("near", 70, 8.1)  # same rounded span ratio as far: the newer one wins
    odd = m("odd", 80, 2)  # earlier, but a quarter of the span
    choice = rank_baselines([ME, overlap, far, near, odd], ME)
    assert choice.manifest is near and choice.reason is not None
    assert choice.reason.endswith("best of 4 eligible sets")
    assert rank_baselines([ME, overlap, odd], ME).manifest is odd
    only = rank_baselines([ME, overlap], ME)
    assert only.manifest is overlap and only.reason is not None
    assert "overlaps this set (no earlier set qualifies)" in only.reason
    same = [m("x1", 70, 8, created_h=1), m("x2", 70, 8, created_h=2)]
    assert rank_baselines([ME, *same], ME).manifest is same[1]


def test_empty_set_has_no_baseline() -> None:
    empty = LogSetManifest(**{**_fields(ME), "first_ts": None, "last_ts": None})
    assert rank_baselines([empty, m("x", 0, 8)], empty).manifest is None


def _fields(x: LogSetManifest) -> dict[str, object]:
    return {f: getattr(x, f) for f in x.__dataclass_fields__}


def test_explicit_baseline_warnings() -> None:
    assert explicit_baseline(ME, ME).warnings == ()
    fine = explicit_baseline(ME, m("fine", 80, 8))
    assert fine.warnings == () and fine.resolved_by == "param" and fine.reason
    stale = explicit_baseline(ME, m("stale", 80, 8, key="jira@1|L+G+E+F0|sig1|mask0"))
    assert ["older recipe" in w and "atlas-log upgrade stale" in w for w in stale.warnings] == [
        True
    ]
    sub = explicit_baseline(ME, m("sub", 104, 4, files=("c",), entries=5))
    assert len(sub.warnings) == 2 and sub.shared_sources == 1
    assert "is a subset of this set's sources" in sub.warnings[0]
    assert "is tiny (5 entries, under 1% of this set's 1,000)" in sub.warnings[1]
    later = explicit_baseline(ME, m("later", 120, 8))
    assert later.warnings == ()  # an explicit later set is the caller's choice


# --- nodes (docs/cluster.md, §5)


def on(node: str | None, x: LogSetManifest) -> LogSetManifest:
    return dataclasses.replace(x, node=node)


def test_another_node_is_rejected_after_the_recipe_rules() -> None:
    me = on("NODE1", ME)
    other = on("NODE2", m("other", 80, 8))
    stale = on("NODE2", m("stale", 80, 8, key="jira@1|L+G+E+F0|sig1|mask0"))
    choice = rank_baselines([me, other, stale], me)
    assert choice.manifest is None
    found = {r.manifest.name: (r.reason, r.detail) for r in choice.rejected}
    assert found["stale"][0] == "stale"  # the recipe rules come first
    assert found["other"] == ("other_node", "logs of node NODE2, while this set is node NODE1")
    assert "other (other_node: logs of node NODE2" in choice.warnings[0]


def test_the_node_rule_needs_a_node_on_both_sides() -> None:
    assert rank_baselines([ME, on("NODE2", m("x", 80, 8))], ME).manifest is not None
    me = on("NODE1", ME)
    plain = m("plain", 80, 8)
    assert rank_baselines([me, plain], me).manifest is plain


def test_same_node_is_preferred_before_the_span_rule() -> None:
    me = on("NODE1", ME)
    plain = m("plain", 80, 8)  # node-less, best span
    mine = on("NODE1", m("mine", 70, 2))  # same node, worse span
    choice = rank_baselines([me, plain, mine], me)
    assert choice.manifest is mine and choice.reason is not None
    assert "same node NODE1" in choice.reason
    # "ends before" still comes first: an overlapping same-node set loses to an earlier one
    overlap = on("NODE1", m("overlap", 99, 8.5))
    assert rank_baselines([me, plain, overlap], me).manifest is plain
    # without a node on this set, nothing changes
    assert rank_baselines([ME, plain, on("NODE1", m("mine", 70, 2))], ME).manifest is plain


def test_explicit_baseline_of_another_node_is_used_as_given() -> None:
    choice = explicit_baseline(on("NODE1", ME), on("NODE2", m("other", 80, 8)))
    assert choice.manifest is not None and choice.warnings == ()
