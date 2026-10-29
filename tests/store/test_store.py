from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest

from atlas_log_analyzer.core.models import LogLevel
from atlas_log_analyzer.core.signature import SignatureSpec
from atlas_log_analyzer.store import (
    LogSet,
    LogSetManifest,
    Store,
    StoreError,
    diff,
    rows,
    schema_json,
    to_records,
)
from atlas_log_analyzer.store.logset import ENTRIES_FILE, MANIFEST_FILE
from atlas_log_analyzer.store.schema import ENTRIES_SCHEMA, GROUPS_SCHEMA, SCHEMA_NAMES
from atlas_log_analyzer.store.store import logset_id
from atlas_log_analyzer.store.writer import iter_entries, write_merges

from .conftest import DUPLICATES, N_PER_SOURCE, PARSE_ERRORS, Built, build
from .synth import BASE, KINDS, WEIGHTS_A, generate, source_files

TOTAL_A = 3 * N_PER_SOURCE + PARSE_ERRORS


def ms(series: pl.Series) -> list[int]:
    """Datetime column as epoch ms (no tzdata needed on Windows)."""
    return series.dt.epoch("ms").to_list()


def ms_of(ts: datetime) -> int:
    return round(ts.timestamp() * 1000)


# --- manifest -----------------------------------------------------------------------------


def test_manifest_round_trip(built: Built) -> None:
    m = built.logset.manifest
    d = m.to_dict()
    assert d["schema"] == "logset.v1"
    text = json.dumps(d)
    assert LogSetManifest.from_dict(json.loads(text)) == m
    on_disk = json.loads((built.logset.path / MANIFEST_FILE).read_text(encoding="utf-8"))
    assert on_disk == d
    assert m.id == logset_id(s.xxh3 for s in built.sources)
    assert m.name == "prod-a"
    assert m.format == "jira"
    assert m.signature_spec == SignatureSpec().key
    assert m.entry_count == TOTAL_A
    assert m.group_count == len(built.grouper.groups)
    assert [s.path for s in m.sources] == [str(s.path) for s in built.sources]
    assert sum(s.entries for s in m.sources) == TOTAL_A
    assert m.sources[1].entries == N_PER_SOURCE  # duplicates moved to b.log were dropped
    assert m.first_ts == BASE.astimezone(UTC)
    assert m.last_ts is not None and m.first_ts is not None and m.last_ts > m.first_ts
    assert m.sources[0].first_ts == BASE


def test_manifest_refuses_newer_major(built: Built) -> None:
    d = built.logset.manifest.to_dict()
    with pytest.raises(ValueError, match="newer"):
        LogSetManifest.from_dict({**d, "schema_version": "2.0"})
    with pytest.raises(ValueError, match="newer"):
        LogSetManifest.from_dict({**d, "schema": "logset.v2"})
    with pytest.raises(ValueError):
        LogSetManifest.from_dict({**d, "schema": "groups.v1"})
    assert LogSetManifest.from_dict({**d, "schema_version": "1.7"}).schema_version == "1.7"
    bad = dict(d)
    del bad["entry_count"]
    with pytest.raises(ValueError):
        LogSetManifest.from_dict(bad)


# --- ingest -------------------------------------------------------------------------------


def test_dedup_and_files(built: Built) -> None:
    assert built.emitted == TOTAL_A + DUPLICATES
    pf = pq.ParquetFile(built.logset.path / ENTRIES_FILE)
    assert pf.metadata.num_rows == TOTAL_A
    assert pf.schema_arrow.remove_metadata().equals(ENTRIES_SCHEMA.remove_metadata())
    assert pf.metadata.row_group(0).column(0).compression == "ZSTD"
    ids = built.logset.entries(columns=["id"])["id"]
    assert ids.n_unique() == TOTAL_A
    groups = pq.read_table(built.logset.path / "groups.parquet")
    assert groups.schema.remove_metadata().equals(GROUPS_SCHEMA.remove_metadata())
    # Nothing temporary left behind.
    assert [p.name for p in built.store.root.iterdir()] == [built.logset.manifest.id]


def test_groups_recomputed_when_grouper_counts_duplicates(store: Store, built: Built) -> None:
    naive = build(
        store, ["a.log", "b.log", "c.log"], WEIGHTS_A, root="/naive", grouper_dedups=False
    )
    assert sum(g.count for g in naive.grouper.groups.values()) == TOTAL_A + DUPLICATES
    got = naive.logset.groups().drop("examples")
    want = built.logset.groups().drop("examples")
    assert naive.logset.manifest.group_count == got.height
    for col in got.columns:
        if col == "sources":  # different root directory
            continue
        if col in ("top_users", "top_threads"):  # tie order may differ
            norm = lambda s: [sorted((d["value"], d["count"]) for d in v) for v in s]  # noqa: E731
            assert norm(got[col]) == norm(want[col]), col
        else:
            assert got[col].equals(want[col]), col
    assert got["count"].sum() == TOTAL_A


def test_unique_entries() -> None:
    from atlas_log_analyzer.store.writer import unique_entries

    sources = source_files(["a.log", "b.log"])
    entries = [se.entry for se in generate(sources, WEIGHTS_A, 20, duplicates=5)]
    out = list(unique_entries(entries))
    assert len(entries) == 45 and len(out) == 40
    assert len({e.id for e in out}) == 40


def test_idempotent_reingest_does_not_consume(built: Built) -> None:
    consumed = False

    def stream():  # type: ignore[no-untyped-def]
        nonlocal consumed
        consumed = True
        yield from ()

    called = False

    def groups_after():  # type: ignore[no-untyped-def]
        nonlocal called
        called = True
        return []

    from .synth import FakeFormat

    again = built.store.ingest(
        stream(), groups_after, built.sources, FakeFormat(), spec=SignatureSpec()
    )
    assert not consumed and not called
    assert again.manifest == built.logset.manifest
    assert len(built.store.list()) == 1


def test_overwrite_replaces(built: Built) -> None:
    again = build(
        built.store, ["a.log", "b.log", "c.log"], WEIGHTS_A, name="prod-a", overwrite=True
    )
    assert again.logset.manifest.id == built.logset.manifest.id
    assert again.logset.manifest.created_at >= built.logset.manifest.created_at
    assert again.logset.manifest.entry_count == TOTAL_A
    assert len(built.store.list()) == 1
    assert [p.name for p in built.store.root.iterdir()] == [built.logset.manifest.id]


def test_failed_ingest_leaves_nothing(store: Store) -> None:
    sources = source_files(["x.log"])

    def boom():  # type: ignore[no-untyped-def]
        yield from generate(sources, WEIGHTS_A, 10)
        raise RuntimeError("parser died")

    from .synth import FakeFormat

    with pytest.raises(RuntimeError):
        store.ingest(boom(), list, sources, FakeFormat(), spec=SignatureSpec())
    assert list(store.root.iterdir()) == []
    assert store.list() == []


def test_name_conflict(built: Built) -> None:
    with pytest.raises(StoreError, match="already used"):
        build(built.store, ["z.log"], WEIGHTS_A, name="prod-a")


def test_no_sources(store: Store) -> None:
    from .synth import FakeFormat

    with pytest.raises(StoreError):
        store.ingest(iter(()), list, [], FakeFormat(), spec=SignatureSpec())


# --- open / list / remove ----------------------------------------------------------------


def test_open_by_id_prefix_name(built: Built, built_b: Built) -> None:
    store = built.store
    a_id = built.logset.manifest.id
    assert store.open(a_id).manifest.id == a_id
    assert store.open("prod-a").manifest.id == a_id
    assert store.open("prod-b").manifest.id == built_b.logset.manifest.id
    prefix = a_id[:6] if a_id[:6] != built_b.logset.manifest.id[:6] else a_id[:10]
    assert store.open(prefix).manifest.id == a_id
    with pytest.raises(StoreError):
        store.open(a_id[:3])  # too short
    with pytest.raises(StoreError):
        store.open("nope")
    with pytest.raises(StoreError):
        store.open("..")
    listed = store.list()
    assert [m.id for m in listed] == [built_b.logset.manifest.id, a_id]  # newest first


def test_open_ambiguous_prefix(store: Store, tmp_path: Path) -> None:
    # Two hand-made sets whose ids share a prefix.
    base = build(store, ["a.log"], WEIGHTS_A, name="one").logset
    m = base.manifest
    for new_id in ("abcd0001", "abcd0002"):
        d = store.root / new_id
        d.mkdir()
        replace(m, id=new_id, name=None).write(d)
    with pytest.raises(StoreError, match="ambiguous"):
        store.open("abcd")
    assert store.open("abcd0001").manifest.id == "abcd0001"


def test_list_skips_corrupt(built: Built) -> None:
    bad = built.store.root / "deadbeef"
    bad.mkdir()
    (bad / MANIFEST_FILE).write_text("{not json", encoding="utf-8")
    assert [m.id for m in built.store.list()] == [built.logset.manifest.id]
    with pytest.raises(StoreError):
        built.store.open("deadbeef")


def test_remove(built: Built, built_b: Built) -> None:
    built.store.remove("prod-a")
    assert [m.name for m in built.store.list()] == ["prod-b"]
    with pytest.raises(StoreError):
        built.store.remove("prod-a")


def test_empty_store(tmp_path: Path) -> None:
    s = Store(tmp_path / "missing")
    assert s.list() == []
    with pytest.raises(StoreError):
        s.open("anything")


# --- groups -------------------------------------------------------------------------------


def test_groups_match_reference(built: Built) -> None:
    df = built.logset.groups()
    ref = built.grouper.groups
    assert df.height == len(ref)
    assert df["count"].sum() == TOTAL_A
    assert df["count"].to_list() == sorted(df["count"].to_list(), reverse=True)
    row = rows(df.filter(pl.col("signature") == next(iter(ref))))[0]
    g = ref[row["signature"]]
    assert row["count"] == g.count
    assert row["template"] == g.template
    assert row["level_name"] == LogLevel(g.level).name
    assert row["first_seen"] == g.first_seen
    assert row["user_count"] == len(g.users)
    assert row["examples"] == g.examples
    assert sum(s["count"] for s in row["sources"]) == g.count
    assert len(row["top_threads"]) <= 10


def test_groups_filters(built: Built) -> None:
    ls = built.logset
    errors = ls.groups(level="ERROR")
    assert set(errors["level_name"].cast(pl.String)) == {"ERROR"}
    assert errors.height == 3  # script, npe, npe2
    assert ls.groups(level="error,warn").height == 4
    assert set(ls.groups(min_level="WARN")["level_name"].cast(pl.String)) == {"WARN", "ERROR"}
    assert ls.groups(min_level="WARNING").height == 4
    assert ls.groups(logger="com.atlassian.jira").height == 5  # package prefix
    assert ls.groups(logger="com.atlassian.jira.Slow").height == 1
    assert ls.groups(logger="com.atlassian.ji").height == 0
    assert ls.groups(limit=2).height == 2
    assert ls.groups(since=datetime(2030, 1, 1)).height == 0
    assert ls.groups(until=datetime(2020, 1, 1, tzinfo=UTC)).height == 0
    assert ls.groups(since=BASE).height == ls.groups().height
    with pytest.raises(ValueError):
        ls.groups(level="LOUD")
    with pytest.raises(ValueError):
        ls.groups(sort="nope")


def test_groups_sort(built: Built) -> None:
    ls = built.logset
    last = ms(ls.groups(sort="last_seen")["last_seen"])
    assert last == sorted(last, reverse=True)
    first = ms(ls.groups(sort="first_seen")["first_seen"])
    assert first == sorted(first)
    tmpl = ls.groups(sort="template")["template"].to_list()
    assert tmpl == sorted(tmpl)


def test_groups_coarse(built: Built) -> None:
    ls = built.logset
    fine = ls.groups()
    coarse = ls.groups(coarse=True)
    assert coarse.height == fine.height - 1  # npe + npe2 share a coarse signature
    assert coarse["count"].sum() == fine["count"].sum()
    assert coarse.columns[: len(GROUPS_SCHEMA.names)] == GROUPS_SCHEMA.names
    npe = fine.filter(pl.col("template") == "Request failed for <PATH>")
    merged = rows(coarse.filter(pl.col("template") == "Request failed for <PATH>"))[0]
    assert merged["count"] == npe["count"].sum()
    assert merged["signature"] == merged["coarse_signature"]
    assert sorted(merged["signatures"]) == sorted(npe["signature"].to_list())
    assert ms_of(merged["first_seen"]) == min(ms(npe["first_seen"]))
    assert ms_of(merged["last_seen"]) == max(ms(npe["last_seen"]))
    big = rows(npe.sort("count", descending=True))[0]
    assert merged["exception_type"] == big["exception_type"]
    assert sum(s["count"] for s in merged["sources"]) == merged["count"]
    assert merged["source_count"] == len(merged["sources"])
    assert len(merged["examples"]) <= 3
    assert ls.groups(coarse=True, level="ERROR").height == 2


# --- entries ------------------------------------------------------------------------------


def test_entries_filters(built: Built) -> None:
    ls = built.logset
    all_ = ls.entries()
    assert all_.height == TOTAL_A
    assert all_.columns == ENTRIES_SCHEMA.names
    ts = ms(all_["ts"])
    assert ts == sorted(ts)
    sig = ls.groups(level="FATAL,ERROR")["signature"][0]
    by_sig = ls.entries(signature=sig)
    assert by_sig.height == built.grouper.groups[sig].count
    assert ls.entries(level="ERROR").height == sum(
        g.count for g in built.grouper.groups.values() if g.level == LogLevel.ERROR
    )
    assert ls.entries(min_level="ERROR").height == ls.entries(level="ERROR,FATAL").height
    assert ls.entries(user="user1").height > 0
    assert set(ls.entries(user="user1")["user"]) == {"user1"}
    assert set(ls.entries(thread="http-nio-8080-exec-3")["thread"]) == {"http-nio-8080-exec-3"}
    hits = ls.entries(contains="abc-1")  # case-insensitive
    assert hits.height > 0 and all("ABC-1" in r for r in hits["raw"])
    assert ls.entries(contains="no such text (").height == 0
    assert ls.entries(limit=5).height == 5
    assert ms(ls.entries(limit=5)["ts"]) == ts[:5]
    since = BASE + timedelta(minutes=1)
    assert min(ms(ls.entries(since=since)["ts"])) >= ms_of(since)
    assert max(ms(ls.entries(until=since)["ts"])) < ms_of(since)
    naive_utc = since.astimezone(UTC).replace(tzinfo=None)
    assert ls.entries(since=naive_utc).height == ls.entries(since=since).height
    cols = ls.entries(columns=["id", "level_name"], limit=3)
    assert cols.columns == ["id", "level_name"]
    assert ls.entries(logger="com.atlassian").height > 0
    coarse = ls.groups(coarse=True, level="ERROR")["coarse_signature"][0]
    assert ls.entries(coarse_signature=coarse).height > 0


def test_entry(built: Built) -> None:
    ls = built.logset
    err = ls.entries(level="ERROR", limit=50)
    row_id = next(
        r["id"]
        for r in rows(err)
        if r["exception_type"] == "java.lang.IllegalStateException" and r["context"]
    )
    e = ls.entry(row_id)
    assert e is not None
    assert set(e) == set(ENTRIES_SCHEMA.names)
    assert e["exception_type"] == "java.lang.IllegalStateException"
    chain = e["exception_chain"]
    assert [c["type"] for c in chain] == [
        "java.lang.IllegalStateException",
        "java.sql.SQLException",
    ]
    assert chain[0]["frames"][0] == {
        "location": "com.example.Foo.bar",
        "file": "Foo.java",
        "line": 42,
        "module": None,
    }
    assert chain[0]["omitted_frames"] == 7
    assert e["variables"][0].startswith("ABC-")
    assert e["tz_offset"] == 120
    assert e["level"] == int(LogLevel.ERROR)
    assert ls.entry("0000000000000000") is None


def test_context_columns(built: Built) -> None:
    df = built.logset.entries(columns=["user", "url", "context"])
    with_custom = df.filter(pl.col("url").is_not_null())
    assert with_custom.height > 0
    ctx = with_custom.row(0, named=True)["context"]
    as_dict = ctx if isinstance(ctx, dict) else {kv["key"]: kv["value"] for kv in ctx}
    assert set(as_dict) == {"custom"}  # well-known keys have their own columns


def test_iter_entries_round_trip(built: Built) -> None:
    sources = built.sources
    originals = {
        se.entry.id: se.entry for se in generate(sources, WEIGHTS_A, N_PER_SOURCE, parse_errors=3)
    }
    rebuilt = list(iter_entries(built.logset.entries_path, "jira"))
    assert len(rebuilt) == TOTAL_A
    for e in rebuilt:
        o = originals[e.id]
        assert e == o
        assert e.timestamp.utcoffset() == o.timestamp.utcoffset()
        assert dict(e.context) == dict(o.context)


# --- stats / sql --------------------------------------------------------------------------


def test_stats(built: Built) -> None:
    st = built.logset.stats()
    json.dumps(st)
    assert st["entry_count"] == TOTAL_A
    assert st["parse_errors"] == PARSE_ERRORS
    assert sum(st["levels"].values()) == TOTAL_A
    assert st["levels"]["UNKNOWN"] == PARSE_ERRORS
    assert len(st["loggers"]) <= 20
    assert st["loggers"][0]["count"] >= st["loggers"][-1]["count"]
    assert sum(h["count"] for h in st["hours"]) == TOTAL_A
    assert st["hours"][0]["hour"].startswith("2026-10-01T06:00:00")
    assert st["time_range"]["first"] == BASE.astimezone(UTC).isoformat()
    assert len(st["sources"]) == 3
    assert st["group_count"] == len(built.grouper.groups)


def test_sql_round_trip(built: Built) -> None:
    ls = built.logset
    n = ls.sql("select count(*) as n from entries")["n"][0]
    assert n == TOTAL_A
    df = ls.sql(
        "select id, level_name, logger, source_path, context, exception_chain, variables "
        "from entries where exception_type is not null and cardinality(context) > 0 limit 5"
    )
    assert df.height == 5
    row = df.row(0, named=True)
    assert row["level_name"] == "ERROR"
    assert isinstance(row["logger"], str) and isinstance(row["source_path"], str)
    ctx = row["context"]
    as_dict = ctx if isinstance(ctx, dict) else {kv["key"]: kv["value"] for kv in ctx}
    assert "custom" in as_dict
    assert row["exception_chain"][0]["frames"][0]["location"] in {"com.example.Foo.bar"} or (
        row["exception_chain"][0]["type"] == "java.lang.NullPointerException"
    )
    assert isinstance(row["variables"], list)
    g = ls.sql("select signature, top_users, sources from groups order by count desc limit 1")
    assert g.row(0, named=True)["sources"][0]["count"] > 0
    joined = ls.sql(
        "select g.template, count(*) n from entries e join groups g using (signature) "
        "group by 1 order by 2 desc"
    )
    assert joined["n"].sum() == TOTAL_A
    with pytest.raises(Exception, match="merges"):
        ls.sql("select * from merges")


def test_sql_merges_view(built: Built) -> None:
    from dataclasses import dataclass

    @dataclass
    class P:
        cluster_id: int
        drain_template: str
        signatures: list[str]

    sigs = built.logset.groups()["signature"].to_list()[:2]
    assert write_merges(built.logset.merges_path, [P(1, "x <*>", sigs)]) == 1
    df = built.logset.sql("select cluster_id, signatures from merges")
    assert df.row(0, named=True) == {"cluster_id": 1, "signatures": sigs}
    assert built.logset.merges_lazy() is not None


# --- diff ---------------------------------------------------------------------------------


def test_diff(built: Built, built_b: Built) -> None:
    a, b = built.logset, built_b.logset
    d = diff(a, b)
    assert d.a == a.manifest.id and d.b == b.manifest.id
    templates = lambda df: set(df["template"])  # noqa: E731
    assert templates(d.new) == {"Startup failed: <*>"}
    assert templates(d.vanished) == {"Cache <*> evicted"}
    assert (d.new["count_a"] == 0).all() and (d.vanished["count_b"] == 0).all()
    for col in ("signature", "template", "level", "logger", "exception_type", "count_a"):
        assert col in d.changed.columns
    assert "ratio" in d.changed.columns
    mags = [abs(__import__("math").log(r)) for r in d.changed["ratio"]]
    assert mags == sorted(mags, reverse=True)
    script = rows(d.changed.filter(pl.col("template").str.starts_with("Script")))[0]
    assert script["ratio"] == pytest.approx(script["count_b"] / script["count_a"])
    total_sigs = len(set(a.groups()["signature"]) | set(b.groups()["signature"]))
    assert d.new.height + d.vanished.height + d.changed.height + d.unchanged_count == total_sigs

    err = diff(a, b, min_level="ERROR")
    assert err.vanished.height == 0  # DEBUG group filtered out
    assert set(err.new["level_name"]) == {"FATAL"}

    c = diff(a, b, coarse=True)
    assert c.changed.height + c.unchanged_count + c.new.height + c.vanished.height == len(
        set(a.groups(coarse=True)["signature"]) | set(b.groups(coarse=True)["signature"])
    )

    same = diff(a, a)
    assert same.new.height == same.vanished.height == same.changed.height == 0
    assert same.unchanged_count == a.manifest.group_count


def test_diff_refuses_incompatible(built: Built, built_b: Built) -> None:
    b = built_b.logset
    other = LogSet(b.path, replace(b.manifest, signature_spec="L+G+E+F3"))
    with pytest.raises(ValueError, match="signature_spec"):
        diff(built.logset, other)
    other2 = LogSet(b.path, replace(b.manifest, mask_rules_version=99))
    with pytest.raises(ValueError, match="mask_rules_version"):
        diff(built.logset, other2)
    assert diff(built.logset, other, force=True).new.height == 1


# --- schema -------------------------------------------------------------------------------


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_schema_json(name: str) -> None:
    doc = schema_json(name)
    json.dumps(doc)
    assert doc["schema"] == f"{name}.v1"
    assert doc["version"] == "1.0"
    fields = doc["fields"]
    assert isinstance(fields, list) and fields
    for f in fields:
        assert set(f) == {"name", "type", "nullable", "description"}
        assert f["description"], f["name"]
    if name == "entries":
        assert [f["name"] for f in fields] == ENTRIES_SCHEMA.names
    if name == "logset":
        assert {f["name"] for f in fields} == set(
            LogSetManifest.from_dict(_any_manifest_dict()).to_dict()
        )


def _any_manifest_dict() -> dict[str, object]:
    return {
        "schema": "logset.v1",
        "id": "x",
        "name": None,
        "format": "jira",
        "sources": [],
        "first_ts": None,
        "last_ts": None,
        "entry_count": 0,
        "group_count": 0,
        "created_at": "2026-10-29T00:00:00+00:00",
        "tool_version": "0",
        "schema_version": "1.0",
        "signature_version": 1,
        "mask_rules_version": 1,
        "signature_spec": "k",
    }


def test_schema_json_unknown() -> None:
    with pytest.raises(ValueError):
        schema_json("nope")


# --- regroup ------------------------------------------------------------------------------


def test_regroup(built: Built) -> None:
    try:
        ls = built.store.regroup("prod-a", SignatureSpec(frames=1))
    except NotImplementedError as exc:
        pytest.xfail(f"core not implemented yet: {exc}")
    assert ls.manifest.signature_spec == SignatureSpec(frames=1).key
    assert ls.manifest.entry_count == TOTAL_A
    groups = ls.groups()
    assert groups["count"].sum() == TOTAL_A
    assert ls.manifest.group_count == groups.height
    # Every entry signature has a group.
    assert set(ls.entries(columns=["signature"])["signature"]) == set(groups["signature"])
    assert KINDS  # synthetic kinds are still the source of truth for templates


def test_rows_and_records(built: Built) -> None:
    df = built.logset.groups(limit=2)
    r = rows(df)[0]
    assert r["first_seen"].tzinfo is not None
    rec = to_records(df)
    json.dumps(rec)
    assert rec[0]["first_seen"] == r["first_seen"].isoformat(timespec="milliseconds")
    e = to_records(built.logset.entries(limit=1))[0]
    assert isinstance(e["context"], dict)
    json.dumps(e)


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_format_docs_up_to_date(name: str) -> None:
    import importlib.util

    docs = Path(__file__).parents[2] / "docs" / "formats"
    spec = importlib.util.spec_from_file_location("_gen_format_docs", docs / "generate.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = (docs / f"{name}.v1.md").read_text(encoding="utf-8")
    assert text == mod.render(name), "run: uv run python docs/formats/generate.py"
