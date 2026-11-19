"""Read documents built by ``service/queries.py`` over a real store (no front end)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ragnalog.service import queries
from ragnalog.service.errors import OpError
from ragnalog.service.params import EntryQuery, GroupQuery, Page, Select, TextCap
from ragnalog.service.sets import Sets, open_set
from ragnalog.store.pipeline import ingest_paths
from ragnalog.store.store import Store

MINI = Path(__file__).resolve().parents[1] / "cli" / "data" / "mini.log"
ALL = Select()


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Store:
    store = Store(tmp_path_factory.mktemp("service") / "store")
    ingest_paths(store, [MINI], name="mini")
    return store


def test_store_is_a_sets(store: Store) -> None:
    sets: Sets = store
    assert [m.name for m in sets.manifests()] == ["mini"]


def test_open_set_errors_per_front_end(store: Store) -> None:
    with pytest.raises(OpError) as cli:
        open_set(store, "nope", front="cli")
    assert (cli.value.code, cli.value.reason, cli.value.hint) == (
        "store_error",
        "set_not_found",
        "available sets: mini",
    )
    with pytest.raises(OpError) as api:
        open_set(store, "nope", front="api")
    assert (api.value.code, api.value.status) == ("not_found", 404)
    assert api.value.hint == "list the sets with GET /api/sets"


def test_entries_pages_with_next_offset(store: Store) -> None:
    first = queries.entries(
        store, "mini", EntryQuery(), Page(4, 0), ALL, TextCap(None), front="cli"
    )
    assert (first["count"], first["total"], first["next_offset"], first["truncated"]) == (
        4,
        10,
        4,
        True,
    )
    last = queries.entries(store, "mini", EntryQuery(), Page(4, 8), ALL, TextCap(), front="cli")
    assert (last["count"], last["next_offset"], last["truncated"]) == (2, None, False)
    assert list(first) == [
        "schema",
        "logset",
        "count",
        "total",
        "offset",
        "next_offset",
        "truncated",
        "items",
    ]


def test_groups_total_without_a_count_query(store: Store) -> None:
    doc = queries.groups(store, "mini", GroupQuery(), Page(None, 0), ALL, front="api")
    assert doc["schema"] == "groups.v1" and doc["count"] == doc["total"] > 1
    assert not doc["truncated"]


def test_coarse_signature_filter(store: Store) -> None:
    coarse = queries.groups(
        store, "mini", GroupQuery(), Page(1, 0), Select(columns=["signature"]), coarse=True,
        front="cli",
    )  # fmt: skip
    sig = coarse["items"][0]["signature"]
    q = EntryQuery(coarse_signature=sig)
    doc = queries.entries(store, "mini", q, Page(None, 0), ALL, TextCap(None), front="cli")
    assert doc["total"] > 0 and {r["coarse_signature"] for r in doc["items"]} == {sig}


def test_sets_doc_lists_a_damaged_set_with_null_summary(store: Store) -> None:
    def broken(_: object) -> dict[str, int]:
        raise OSError("unreadable")

    doc = queries.sets_doc(store, brief=True, store="s", summary=broken)
    assert doc["items"][0]["summary"] is None and doc["store"] == "s"


def test_raw_entries_pairs(store: Store) -> None:
    pairs = queries.raw_entries(store, "mini", EntryQuery(), Page(2, 0), TextCap(10), front="cli")
    assert len(pairs) == 2
    raw, label = pairs[0]
    assert label is not None and label.startswith("mini.log:")
    assert "[truncated:" in raw or len(raw) <= 10


def test_timeline_bad_bucket(store: Store) -> None:
    with pytest.raises(OpError) as info:
        queries.timeline(store, "mini", EntryQuery(), bucket="7x", front="api")
    assert info.value.code == "invalid_value" and info.value.message.startswith("bucket: ")
