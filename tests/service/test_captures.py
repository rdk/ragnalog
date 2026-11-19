"""``service/captures.py``: listing and resolving captures from manifests alone."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from ragnalog.service.captures import (
    CaptureInfo,
    capture_id,
    captures_doc,
    get_capture,
    is_capture_ref,
    list_captures,
    resolve_capture,
)
from ragnalog.service.errors import OpError
from ragnalog.store.logset import LogSet
from ragnalog.store.manifest import LogSetManifest

T0 = datetime(2026, 11, 5, 6, tzinfo=UTC)


def m(
    set_id: str,
    *,
    node: str | None,
    capture: str | None,
    cluster: str | None = "acme",
    day: int = 0,
    hours: float = 4,
    entries: int = 10,
    name: str | None = None,
) -> LogSetManifest:
    first = T0 + timedelta(days=day)
    return LogSetManifest(
        id=set_id,
        name=name if name is not None else f"set-{set_id}",
        format="jira",
        sources=(),
        first_ts=first,
        last_ts=first + timedelta(hours=hours),
        entry_count=entries,
        group_count=1,
        created_at=T0,
        tool_version="0",
        schema_version="1.1",
        signature_version=1,
        mask_rules_version=1,
        signature_spec="L+G+E+F0",
        node=node,
        cluster=cluster,
        capture=capture,
    )


class FakeSets:
    def __init__(self, manifests: list[LogSetManifest]) -> None:
        self._manifests = manifests

    def manifests(self) -> list[LogSetManifest]:
        return list(self._manifests)

    def open(self, ref: str) -> LogSet:
        raise AssertionError("captures read manifests only")


SETS = FakeSets(
    [
        m("a2", node="NODE2", capture="2026-11-06_job", day=1, hours=5, entries=20),
        m("a1", node="NODE1", capture="2026-11-06_job", day=1),
        m("b1", node="NODE1", capture="2026-11-05_job"),
        m("b2", node="NODE2", capture="2026-11-05_job", cluster=None),
        m("c1", node=None, capture="feedc0de00000001", cluster="other", name="lonely"),
        m("x1", node="NODE1", capture=None),
    ]
)


def test_list_captures_newest_first_with_sorted_nodes() -> None:
    found = list_captures(SETS)
    assert [c.capture for c in found] == ["2026-11-06_job", "2026-11-05_job", "feedc0de00000001"]
    newest = found[0]
    assert isinstance(newest, CaptureInfo)
    assert [n.node for n in newest.nodes] == ["NODE1", "NODE2"]
    assert newest.entry_count == 30 and newest.cluster == "acme"
    assert newest.first_ts == "2026-11-06T06:00:00+00:00"
    assert newest.last_ts == "2026-11-06T11:00:00+00:00"
    assert newest.nodes[1].id == "a2" and newest.nodes[1].entry_count == 20
    assert found[1].cluster == "acme"  # members that have one agree
    assert found[2].nodes[0].node == "lonely"  # a member without a node shows its name


def test_resolve_capture_refs() -> None:
    assert resolve_capture(SETS, "capture:2026-11-05_job").capture == "2026-11-05_job"
    assert resolve_capture(SETS, "capture:feed").capture == "feedc0de00000001"
    assert resolve_capture(SETS, "cluster:acme").capture == "2026-11-06_job"
    assert resolve_capture(SETS, "cluster:acme@2026-11-05").capture == "2026-11-05_job"
    assert resolve_capture(SETS, "cluster:other").capture == "feedc0de00000001"


@pytest.mark.parametrize(
    ("ref", "code", "reason"),
    [
        ("capture:nope", "not_found", "capture_not_found"),
        ("capture:", "not_found", "capture_not_found"),
        ("cluster:nope", "not_found", "capture_not_found"),
        ("cluster:acme@2026-11-07", "not_found", "capture_not_found"),
        ("capture:2026", "ambiguous", "ambiguous_capture"),
        ("cluster:acme@2026-13-01", "invalid_value", None),
        ("set-a1", "invalid_value", None),
    ],
)
def test_resolve_capture_errors(ref: str, code: str, reason: str | None) -> None:
    with pytest.raises(OpError) as info:
        resolve_capture(SETS, ref, front="cli")
    err = info.value
    assert (err.code, err.reason) == (code, reason)
    if reason == "capture_not_found":
        assert (err.exit_code, err.status) == (2, 404) and "ragnalog captures" in str(err.hint)
    if reason == "ambiguous_capture":
        assert (err.exit_code, err.status) == (1, 409)
        assert "2026-11-06_job, 2026-11-05_job" in err.message


def test_same_day_captures_are_ambiguous() -> None:
    sets = FakeSets(
        [m("a", node="NODE1", capture="one"), m("b", node="NODE1", capture="two", hours=5)]
    )
    with pytest.raises(OpError) as info:
        resolve_capture(sets, "cluster:acme@2026-11-05")
    assert info.value.reason == "ambiguous_capture" and "GET /api/captures" in str(info.value.hint)
    assert resolve_capture(sets, "cluster:acme").capture == "two"  # the newest last_ts


def test_is_capture_ref() -> None:
    assert is_capture_ref("capture:abc") and is_capture_ref("cluster:jira-test@2026-11-05")
    assert not is_capture_ref("prod-1029") and not is_capture_ref("captures")


def test_capture_id_is_a_hash_of_the_sorted_set_ids() -> None:
    assert capture_id(["b", "a"]) == capture_id(["a", "b"])
    assert len(capture_id(["a"])) == 16 and capture_id(["a"]) != capture_id(["a", "b"])


def test_captures_doc_and_brief() -> None:
    doc = captures_doc(SETS)
    assert doc["schema"] == "captures.v1" and doc["count"] == 3
    assert set(doc["items"][0]["nodes"][0]) == {
        "node",
        "id",
        "name",
        "first_ts",
        "last_ts",
        "entry_count",
    }
    brief = captures_doc(SETS, brief=True)
    assert brief["items"][0]["nodes"][0] == {"node": "NODE1", "id": "a1", "name": "set-a1"}
    assert get_capture(SETS, "2026-11-05_job") is not None and get_capture(SETS, "x") is None


def test_listing_captures_loads_no_polars(tmp_path: object) -> None:
    script = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from ragnalog.service.captures import captures_doc\n"
        "from ragnalog.store.store import Store\n"
        "captures_doc(Store(Path(sys.argv[1])))\n"
        "print(json.dumps('polars' in sys.modules))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, check=True
    )
    assert json.loads(out.stdout) is False
