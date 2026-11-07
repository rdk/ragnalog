"""Vectorised ``regroup``, ``upgrade``, recipe keys in manifests, and epoch-free time ranges."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from atlas_log_analyzer.core.formats.jira import JiraFormat
from atlas_log_analyzer.core.pipeline import Recipe
from atlas_log_analyzer.core.signature import SignatureSpec
from atlas_log_analyzer.store import LogSet, Store, diff
from atlas_log_analyzer.store.logset import MANIFEST_FILE
from atlas_log_analyzer.store.manifest import LogSetManifest, current_recipe_key
from atlas_log_analyzer.store.pipeline import ingest_paths
from atlas_log_analyzer.store.store import LogSetNotFound, StoreError
from atlas_log_analyzer.store.writer import EntriesWriter, iter_entries, write_groups

from ..cli.synth import PLUGIN_TRACE

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "jira"
MINI = Path(__file__).resolve().parents[1] / "cli" / "data" / "mini.log"


def ties_log(path: Path) -> Path:
    """Counter ties whose first-appearance order differs from the value order, empty users."""
    day = "2026-10-29 10:00:0{},000+0100"
    lines = [
        f"{day.format(0)} exec-9 ERROR zed      [org.acme.A] boom 1",
        f"{day.format(1)} exec-1 ERROR amy      [org.acme.A] boom 2",
        f"{day.format(2)} exec-5 ERROR kim      [org.acme.A] boom 3",
        f"{day.format(3)} exec-1 ERROR      [org.acme.A] boom 4",
        f"{day.format(4)} exec-9 ERROR      [org.acme.A] boom 5",
        f"{day.format(5)} exec-9 ERROR      [org.acme.A] boom 6\n{PLUGIN_TRACE}",
        f"{day.format(6)} exec-2 WARN      [org.acme.B] \n{PLUGIN_TRACE}",
        f"{day.format(7)} exec-2 WARN      [org.acme.B] \n{PLUGIN_TRACE}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def fixture_set(tmp_path: Path) -> tuple[Store, LogSet]:
    store = Store(tmp_path / "store")
    paths = [
        FIXTURES / "sample.log",
        FIXTURES / "edge_cases.log",
        MINI,
        ties_log(tmp_path / "t.log"),
    ]
    return store, ingest_paths(store, paths, name="fx").logset


def _object_regroup(ls: LogSet, spec: SignatureSpec, out: Path) -> tuple[pa.Table, pa.Table]:
    """The regroup this replaced: one LogEntry per row through a Grouper and the writer."""
    out.mkdir()
    grouper = Recipe(JiraFormat(), spec).grouper()
    with EntriesWriter(out / "entries.parquet", dedupe=False) as writer:
        writer.write(grouper.sign(iter_entries(ls.entries_path, "jira")))
    write_groups(out / "groups.parquet", grouper.groups.values())
    return pq.read_table(out / "entries.parquet"), pq.read_table(out / "groups.parquet")


@pytest.mark.parametrize(
    "spec",
    [
        SignatureSpec(),
        SignatureSpec(frames=2),
        SignatureSpec(include_exceptions=False),
        SignatureSpec(include_level=False, include_logger=False, frames=1),
    ],
    ids=lambda s: s.key,
)
def test_regroup_equals_object_based_regroup(
    fixture_set: tuple[Store, LogSet], spec: SignatureSpec, tmp_path: Path
) -> None:
    store, ls = fixture_set
    want_entries, want_groups = _object_regroup(ls, spec, tmp_path / "ref")
    got = store.regroup("fx", spec)
    entries = pq.read_table(got.entries_path)
    groups = pq.read_table(got.groups_path)
    assert entries.equals(want_entries)
    assert groups.equals(want_groups)
    assert got.manifest.recipe_key == Recipe(JiraFormat(), spec).key
    assert got.manifest.signature_spec == spec.key
    assert got.manifest.group_count == groups.num_rows
    assert (
        pq.ParquetFile(got.entries_path).num_row_groups
        == pq.ParquetFile(ls.entries_path).num_row_groups
    )


def test_groups_follow_most_common_order(fixture_set: tuple[Store, LogSet]) -> None:
    store, _ = fixture_set
    ls = store.regroup("fx", SignatureSpec())
    boom = ls.groups(contains="boom").filter(pl.col("exception_type").is_null())
    row = boom.row(0, named=True)
    assert row["count"] == 5
    assert [u["value"] for u in row["top_users"]] == ["zed", "amy", "kim"]  # ties: first seen
    assert [t["value"] for t in row["top_threads"]] == ["exec-9", "exec-1", "exec-5"]
    assert len(row["examples"]) == 3


def _rewrite_manifest(ls: LogSet, **changes: object) -> None:
    data = json.loads((ls.path / MANIFEST_FILE).read_text(encoding="utf-8"))
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    (ls.path / MANIFEST_FILE).write_text(json.dumps(data), encoding="utf-8")


def test_manifest_recipe_key_written_and_derived(fixture_set: tuple[Store, LogSet]) -> None:
    _, ls = fixture_set
    assert ls.manifest.recipe_key == "jira@3|L+G+E+F0|sig1|mask2"
    assert ls.manifest.to_dict()["recipe_key"] == ls.manifest.recipe_key
    _rewrite_manifest(ls, recipe_key=None, signature_spec="L+G+E+F3")
    old = LogSetManifest.read(ls.path)
    assert old.recipe_key == "jira@1|L+G+E+F3|sig1|mask2"  # derived, format version 1


def test_diff_compares_recipe_keys(fixture_set: tuple[Store, LogSet], tmp_path: Path) -> None:
    _, ls = fixture_set
    other = Store(tmp_path / "other")
    framed = ingest_paths(other, [MINI], name="mini", frames=2).logset
    with pytest.raises(ValueError, match="not comparable") as err:
        diff(ls, framed)
    assert "signature_spec: L+G+E+F0 != L+G+E+F2" in str(err.value)
    assert "upgrade" in str(err.value)
    _rewrite_manifest(ls, recipe_key="jira@1|L+G+E+F2|sig1|mask1|rules:00ff")
    with pytest.raises(ValueError, match=r"rules: none != 00ff"):
        diff(framed, Store(ls.path.parent).open("fx"))
    assert diff(ls, framed, force=True).a == ls.manifest.id


def test_upgrade_rebuilds_stale_sets(fixture_set: tuple[Store, LogSet]) -> None:
    store, ls = fixture_set
    groups_before = pq.read_table(ls.groups_path)
    _rewrite_manifest(ls, recipe_key="jira@1|L+G+E+F0|sig1|mask0", mask_rules_version=0)
    stale = store.open("fx").manifest
    assert current_recipe_key(stale.format, stale.signature_spec) != stale.recipe_key
    result = store.upgrade("fx")
    assert result.upgraded and result.reason is None
    assert result.previous_recipe_key == "jira@1|L+G+E+F0|sig1|mask0"
    assert result.manifest.recipe_key == "jira@3|L+G+E+F0|sig1|mask2"
    assert result.manifest.mask_rules_version == 2
    assert pq.read_table(store.open("fx").groups_path).equals(groups_before)
    again = store.upgrade("fx")
    assert not again.upgraded and again.reason == "current"


def test_upgrade_keeps_the_spec_and_skips_unknown_formats(
    fixture_set: tuple[Store, LogSet],
) -> None:
    store, _ = fixture_set
    store.regroup("fx", SignatureSpec(frames=2))
    _rewrite_manifest(store.open("fx"), recipe_key="jira@0|L+G+E+F2|sig1|mask1")
    result = store.upgrade("fx")
    assert result.upgraded and result.manifest.signature_spec == "L+G+E+F2"
    assert result.manifest.recipe_key == "jira@3|L+G+E+F2|sig1|mask2"
    _rewrite_manifest(store.open("fx"), format="nope", recipe_key=None)
    skipped = store.upgrade("fx")
    assert not skipped.upgraded and skipped.reason is not None and "unknown" in skipped.reason


def test_headerless_start_does_not_reach_back_to_1970(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "atlassian-jira.log"
    log.parent.mkdir()
    text = MINI.read_text(encoding="utf-8")
    log.write_text("\tat org.acme.Tail.run(Tail.java:1)\n... 3 more\n" + text, encoding="utf-8")
    ls = ingest_paths(Store(tmp_path / "store"), [log]).logset
    m = ls.manifest
    assert m.entry_count == 11  # the orphan lines are kept as one parse-error entry
    assert m.first_ts is not None and m.first_ts.year == 2026
    assert m.sources[0].first_ts == m.first_ts and m.sources[0].entries == 11
    plain = ingest_paths(Store(tmp_path / "plain"), [MINI]).logset.manifest
    assert (m.first_ts, m.last_ts) == (plain.first_ts, plain.last_ts)


def _during_rebuild(monkeypatch: pytest.MonkeyPatch, action: Callable[[], object]) -> None:
    """Run ``action`` while a regroup re-signs (after it read the set, before it installs)."""
    from atlas_log_analyzer.store import regroup

    real = regroup.resign_entries

    def resign(*args: Any, **kwargs: Any) -> int:
        count = real(*args, **kwargs)
        action()
        return count

    monkeypatch.setattr(regroup, "resign_entries", resign)


def test_regroup_keeps_labels_set_meanwhile(
    fixture_set: tuple[Store, LogSet], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _ = fixture_set
    _during_rebuild(monkeypatch, lambda: store.label("fx", node="NODE2", cluster="c1"))
    rebuilt = store.regroup("fx", SignatureSpec(frames=2))
    assert (rebuilt.manifest.node, rebuilt.manifest.cluster) == ("NODE2", "c1")
    stored = store.open("fx").manifest
    assert (stored.node, stored.signature_spec) == ("NODE2", "L+G+E+F2")


def test_regroup_does_not_bring_back_a_removed_set(
    fixture_set: tuple[Store, LogSet], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _ = fixture_set
    _during_rebuild(monkeypatch, lambda: store.remove("fx"))
    with pytest.raises(LogSetNotFound, match="removed"):
        store.regroup("fx", SignatureSpec(frames=2))
    assert store.list() == []
    assert not [p for p in store.root.iterdir() if p.name.startswith(".tmp-")]


def test_regroup_refuses_when_the_data_changed_meanwhile(
    fixture_set: tuple[Store, LogSet], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, ls = fixture_set
    _during_rebuild(monkeypatch, lambda: _rewrite_manifest(ls, entry_count=1))
    with pytest.raises(StoreError, match="changed while"):
        store.regroup("fx", SignatureSpec(frames=2))
    assert store.open("fx").manifest.signature_spec == "L+G+E+F0"
