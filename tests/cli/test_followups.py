"""Set timeline entry filters (CLI and API) and removal of a set with an unreadable manifest."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from ragnalog.webapp import Settings, create_app

from .conftest import MINI_LOG, Invoke


@pytest.fixture
def api(store_dir: Path) -> Iterator[TestClient]:
    with TestClient(create_app(Settings(store=store_dir, sync_jobs=True))) as client:
        yield client


def _total(doc: dict[str, object]) -> int:
    return sum(item["count"] for item in doc["items"])  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("flags", "query", "expected"),
    [
        (["--thread", "IssueIndexer:thread-15"], "thread=IssueIndexer:thread-15", 2),
        (["--contains", "Mail queue"], "contains=Mail%20queue", 1),
        (["--thread", "no-such-thread"], "thread=no-such-thread", 0),
    ],
)
def test_set_timeline_applies_entry_filters(
    jcli: Invoke,
    api: TestClient,
    flags: list[str],
    query: str,
    expected: int,
) -> None:
    assert jcli("ingest", MINI_LOG, "--name", "mini").code == 0
    entries = jcli("entries", "mini", *flags).json()
    cli = jcli("timeline", "mini", "--bucket", "1d", *flags)
    assert cli.code == 0
    got = api.get(f"/api/sets/mini/timeline?bucket=1d&{query}")

    assert got.status_code == 200
    assert got.json() == cli.json()
    assert _total(cli.json()) == entries["total"] == expected
    assert _total(jcli("timeline", "mini", "--bucket", "1d").json()) > expected


def test_capture_timeline_takes_the_entry_filters(jcli: Invoke) -> None:
    assert jcli("ingest", MINI_LOG, "--name", "mini").code == 0
    whole = jcli("timeline", "mini", "--by-node", "--bucket", "1d").json()

    def total(doc: dict[str, Any]) -> int:
        return sum(sum(s) for n in doc["nodes"].values() for s in n["levels"].values())

    none = jcli("timeline", "mini", "--by-node", "--bucket", "1d", "--thread", "no-such-thread")
    assert none.code == 0 and total(none.json()) == 0
    some = jcli("timeline", "mini", "--by-node", "--bucket", "1d", "--min-level", "ERROR").json()
    assert 0 < total(some) < total(whole)
    assert jcli("timeline", "mini", "--by-node", "--node", "nope").code == 1


def _corrupt(store_dir: Path, name: str = "badbadbadbad0001") -> None:
    bad = store_dir / name
    bad.mkdir(parents=True)
    (bad / "manifest.json").write_text("{not json")


def test_remove_deletes_a_set_with_a_corrupt_manifest(jcli: Invoke, store_dir: Path) -> None:
    assert jcli("ingest", MINI_LOG, "--name", "mini").code == 0
    _corrupt(store_dir)

    res = jcli("remove", "badbadbadbad0001")

    assert res.code == 0
    assert res.json() == {"schema": "remove.v1", "removed": "badbadbadbad0001", "name": None}
    assert not (store_dir / "badbadbadbad0001").exists()
    assert [i["name"] for i in jcli("sets").json()["items"]] == ["mini"]


def test_remove_unknown_ref_is_still_not_found(jcli: Invoke, store_dir: Path) -> None:
    assert jcli("ingest", MINI_LOG, "--name", "mini").code == 0
    _corrupt(store_dir)

    res = jcli("remove", "nosuchset")

    assert res.code == 3
    assert res.json()["error"]["code"] == "store_error"
    assert res.json()["error"]["reason"] == "set_not_found"
    assert (store_dir / "badbadbadbad0001").exists()


def test_api_delete_removes_a_set_with_a_corrupt_manifest(api: TestClient, store_dir: Path) -> None:
    _corrupt(store_dir)

    gone = api.delete("/api/sets/badbadbadbad0001")

    assert gone.status_code == 200
    assert gone.json()["removed"] == "badbadbadbad0001"
    assert not (store_dir / "badbadbadbad0001").exists()
    assert api.delete("/api/sets/nosuchset").status_code == 404
