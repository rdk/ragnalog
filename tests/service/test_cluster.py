"""Cluster analytics (``service/cluster.py``) over two invented nodes, NODE1 and NODE2.

``tests/fixtures/cluster/NODE{1,2}/atlassian-jira.log`` (times UTC, 2026-11-02):

- NODE2 logs from 08:00 to 11:59:30, NODE1 from 09:00 to 12:00 (overlap 09:00-11:59:30);
  NODE1 logs nothing from 10:41 to 10:59 (a gap).
- Groups: a heartbeat and a pool warning on both nodes, an LDAP sync ten times on NODE1
  and once on NODE2 (skewed), a deadlock and an index lock only on NODE1, a replication
  backlog only on NODE2 (2 minutes after each deadlock burst), a warm-up only on NODE2
  before NODE1 starts.
- Events: NODE1 starts at 09:00, re-creates the cache peer of NODE2 at 09:30 and
  releases the index snapshot lock at 11:00:00.100; NODE2 replays the index from NODE1
  at 11:00:02.300, accuses NODE1 of not replicating its cache hourly, fails a health
  check; a poller job and a cleanup job run on both nodes in the same minute.
- Session ``s3ss10n1`` is seen on NODE1 at 10:10 and on NODE2 at 10:45.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import msgspec
import pytest

from atlas_log_analyzer.core.events import EVENT_RULES_VERSION
from atlas_log_analyzer.service import cluster as C
from atlas_log_analyzer.service.documents import DOCUMENTS, ClusterReport, document_schema
from atlas_log_analyzer.service.errors import OpError
from atlas_log_analyzer.store.filters import EntryFilter, GroupFilter
from atlas_log_analyzer.store.manifest import LogSetManifest
from atlas_log_analyzer.store.pipeline import ingest_paths
from atlas_log_analyzer.store.store import Store
from tests.contract.test_schemas import validate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cluster"
DAY = datetime(2026, 11, 2, tzinfo=UTC)


def at(hh: int, mm: int, ss: float = 0) -> datetime:
    return DAY + timedelta(hours=hh, minutes=mm, seconds=ss)


def iso(ts: datetime) -> str:
    return C._iso(ts)


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Store]:
    root = tmp_path_factory.mktemp("cluster")
    st = Store(root / "store")
    ingest_paths(st, [FIXTURES / "NODE1"], name="acme-node1")
    ingest_paths(st, [FIXTURES / "NODE2"], name="acme-node2")
    yield st


@pytest.fixture(scope="module")
def capture(store: Store) -> C.Capture:
    pairs = [("NODE2", store.open("acme-node2")), ("NODE1", store.open("acme-node1"))]
    return C.open_capture(pairs, capture_id="c0ffee", cluster="acme")


@pytest.fixture(scope="module")
def window(capture: C.Capture) -> C.Window:
    return C.resolve_window(capture)


def doc(struct: msgspec.Struct) -> dict[str, Any]:
    """The JSON of a document, checked against its Struct and its JSON Schema."""
    data: dict[str, Any] = json.loads(msgspec.json.encode(struct))
    name = data["schema"]
    typed = msgspec.convert(data, DOCUMENTS[name])
    assert json.loads(msgspec.json.encode(typed)) == data, "a key is not in its Struct"
    root = document_schema(name)
    validate(data, root, root)
    return data


def error(call: Any) -> OpError:
    with pytest.raises(OpError) as info:
        call()
    return info.value


# --- capture and window


def test_open_capture_sorts_nodes(capture: C.Capture) -> None:
    assert capture.node_ids == ["NODE1", "NODE2"]
    assert [m.logset.manifest.name for m in capture.nodes] == ["acme-node1", "acme-node2"]
    assert (capture.id, capture.cluster, capture.format) == ("c0ffee", "acme", "jira")
    assert capture.ref == "capture:c0ffee"


def test_open_capture_refuses_bad_members(store: Store, tmp_path: Path) -> None:
    n1 = store.open("acme-node1")
    assert error(lambda: C.open_capture([])).code == "invalid_value"
    twice = error(lambda: C.open_capture([("NODE1", n1), ("NODE1", store.open("acme-node2"))]))
    assert twice.code == "invalid_value" and "given twice" in twice.message
    assert "given twice" in error(lambda: C.open_capture([("A", n1), ("B", n1)])).message
    other_dir = tmp_path / "eeee000000000001"
    shutil.copytree(n1.path, other_dir)
    m = LogSetManifest.read(other_dir)
    key = m.recipe_key.replace("|L+G+E+F0|", "|L+G+E+F1|")
    dataclasses.replace(m, id="eeee000000000001", recipe_key=key).write(other_dir)
    other = type(n1).load(other_dir)
    for front, code in (("api", "incompatible_sets"), ("cli", "invalid_value")):
        err = error(lambda f=front: C.open_capture([("A", n1), ("B", other)], front=f))
        assert (err.code, err.reason) == (code, "incompatible_sets")
        assert err.hint is not None and "upgrade" in err.hint


def test_windows(capture: C.Capture) -> None:
    overlap = C.resolve_window(capture)
    assert (overlap.mode, overlap.since, overlap.until) == ("overlap", at(9, 0), at(11, 59, 30))
    union = C.resolve_window(capture, "union")
    assert (union.mode, union.since, union.until) == ("union", at(8, 0), at(12, 0))
    # one end given: the other is the union's (the capture's last or first entry)
    explicit = C.resolve_window(capture, since="2026-11-02T10:00")
    assert (explicit.mode, explicit.since, explicit.until) == ("explicit", at(10, 0), at(12, 0))
    early = C.resolve_window(capture, until=at(8, 30))  # before the overlap starts (9:00)
    assert (early.mode, early.since, early.until) == ("explicit", at(8, 0), at(8, 30))
    relative = C.resolve_window(capture, "union", since="30m", until=at(12, 0))
    assert (relative.since, relative.until) == (at(11, 30), at(12, 0))
    assert error(lambda: C.resolve_window(capture, "sideways")).code == "invalid_value"
    assert error(lambda: C.resolve_window(capture, since="yesterday")).code == "invalid_value"
    late = error(lambda: C.resolve_window(capture, since=at(12, 0), until=at(9, 0)))
    assert late.code == "invalid_value" and "after" in late.message
    # one bound outside the logs: the message names the window, not a bound never given
    before = error(lambda: C.resolve_window(capture, until=at(7, 0)))
    assert before.code == "invalid_value" and "holds no entries" in before.message
    assert "since" not in before.message


def test_empty_overlap_suggests_union(store: Store, tmp_path: Path) -> None:
    early = tmp_path / "early" / "atlassian-jira.log"
    early.parent.mkdir()
    early.write_text(
        "2026-11-01 07:00:00,000+0000 Caesium-1-1 INFO      [org.acme.health.Heartbeat] "
        "Heartbeat sent to monitoring\n",
        encoding="utf-8",
    )
    ingest_paths(store, [early.parent], name="acme-early")
    cap = C.open_capture([("EARLY", store.open("acme-early")), ("NODE1", store.open("acme-node1"))])
    err = error(lambda: C.resolve_window(cap))
    assert err.code == "invalid_value" and err.hint is not None and "union" in err.hint
    assert C.resolve_window(cap, "union").since == at(7, 0) - timedelta(days=1)
    store.remove("acme-early")


# --- capture.v1


def test_capture_summary(capture: C.Capture, window: C.Window) -> None:
    d = doc(C.capture_summary(capture, window=window))
    assert (d["schema"], d["capture"], d["cluster"]) == ("capture.v1", "c0ffee", "acme")
    assert d["window"] == {"mode": "overlap", "since": iso(at(9, 0)), "until": iso(at(11, 59, 30))}
    n1, n2 = d["nodes"]
    assert (n1["node"], n2["node"]) == ("NODE1", "NODE2")
    assert n1["set"]["name"] == "acme-node1"
    assert (n1["first_ts"], n2["first_ts"]) == (iso(at(9, 0)), iso(at(8, 0)))
    assert n1["coverage"] == {"since": iso(at(9, 0)), "until": iso(at(11, 59, 30))}
    assert n1["gaps"] == [[iso(at(10, 41)), iso(at(11, 0))]]
    assert n2["gaps"] == []
    assert (n1["entries"], n1["errors"], n1["warnings"]) == (67, 14, 4)
    assert (n2["entries"], n2["errors"], n2["warnings"]) == (55, 6, 6)
    assert n1["errors_per_hour"] == pytest.approx(14 / (179.5 / 60), abs=0.001)
    assert n1["top_error"]["template"] == "Deadlock detected on table jiraissue"
    assert n1["top_error"]["count"] == 12
    assert n1["events"] == {"cache_peer": 1, "index_snapshot": 1, "job_run": 2, "start": 1}
    assert n2["events"] == {
        "cache_replication": 3,
        "health_failed": 1,
        "index_replay": 1,
        "job_run": 3,
    }
    assert (n1["verdict"], n1["baseline"]) == (None, None)
    assert d["skew"] == [{"a": "NODE1", "b": "NODE2", "offset_ms": 2200, "pairs": 1}]


def test_capture_summary_union_and_explicit(capture: C.Capture) -> None:
    union = doc(C.capture_summary(capture, window=C.resolve_window(capture, "union")))
    n1, n2 = union["nodes"]
    assert n1["coverage"] == {"since": iso(at(9, 0)), "until": iso(at(12, 0))}
    assert n2["coverage"] == {"since": iso(at(8, 0)), "until": iso(at(11, 59, 30))}
    outside = C.Window("explicit", at(8, 0), at(8, 30))
    early = doc(C.capture_summary(capture, window=outside))
    assert early["nodes"][0]["coverage"] is None and early["nodes"][0]["entries"] == 0
    assert early["nodes"][0]["top_error"] is None


def test_capture_summary_triages_each_node_against_its_own_past(
    capture: C.Capture, store: Store, tmp_path: Path
) -> None:
    older = tmp_path / "older" / "atlassian-jira.log"
    older.parent.mkdir()
    text = (FIXTURES / "NODE1" / "atlassian-jira.log").read_text(encoding="utf-8")
    keep = [line for line in text.splitlines() if "Deadlock" not in line]
    older.write_text("\n".join(keep).replace("2026-11-02", "2026-10-26") + "\n", encoding="utf-8")
    ingest_paths(store, [older.parent], name="acme-node1-older")
    try:
        d = doc(C.capture_summary(capture, window=C.resolve_window(capture), store=store))
        n1 = d["nodes"][0]
        assert n1["baseline"]["name"] == "acme-node1-older"
        assert n1["verdict"] == "new_errors"
        names = {n["baseline"]["name"] for n in d["nodes"] if n["baseline"] is not None}
        assert not names & {"acme-node1", "acme-node2"}
    finally:
        store.remove("acme-node1-older")


# --- cluster-timeline.v1


def test_cluster_timeline(capture: C.Capture, window: C.Window) -> None:
    d = doc(C.cluster_timeline(capture, window=window))
    assert d["bucket"] == "1h"
    assert d["buckets"] == ["2026-11-02T09:00:00Z", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z"]
    n1 = d["nodes"]["NODE1"]
    assert set(n1["levels"]) == {"ERROR", "WARN", "INFO", "DEBUG", "OTHER"}
    assert sum(sum(v) for v in n1["levels"].values()) == 67
    assert n1["levels"]["ERROR"] == [6, 5, 3]
    assert n1["coverage"] == [0, 2] and d["nodes"]["NODE2"]["coverage"] == [0, 2]
    assert "signature" not in d


def test_cluster_timeline_takes_entry_filters(capture: C.Capture, window: C.Window) -> None:
    whole = doc(C.cluster_timeline(capture, window=window))
    same = doc(C.cluster_timeline(capture, window=window, where=EntryFilter()))
    assert same == whole
    errors = doc(C.cluster_timeline(capture, window=window, where=EntryFilter(level="ERROR")))
    n1 = errors["nodes"]["NODE1"]["levels"]
    assert n1["ERROR"] == [6, 5, 3] and not any(n1["WARN"] + n1["INFO"] + n1["DEBUG"])
    assert errors["nodes"]["NODE1"]["coverage"] == whole["nodes"]["NODE1"]["coverage"]
    nothing = doc(C.cluster_timeline(capture, window=window, where=EntryFilter(thread="no-such")))
    assert not any(sum(s) for n in nothing["nodes"].values() for s in n["levels"].values())
    deadlock = next(
        r for r in C.matrix(capture, window=window).items if r.template.startswith("Dead")
    )
    both = doc(
        C.cluster_timeline(
            capture,
            window=window,
            signature=deadlock.signature[:6],
            where=EntryFilter(min_level="WARN"),
        )
    )
    assert both["signature"] == deadlock.signature
    assert both["nodes"]["NODE1"]["levels"] == {"count": [6, 3, 3]}
    none = doc(
        C.cluster_timeline(
            capture, window=window, signature=deadlock.signature, where=EntryFilter(contains="~~")
        )
    )
    assert none["nodes"]["NODE1"]["levels"] == {"count": [0, 0, 0]}


def test_cluster_timeline_of_one_group_and_buckets(capture: C.Capture, window: C.Window) -> None:
    deadlock = next(
        r for r in C.matrix(capture, window=window).items if r.template.startswith("Dead")
    )
    d = doc(C.cluster_timeline(capture, window=window, signature=deadlock.signature[:6]))
    assert d["signature"] == deadlock.signature
    assert d["nodes"]["NODE1"]["levels"] == {"count": [6, 3, 3]}
    assert d["nodes"]["NODE2"]["levels"] == {"count": [0, 0, 0]}
    fine = doc(C.cluster_timeline(capture, window=window, bucket="15m"))
    assert len(fine["buckets"]) == 12 and fine["buckets"][-1] == "2026-11-02T11:45:00Z"
    union = doc(C.cluster_timeline(capture, window=C.resolve_window(capture, "union")))
    assert union["nodes"]["NODE1"]["coverage"] == [1, 4]
    assert union["nodes"]["NODE2"]["coverage"] == [0, 3]
    bad = error(lambda: C.cluster_timeline(capture, window=window, bucket="1w"))
    assert bad.code == "invalid_value" and "bucket" in bad.message
    two_days = C.Window("explicit", at(0, 0), at(0, 0) + timedelta(days=2))
    many = error(lambda: C.cluster_timeline(capture, window=two_days, bucket="1s"))
    assert many.code == "invalid_value" and "buckets" in many.message
    missing = error(lambda: C.cluster_timeline(capture, window=window, signature="ffff00"))
    assert missing.reason == "group_not_found"


# --- matrix.v1


def rows_by_template(d: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["template"]: r for r in d["items"]}


def test_matrix_spread_skew_and_lead(capture: C.Capture, window: C.Window) -> None:
    d = doc(C.matrix(capture, window=window, limit=None))
    assert d["nodes"] == ["NODE1", "NODE2"] and d["total"] == d["count"] and not d["truncated"]
    rows = rows_by_template(d)
    beat = rows["Heartbeat sent to monitoring"]
    assert beat["spread"] == "all" and 1 <= beat["skew"] < 4
    assert (beat["first_node"], beat["lead_ms"]) == ("NODE2", 300_000)
    assert beat["per_node"]["NODE1"]["count"] == 32 and beat["total"] == 69
    sync = rows["Synced <NUM> users from directory"]
    assert sync["spread"] == "skewed" and sync["skew"] >= 4
    assert (sync["first_node"], sync["per_node"]["NODE2"]["count"]) == ("NODE1", 1)
    lock = rows["Lock obtain timed out: write.lock"]
    assert (lock["spread"], lock["skew"], lock["lead_ms"], lock["first_node"]) == (
        "only",
        None,
        None,
        "NODE1",
    )
    assert lock["per_node"]["NODE2"] == {
        "count": 0,
        "rate_per_hour": 0.0,
        "first_seen": None,
        "last_seen": None,
    }
    assert rows["Replication backlog above <NUM> messages"]["first_node"] == "NODE2"
    assert "Warming caches for project XYZ" not in rows
    union = rows_by_template(doc(C.matrix(capture, window=C.resolve_window(capture, "union"))))
    assert union["Warming caches for project XYZ"]["spread"] == "only"
    assert d["items"][0]["total"] >= d["items"][-1]["total"]


def test_matrix_filters_sort_and_page(capture: C.Capture, window: C.Window) -> None:
    only = doc(C.matrix(capture, window=window, spread="only"))
    assert only["items"] and {r["spread"] for r in only["items"]} == {"only"}
    on2 = doc(C.matrix(capture, window=window, node="NODE2", limit=None))
    assert all(r["per_node"]["NODE2"]["count"] > 0 for r in on2["items"])
    errors = doc(C.matrix(capture, window=window, where=GroupFilter(min_level="ERROR")))
    assert {r["level_name"] for r in errors["items"]} == {"ERROR"}
    beats = doc(C.matrix(capture, window=window, where=GroupFilter(contains="heartbeat")))
    assert [r["template"] for r in beats["items"]] == ["Heartbeat sent to monitoring"]
    by_skew = doc(C.matrix(capture, window=window, sort="skew"))
    skews = [r["skew"] for r in by_skew["items"] if r["skew"] is not None]
    assert skews == sorted(skews, reverse=True)
    by_lead = doc(C.matrix(capture, window=window, sort="lead"))
    assert by_lead["items"][0]["template"] == "Synced <NUM> users from directory"
    page = doc(C.matrix(capture, window=window, limit=2, offset=1))
    assert (page["count"], page["offset"], page["truncated"]) == (2, 1, True)
    lenient = rows_by_template(doc(C.matrix(capture, window=window, skew_min=20, limit=None)))
    assert lenient["Synced <NUM> users from directory"]["spread"] == "all"
    for kwargs in ({"spread": "most"}, {"node": "NODE9"}, {"sort": "name"}, {"skew_min": 0.5}):
        assert error(lambda k=kwargs: C.matrix(capture, window=window, **k)).code == (
            "invalid_value"
        )
    bad_level = error(lambda: C.matrix(capture, window=window, where=GroupFilter(level="LOUD")))
    assert bad_level.code == "invalid_level"
    sig = beats["items"][0]["signature"]
    one = doc(C.matrix(capture, window=window, signature=sig[:6]))
    assert [r["signature"] for r in one["items"]] == [sig] and one["total"] == 1
    assert C.resolve_signature(capture, sig[:6]) == sig
    missing = error(lambda: C.matrix(capture, window=window, signature="0" * 16))
    assert missing.reason == "group_not_found"


# --- events.v1


def test_events(capture: C.Capture, window: C.Window) -> None:
    d = doc(C.events(capture, window=window))
    assert d["rules_version"] == EVENT_RULES_VERSION and d["count"] == len(d["items"])
    kinds = {(e["node"], e["kind"]) for e in d["items"]}
    assert ("NODE1", "start") in kinds and ("NODE2", "health_failed") in kinds
    start = next(e for e in d["items"] if e["kind"] == "start")
    assert (start["ts"], start["count"], start["last_ts"]) == (
        iso(at(9, 0)),
        3,
        iso(at(9, 2, 10)),
    )
    accusations = [e for e in d["items"] if e["kind"] == "cache_replication"]
    assert [e["ts"] for e in accusations] == [iso(at(h, 36, 31)) for h in (9, 10, 11)]
    assert {(e["subject"], e["subject_node"]) for e in accusations} == {("NODE1", "NODE1")}
    peer = next(e for e in d["items"] if e["kind"] == "cache_peer")
    assert (peer["node"], peer["subject_node"]) == ("NODE1", "NODE2")
    replay = next(e for e in d["items"] if e["kind"] == "index_replay")
    assert (replay["node"], replay["subject_node"], replay["ts"]) == (
        "NODE2",
        "NODE1",
        iso(at(11, 0, 2.3)),
    )
    check = next(e for e in d["items"] if e["kind"] == "health_failed")
    assert (check["subject"], check["subject_node"]) == ("Cluster Cache Replication", None)
    jobs = {(e["node"], e["subject"]) for e in d["items"] if e["kind"] == "job_run"}
    assert ("NODE1", "CleanupJobRunner") in jobs and ("NODE2", "acme-poller-job") in jobs
    assert [e["ts"] for e in d["items"]] == sorted(e["ts"] for e in d["items"])
    pair = next(p for p in d["pairs"] if p["a"]["kind"] == "index_snapshot")
    assert pair["a"] == {"node": "NODE1", "kind": "index_snapshot"}
    assert pair["b"] == {"node": "NODE2", "kind": "index_replay"}
    assert (pair["count"], pair["of"], pair["median_lag_ms"]) == (1, 1, 2200)
    assert pair["lift"] >= 3
    assert pair["examples"] == [[iso(at(11, 0, 0.1)), iso(at(11, 0, 2.3))]]
    assert all("job_run" not in (p["a"]["kind"], p["b"]["kind"]) for p in d["pairs"])


def test_event_text_of_a_blank_message_is_the_group_title(tmp_path: Path) -> None:
    """Jira's startup logger writes its text below an empty header message."""
    head = "2026-11-02 09:00:0{}+0000 localhost-startStop-1 INFO      [c.a.jira.startup.{}] {}"
    log = tmp_path / "NODE1" / "atlassian-jira.log"
    log.parent.mkdir()
    lines = [
        head.format("0,000", "JiraStartupLogger", ""),
        "    ",
        "    ****************",
        "    Jira starting...",
        "    ****************",
        head.format("5,000", "LauncherContextListener", "Startup is complete."),
    ]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    st = Store(tmp_path / "store")
    cap = C.open_capture([("NODE1", ingest_paths(st, [log]).logset)], capture_id="c1")
    d = doc(C.events(cap, window=C.resolve_window(cap)))
    [start] = d["items"]
    assert (start["kind"], start["count"], start["text"]) == ("start", 2, "Jira starting...")


def test_events_kinds(capture: C.Capture, window: C.Window) -> None:
    d = doc(C.events(capture, window=window, kinds=["cache_peer", "cache_replication"]))
    assert {e["kind"] for e in d["items"]} == {"cache_peer", "cache_replication"}
    err = error(lambda: C.events(capture, window=window, kinds=["meteor"]))
    assert err.code == "invalid_value" and err.hint is not None and "job_run" in err.hint


def test_events_fold_scheduled_repeats(capture: C.Capture) -> None:
    found = C._Events(
        [
            C._Event("N1", t, "health_failed", "Disk", None, "s", f"e{t}", "x", last=t)
            for t in (0, 3_600_000, 7_200_000, 20_000_000)
        ]
        + [C._Event("N1", 50, "cache_replication", "N2", "N2", "s", "c", "x", last=50)]
    )
    items = found.streaks()
    checks = [e for e in items if e.kind == "health_failed"]
    assert [(e.ts, e.last, e.count) for e in checks] == [
        (0, 7_200_000, 3),
        (20_000_000, 20_000_000, 1),
    ]
    assert len(items) == 3


# --- interactions.v1


def test_interactions(capture: C.Capture, window: C.Window) -> None:
    d = doc(C.interactions(capture, window=window))
    assert d["delta_s"] == 300
    edges = {(m["from"], m["to"]): m for m in d["mentions"]}
    assert edges[("NODE2", "NODE1")]["count"] == 5
    assert len(edges[("NODE2", "NODE1")]["groups"]) == 3
    assert edges[("NODE1", "NODE2")]["count"] == 1
    co = next(c for c in d["co_bursts"] if c["a"]["template"].startswith("Deadlock"))
    assert co["a"]["node"] == "NODE1" and co["b"]["node"] == "NODE2"
    assert co["b"]["template"] == "Replication backlog above <NUM> messages"
    assert (co["hits"], co["of"], co["median_lag_ms"]) == (4, 4, 120_000)
    assert co["lift"] >= 3 and co["a"]["groups"] == 1
    jobs = {j["job"]: j for j in d["concurrent_jobs"]}
    assert jobs["CleanupJobRunner"]["nodes"] == ["NODE1", "NODE2"]
    assert jobs["CleanupJobRunner"]["examples"] == [iso(at(10, 30, 5))]
    assert jobs["acme-poller-job"]["slots"] == 1
    assert d["sessions"] == [{"nodes": ["NODE1", "NODE2"], "count": 1}]
    assert "s3ss10n1" not in json.dumps(d)
    owners = {j["job"]: j["runs"] for j in d["jobs"]}
    assert owners["CleanupJobRunner"] == {"NODE1": 1, "NODE2": 2}
    narrow = doc(C.interactions(capture, window=window, delta_s=60))
    assert not any(c["a"]["template"].startswith("Deadlock") for c in narrow["co_bursts"])
    negative = error(lambda: C.interactions(capture, window=window, delta_s=-1, front="cli"))
    # the option is --delta (API delta), not delta_s
    assert (negative.code, negative.message) == ("invalid_value", "--delta must be >= 0 (seconds)")
    api = error(lambda: C.interactions(capture, window=window, delta_s=-1, front="api"))
    assert api.message.startswith("delta must be")


def test_bursts_start_after_quiet_minutes() -> None:
    series = [(100, 3), (101, 3), (105, 1), (110, 3), (130, 3), (131, 4)]
    assert C._burst_starts(series, 0) == [100, 130]
    assert C._burst_starts([(5, 1)], 0) == []
    assert C._burst_starts([(10, 1)], 0) == [10]


def test_families_fold_groups_that_burst_together() -> None:
    groups = {"a": [10, 50, 90], "b": [11, 51, 91], "c": [10, 70], "d": [30]}
    fams = C._families(groups, 2)
    assert sorted(map(sorted, fams.values())) == [["a", "b"], ["c"], ["d"]]
    assert "a" in fams


# --- merged entries and context


def test_merged_entries(capture: C.Capture, window: C.Window) -> None:
    d = doc(C.merged_entries(capture, window=window, limit=5))
    assert d["logset"] == "capture:c0ffee"
    assert (d["count"], d["total"], d["next_offset"], d["truncated"]) == (5, 122, 5, True)
    first = d["items"][:2]
    assert [(r["ts"], r["node"]) for r in first] == [
        (iso(at(9, 0)), "NODE1"),
        (iso(at(9, 0)), "NODE2"),
    ]
    assert first[0]["set"] == capture.nodes[0].logset.id
    last = doc(C.merged_entries(capture, window=window, limit=50, offset=100))
    assert (last["count"], last["next_offset"], last["truncated"]) == (22, None, False)
    stamps = [r["ts"] for r in last["items"]]
    assert stamps == sorted(stamps)
    errors = doc(C.merged_entries(capture, window=window, where=EntryFilter(level="ERROR")))
    assert {r["level_name"] for r in errors["items"]} == {"ERROR"} and errors["total"] == 20
    brief = doc(C.merged_entries(capture, window=window, limit=1, columns=["id", "message"]))
    assert set(brief["items"][0]) == {"id", "message", "node", "set"}
    cut = doc(
        C.merged_entries(
            capture, window=window, where=EntryFilter(exception="RestException"), max_raw=20
        )
    )
    assert cut["items"][0]["body_truncated"] is True and "use max_raw=0" in cut["items"][0]["raw"]
    assert error(lambda: C.merged_entries(capture, window=window, columns=["nope"])).code == (
        "invalid_value"
    )
    assert error(lambda: C.merged_entries(capture, window=window, offset=-1)).code == (
        "invalid_value"
    )


def test_context_all_nodes(capture: C.Capture, window: C.Window) -> None:
    snapshot = next(e for e in C.events(capture, window=window).items if e.kind == "index_snapshot")
    set_id = capture.nodes[0].logset.id
    d = doc(C.context_all_nodes(capture, set_id, snapshot.entry_id, window_s=5))
    assert (d["logset"], d["anchor"], d["any_thread"]) == (set_id, snapshot.entry_id, True)
    assert [(r["node"], r["ts"]) for r in d["items"]] == [
        ("NODE1", iso(at(11, 0))),
        ("NODE2", iso(at(11, 0))),
        ("NODE1", iso(at(11, 0, 0.1))),
        ("NODE2", iso(at(11, 0, 2.3))),
    ]
    assert (d["total"], d["truncated"]) == (4, False)
    by_name = C.context_all_nodes(capture, "acme-node1", snapshot.entry_id, window_s=0)
    assert by_name.count == 1
    cols = doc(C.context_all_nodes(capture, set_id, snapshot.entry_id, columns=["ts"]))
    assert set(cols["items"][0]) == {"ts", "node", "set"}
    assert error(lambda: C.context_all_nodes(capture, "nope", snapshot.entry_id)).reason == (
        "set_not_found"
    )
    assert error(lambda: C.context_all_nodes(capture, set_id, "0" * 16)).reason == (
        "entry_not_found"
    )


# --- cluster-report.v1


def test_cluster_payload(capture: C.Capture, window: C.Window) -> None:
    payload = C.cluster_payload(capture, window=window)
    d = doc(payload)
    assert list(d) == [
        "schema",
        "generated_at",
        "tool_version",
        "capture",
        "nodes",
        "timeline",
        "groups",
        "entries",
        "events",
        "interactions",
        "options",
        "urls",
    ]
    assert msgspec.json.decode(msgspec.json.encode(payload), type=ClusterReport) == payload
    assert "schema" not in d["capture"] and d["capture"]["capture"] == "c0ffee"
    for part in ("timeline", "events", "interactions"):
        assert "schema" not in d[part]
    assert d["nodes"] == ["NODE1", "NODE2"]
    assert d["timeline"]["bucket"] == "15m" and len(d["timeline"]["buckets"]) == 12
    assert d["options"] == {
        "window": "overlap",
        "examples": 3,
        "max_raw": 4000,
        "max_groups": 500,
        "truncated_groups": 0,
    }
    assert d["urls"] is None
    totals = [g["total"] for g in d["groups"]]
    assert totals == sorted(totals, reverse=True)
    n = len(d["timeline"]["buckets"])
    for g in d["groups"]:
        for node, cells in g["hourly"].items():
            assert node in d["nodes"] and all(0 <= i < n and c > 0 for i, c in cells)
            assert sum(c for _, c in cells) == g["per_node"][node]["count"]
        for node, ids in g["examples"].items():
            assert 1 <= len(ids) <= 3 and all(d["entries"][i]["node"] == node for i in ids)
    beat = next(g for g in d["groups"] if g["template"] == "Heartbeat sent to monitoring")
    assert set(beat["examples"]) == {"NODE1", "NODE2"} and len(beat["examples"]["NODE1"]) == 3
    entry = d["entries"][beat["examples"]["NODE1"][0]]
    assert {
        "ts",
        "level_name",
        "thread",
        "logger",
        "message",
        "raw",
        "source",
        "source_line",
    } <= set(entry)
    assert entry["set"] == capture.nodes[0].logset.id and entry["source"] == 0
    assert d["events"]["count"] == len(d["events"]["items"]) > 0
    assert d["interactions"]["mentions"]


def test_cluster_payload_options(capture: C.Capture) -> None:
    union = C.resolve_window(capture, "union")
    d = doc(C.cluster_payload(capture, window=union, examples=1, max_raw=10, max_groups=2))
    assert len(d["groups"]) == 2 and d["options"]["truncated_groups"] > 0
    assert d["options"]["window"] == "union"
    assert all(len(ids) == 1 for g in d["groups"] for ids in g["examples"].values())
    assert all(len(e["raw"]) <= 10 for e in d["entries"].values())
    none = doc(C.cluster_payload(capture, window=union, examples=0))
    assert none["entries"] == {} and all(g["examples"] == {} for g in none["groups"])
    every = doc(C.cluster_payload(capture, window=union, examples=0, max_groups=0))
    assert every["options"]["truncated_groups"] == 0 and len(every["groups"]) == len(
        doc(C.matrix(capture, window=union, limit=0))["items"]
    )
    assert error(lambda: C.cluster_payload(capture, window=union, max_groups=-1)).code == (
        "invalid_value"
    )
