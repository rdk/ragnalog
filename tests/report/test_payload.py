"""``report.payload.build_payload`` over the Jira fixtures."""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timedelta
from typing import Any

import pytest

from atlas_log_analyzer.report import build_payload
from atlas_log_analyzer.report.payload import LEVEL_BUCKETS
from atlas_log_analyzer.store import Store

TOP_KEYS = {
    "schema", "generated_at", "tool_version", "options", "logset", "stats", "timeline",
    "groups", "entries", "merges", "compare",
}  # fmt: skip
GROUP_KEYS = {
    "signature", "template", "level", "level_name", "logger",
    "exception_type", "count", "first_seen", "last_seen", "user_count", "thread_count",
    "source_count", "top_users", "top_threads", "sources", "examples", "hourly",
}  # fmt: skip
ENTRY_KEYS = {"ts", "level_name", "thread", "logger", "message", "raw", "source", "source_line"}
OPTIONAL_ENTRY_KEYS = {"user", "request_id", "ip", "url", "variables", "raw_truncated"}


@pytest.fixture(scope="module")
def payload(store: Store) -> dict[str, Any]:
    return build_payload(store.open("sample"))


def test_top_level_keys(payload: dict[str, Any], store: Store) -> None:
    assert set(payload) == TOP_KEYS
    assert payload["schema"] == "report.v1"
    assert payload["generated_at"].endswith("Z")
    assert payload["logset"]["schema"] == "logset.v1"
    assert payload["logset"]["sources"], "manifest keeps its sources"
    assert "sources" not in payload["stats"]
    ls = store.open("sample")
    assert payload["stats"]["entry_count"] == ls.manifest.entry_count
    assert payload["compare"] is None
    assert payload["options"] == {
        "min_level": None,
        "examples": 3,
        "max_raw": 4000,
        "max_groups": None,
        "truncated_groups": 0,
    }
    assert "entries_url" not in payload


def test_json_round_trip(payload: dict[str, Any]) -> None:
    text = json.dumps(payload, allow_nan=False)
    assert json.loads(text) == payload


def test_groups_are_sorted_and_complete(payload: dict[str, Any], store: Store) -> None:
    groups = payload["groups"]
    assert len(groups) == store.open("sample").manifest.group_count
    n_sources = len(payload["logset"]["sources"])
    for g in groups:
        assert GROUP_KEYS <= set(g) <= GROUP_KEYS | {"coarse_signature"}
        assert g.get("coarse_signature") != g["signature"]
        assert isinstance(g["level_name"], str)
        for key in ("top_users", "top_threads"):
            assert all(isinstance(v, str) and isinstance(n, int) for v, n in g[key])
        assert g["sources"]
        assert all(0 <= i < n_sources and n > 0 for i, n in g["sources"])
        assert sum(n for _, n in g["sources"]) == g["count"]
    counts = [g["count"] for g in groups]
    assert counts == sorted(counts, reverse=True)
    assert sum(counts) == payload["stats"]["entry_count"]


def test_max_groups_caps(store: Store) -> None:
    p = build_payload(store.open("sample"), max_groups=3)
    full = build_payload(store.open("sample"))
    assert p["options"]["truncated_groups"] == len(full["groups"]) - 3
    assert (
        build_payload(store.open("sample"), max_groups=10_000)["options"]["truncated_groups"] == 0
    )
    for g in p["groups"]:
        assert sum(c for _, c in g["hourly"]) == g["count"]
    assert [g["signature"] for g in p["groups"]] == [g["signature"] for g in full["groups"][:3]]
    present = {g["signature"] for g in p["groups"]}
    assert set(p["entries"]) == {i for g in p["groups"] for i in g["examples"]}
    for m in p["merges"]:
        assert set(m["signatures"]) <= present and len(m["signatures"]) >= 2


def test_timeline_is_contiguous(payload: dict[str, Any]) -> None:
    tl = payload["timeline"]
    hours = tl["hours"]
    assert hours
    assert set(tl["levels"]) == set(LEVEL_BUCKETS)
    for series in tl["levels"].values():
        assert len(series) == len(hours)
    parsed = [datetime.fromisoformat(h) for h in hours]
    assert all(b - a == timedelta(hours=1) for a, b in itertools.pairwise(parsed))
    total = sum(sum(s) for s in tl["levels"].values())
    assert total == payload["stats"]["entry_count"]
    levels = payload["stats"]["levels"]
    assert sum(tl["levels"]["ERROR"]) == levels.get("ERROR", 0) + levels.get("FATAL", 0)
    assert sum(tl["levels"]["WARN"]) == levels.get("WARN", 0)


def test_hourly_indices(payload: dict[str, Any]) -> None:
    n = len(payload["timeline"]["hours"])
    for g in payload["groups"]:
        idx = [i for i, _ in g["hourly"]]
        assert idx == sorted(set(idx))
        assert all(0 <= i < n for i in idx)
        assert sum(c for _, c in g["hourly"]) == g["count"]


def test_examples_and_entries(payload: dict[str, Any]) -> None:
    entries = payload["entries"]
    assert entries
    for g in payload["groups"]:
        assert 1 <= len(g["examples"]) <= 3
        assert all(i in entries for i in g["examples"])
    n_sources = len(payload["logset"]["sources"])
    for e in entries.values():
        assert ENTRY_KEYS <= set(e) <= ENTRY_KEYS | OPTIONAL_ENTRY_KEYS
        for key in OPTIONAL_ENTRY_KEYS & set(e):
            assert e[key], f"{key} present but empty"
        assert e.get("raw_truncated", True) is True
        assert len(e["raw"]) <= 4000
        assert 0 <= e["source"] < n_sources
        assert isinstance(e["source_line"], int)


def test_entry_source_index_matches_store(payload: dict[str, Any], store: Store) -> None:
    ls = store.open("sample")
    paths = [s["path"] for s in payload["logset"]["sources"]]
    for entry_id, e in payload["entries"].items():
        original = ls.entry(entry_id)
        assert original is not None
        assert paths[e["source"]] == original["source_path"]


def test_empty_keys_are_omitted(payload: dict[str, Any], store: Store) -> None:
    ls = store.open("sample")
    seen_missing = set()
    for entry_id, e in payload["entries"].items():
        original = ls.entry(entry_id)
        assert original is not None
        assert ("raw_truncated" in e) is (len(original["raw"]) > 4000)
        for key in ("user", "request_id", "ip", "url", "variables"):
            if original[key]:
                assert e[key] == original[key]
            else:
                assert key not in e
                seen_missing.add(key)
    assert seen_missing, "the fixture has entries without some optional fields"


def test_examples_option(store: Store) -> None:
    p = build_payload(store.open("sample"), examples=1)
    assert all(len(g["examples"]) <= 1 for g in p["groups"])
    none = build_payload(store.open("sample"), examples=0)
    assert none["entries"] == {}
    assert all(g["examples"] == [] for g in none["groups"])


def test_max_raw_truncates(store: Store) -> None:
    ls = store.open("sample")
    p = build_payload(ls, max_raw=60)
    truncated = 0
    for entry_id, e in p["entries"].items():
        original = ls.entry(entry_id)
        assert original is not None
        assert len(e["raw"]) <= 60
        assert e["raw"] == original["raw"][:60]
        cut = len(original["raw"]) > 60
        assert e.get("raw_truncated", False) is cut
        truncated += cut
    assert truncated > 0
    unlimited = build_payload(ls, max_raw=0)
    assert not any("raw_truncated" in e for e in unlimited["entries"].values())


def test_min_level_filters_groups(store: Store) -> None:
    p = build_payload(store.open("sample"), min_level="ERROR")
    assert p["groups"]
    assert all(g["level"] >= 50 for g in p["groups"])
    # the overview still covers the whole set
    assert p["stats"]["entry_count"] == store.open("sample").manifest.entry_count


def test_merges(payload: dict[str, Any]) -> None:
    present = {g["signature"] for g in payload["groups"]}
    for m in payload["merges"]:
        assert set(m) == {"cluster_id", "drain_template", "signatures"}
        assert set(m["signatures"]) <= present


def test_compare_block(store: Store) -> None:
    p = build_payload(store.open("sample"), compare=store.open("edge"))
    cmp = p["compare"]
    assert cmp is not None
    assert set(cmp) == {"other", "min_level", "counts", "new", "vanished", "changed"}
    assert cmp["other"]["name"] == "edge"
    assert "sources" not in cmp["other"]
    counts = {g["signature"]: g["count"] for g in p["groups"]}
    assert cmp["new"], "sample.log has groups that edge_cases.log lacks"
    for sig, count_b in cmp["new"]:
        assert counts[sig] == count_b > 0
    for sig, count_a, count_b in cmp["changed"]:
        assert counts[sig] == count_b and 0 < count_a != count_b
    assert set(cmp["counts"]) == {"new", "changed", "vanished", "unchanged"}
    # no max_groups: every new/changed signature is shown
    assert cmp["counts"]["new"] == len(cmp["new"])
    assert cmp["counts"]["changed"] == len(cmp["changed"])
    assert cmp["counts"]["vanished"] == len(cmp["vanished"])
    json.dumps(p, allow_nan=False)


def test_compare_vanished_rows(store: Store) -> None:
    # edge as this set, sample as the baseline: sample's own groups vanish
    p = build_payload(store.open("edge"), compare=store.open("sample"))
    cmp = p["compare"]
    assert cmp["vanished"]
    present = {g["signature"] for g in p["groups"]}
    for row in cmp["vanished"]:
        assert row["count_b"] == 0 and row["count_a"] > 0
        assert row["signature"] not in present
        assert {"signature", "template", "level_name", "first_seen_a"} <= set(row)
    assert cmp["counts"]["vanished"] == len(cmp["vanished"])


def test_compare_with_max_groups(store: Store) -> None:
    full = build_payload(store.open("sample"), compare=store.open("edge"))["compare"]
    p = build_payload(store.open("sample"), compare=store.open("edge"), max_groups=2)
    cmp = p["compare"]
    present = {g["signature"] for g in p["groups"]}
    assert cmp["counts"] == full["counts"], "counts are the true diff totals"
    assert len(cmp["new"]) < cmp["counts"]["new"]
    assert all(sig in present for sig, *_ in cmp["new"] + cmp["changed"])
    assert cmp["vanished"] == full["vanished"]


def test_entries_url(store: Store) -> None:
    url = "/api/sets/X/groups/{sig}/entries"
    p = build_payload(store.open("sample"), max_groups=1, entries_url=url)
    assert p["entries_url"] == url


def test_compare_respects_versions(store: Store) -> None:
    with pytest.raises(ValueError, match="not comparable"):
        build_payload(store.open("sample"), compare=store.open("edge-f2"))


@pytest.mark.parametrize("kwargs", [{"examples": -1}, {"max_raw": -1}, {"max_groups": -1}])
def test_invalid_options(store: Store, kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        build_payload(store.open("sample"), **kwargs)  # type: ignore[arg-type]
