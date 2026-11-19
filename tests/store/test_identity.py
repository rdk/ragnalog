"""Node, cluster and capture of a set: the manifest fields (schema 1.1) and ``Store.label``."""

from __future__ import annotations

import json
import threading

import pytest

from ragnalog.store import LogSetManifest, Store
from ragnalog.store.manifest import MANIFEST_FILE
from ragnalog.store.schema import SCHEMA_VERSION
from ragnalog.store.store import LogSetNotFound

from .conftest import Built, build
from .synth import WEIGHTS_B


def test_schema_version_is_1_1() -> None:
    assert SCHEMA_VERSION == "1.1"


def test_new_manifest_always_writes_the_three_keys(built: Built) -> None:
    data = json.loads((built.logset.path / MANIFEST_FILE).read_text(encoding="utf-8"))
    assert data["schema_version"] == "1.1"
    assert {k: data[k] for k in ("node", "cluster", "capture")} == dict.fromkeys(
        ("node", "cluster", "capture")
    )


def test_a_1_0_manifest_still_loads_and_is_not_stale(built: Built) -> None:
    from ragnalog.service.views import set_item

    path = built.logset.path / MANIFEST_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("node", "cluster", "capture"):
        del data[key]
    data["schema_version"] = "1.0"
    path.write_text(json.dumps(data), encoding="utf-8")
    store = Store(built.store.root)
    m = store.manifest("prod-a")
    assert (m.node, m.cluster, m.capture, m.schema_version) == (None, None, None, "1.0")
    assert store.open("prod-a").manifest == m
    item = set_item(m.to_dict(), None, brief=False)
    assert item["stale"] is False and item["node"] is None


def test_ingest_records_the_identity(store: Store) -> None:
    from ragnalog.core.signature import SignatureSpec

    from .synth import FakeFormat, generate, source_files

    sources = source_files(["n1.log"], "/logs")
    ls = store.ingest(
        generate(sources, WEIGHTS_B, 10),
        list,
        sources,
        FakeFormat(),  # type: ignore[arg-type]
        spec=SignatureSpec(),
        node="NODE1",
        cluster="acme",
        capture="cap-1",
    )
    m = LogSetManifest.read(ls.path)
    assert (m.node, m.cluster, m.capture) == ("NODE1", "acme", "cap-1")


def test_label_sets_keeps_and_clears(built: Built) -> None:
    store, before = built.store, built.logset.manifest
    m = store.label("prod-a", node="NODE1", cluster="acme")
    assert (m.node, m.cluster, m.capture) == ("NODE1", "acme", None)
    assert (m.id, m.name, m.created_at, m.entry_count) == (
        before.id,
        before.name,
        before.created_at,
        before.entry_count,
    )
    assert store.manifest(before.id) == m == LogSetManifest.read(built.logset.path)
    m = store.label(before.id[:6], capture="cap-1")  # an id prefix; node and cluster stay
    assert (m.node, m.cluster, m.capture) == ("NODE1", "acme", "cap-1")
    m = store.label("prod-a", cluster=None)
    assert (m.node, m.cluster, m.capture) == ("NODE1", None, "cap-1")
    assert store.list()[0] == m  # the listing cache sees the new manifest
    assert not [p for p in built.logset.path.iterdir() if p.name.endswith(".tmp")]


def test_label_unknown_set(built: Built) -> None:
    with pytest.raises(LogSetNotFound):
        built.store.label("nope", node="NODE1")


def test_label_from_threads_keeps_every_field(built: Built) -> None:
    store = built.store
    fields = {"node": "NODE1", "cluster": "acme", "capture": "cap-1"}
    threads = [
        threading.Thread(target=lambda k=k, v=v: store.label("prod-a", **{k: v}))
        for k, v in fields.items()
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    m = Store(store.root).manifest("prod-a")
    assert (m.node, m.cluster, m.capture) == ("NODE1", "acme", "cap-1")


def test_regroup_keeps_the_identity(built: Built) -> None:
    from ragnalog.core.signature import SignatureSpec

    built.store.label("prod-a", node="NODE1", cluster="acme", capture="cap-1")
    ls = built.store.regroup("prod-a", SignatureSpec(frames=1))
    assert (ls.manifest.node, ls.manifest.cluster, ls.manifest.capture) == (
        "NODE1",
        "acme",
        "cap-1",
    )


def test_two_sets_label_independently(store: Store) -> None:
    a = build(store, ["a.log"], WEIGHTS_B, name="a").logset.manifest
    b = build(store, ["b.log"], WEIGHTS_B, name="b").logset.manifest
    store.label("a", node="NODE1")
    assert store.manifest("b") == b and store.manifest("a").node == "NODE1"
    assert a.id != b.id


def test_refs_never_leave_the_store(built: Built, monkeypatch: pytest.MonkeyPatch) -> None:
    """A ref is one plain path part: no separators, no "..", no drive ("C:x" on Windows)."""
    store, ls = built.store, built.logset
    outside = store.root.parent / ls.id  # a set next to the store, not in it
    if not outside.exists():
        import shutil

        shutil.copytree(ls.path, outside)
    drive = store.root.drive or "C:"
    monkeypatch.chdir(outside.parent)  # "C:<id>" is relative to drive C's current directory
    for ref in (f"{drive}..", f"..{chr(92)}{ls.id}", f"../{ls.id}", f"{drive}{ls.id}", "a:b"):
        with pytest.raises(LogSetNotFound):
            store.open(ref)
    assert store.open(ls.id).id == ls.id
