"""``--offset`` paging of ``groups`` and ``entries``: ``offset`` in the envelope, ``truncated``."""

from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import MINI_LOG, Invoke


@pytest.fixture
def mini(jcli: Invoke) -> str:
    assert jcli("ingest", MINI_LOG, "--name", "mini").code == 0
    return "mini"


def test_groups_pages_cover_the_listing(jcli: Invoke, mini: str) -> None:
    full = jcli("groups", mini, "--limit", "0").json()
    total = full["total"]
    assert full["offset"] == 0 and full["truncated"] is False and total > 4
    seen: list[str] = []
    offset = 0
    while True:
        page = jcli("groups", mini, "--limit", "3", "--offset", offset).json()
        assert page["offset"] == offset and page["total"] == total
        assert page["count"] == len(page["items"]) <= 3
        seen += [g["signature"] for g in page["items"]]
        assert page["truncated"] is (offset + page["count"] < total)
        if not page["truncated"]:
            break
        offset += page["count"]
    assert seen == [g["signature"] for g in full["items"]]


def test_groups_offset_past_the_end(jcli: Invoke, mini: str) -> None:
    doc = jcli("groups", mini, "--offset", "100").json()
    assert doc["items"] == [] and doc["count"] == 0
    assert doc["total"] > 0 and doc["truncated"] is False and doc["offset"] == 100


def test_groups_offset_with_sort_and_coarse(jcli: Invoke, mini: str) -> None:
    full = jcli("groups", mini, "--sort", "template", "--coarse", "--limit", "0").json()
    page = jcli("groups", mini, "--sort", "template", "--coarse", "--offset", "1", "--limit", "2")
    doc = page.json()
    assert [g["signature"] for g in doc["items"]] == [g["signature"] for g in full["items"][1:3]]
    assert doc["total"] == full["total"]


def test_entries_offset(jcli: Invoke, mini: str) -> None:
    full = jcli("entries", mini, "--limit", "0", "--columns", "id").json()
    assert full["offset"] == 0 and full["total"] == 10 and full["truncated"] is False
    page = jcli("entries", mini, "--limit", "4", "--offset", "4", "--columns", "id").json()
    assert page["offset"] == 4 and page["total"] == 10 and page["truncated"] is True
    assert [e["id"] for e in page["items"]] == [e["id"] for e in full["items"][4:8]]
    last = jcli("entries", mini, "--limit", "4", "--offset", "8", "--columns", "id").json()
    assert last["count"] == 2 and last["truncated"] is False and last["total"] == 10
    rest = jcli("entries", mini, "--offset", "3", "--limit", "0", "--level", "ERROR").json()
    assert rest["total"] == 5 and rest["count"] == 2 and rest["truncated"] is False


def test_entries_raw_offset(cli: Invoke, mini: str, store_dir: Path) -> None:
    res = cli("--store", store_dir, "entries", mini, "--raw", "--offset", "3", "--limit", "1")
    assert res.stdout.startswith("2026-10-29 02:20:00,001+0100 Caesium-1-3")


def test_negative_offset(jcli: Invoke, mini: str) -> None:
    for command in ("groups", "entries"):
        res = jcli(command, mini, "--offset", "-1")
        assert res.code == 1 and res.json()["error"]["code"] == "invalid_value"


@pytest.mark.parametrize(
    "argv",
    [
        ("groups", "mini", "--limit", "99999999999999999999"),
        ("groups", "mini", "--offset", "99999999999999999999"),
        ("entries", "mini", "--limit", "9223372036854775807", "--offset", "1"),
        ("entries", "mini", "--max-raw", "2147483648", "--limit", "1"),
        ("diff", "mini", "mini", "--limit", "99999999999999999999"),
        ("report", "mini", "--max-raw", "99999999999999999999"),
    ],
)
def test_counts_past_int32_are_invalid_values(jcli: Invoke, mini: str, argv: tuple[str]) -> None:
    """They used to overflow the store's queries: internal_error."""
    res = jcli(*argv)
    assert res.code == 1 and res.json()["error"]["code"] == "invalid_value", res.stdout
    assert "must be <= 2147483647" in res.json()["error"]["message"]


def test_context_bounds(jcli: Invoke, mini: str) -> None:
    entry = jcli("entries", mini, "--limit", "1", "--columns", "id").json()["items"][0]["id"]
    for option in ("--before", "--after"):
        res = jcli("context", mini, entry, option, "99999999999999999999")
        assert res.code == 1 and res.json()["error"]["code"] == "invalid_value", option


def test_table_shows_offset(cli: Invoke, mini: str, store_dir: Path) -> None:
    res = cli("--output", "table", "--store", store_dir, "groups", mini, "--offset", "2")
    assert "offset=2" in res.stderr
