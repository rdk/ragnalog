"""``--columns``: comma-separated or repeated, validated against the stored schema."""

from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import MINI_LOG, Invoke


@pytest.fixture
def mini(jcli: Invoke) -> str:
    assert jcli("ingest", MINI_LOG, "--name", "mini").code == 0
    return "mini"


def test_entries_columns_comma_separated(jcli: Invoke, mini: str) -> None:
    res = jcli("entries", mini, "--columns", "ts,level_name", "--limit", "3")
    assert res.code == 0, res.stdout
    items = res.json()["items"]
    assert len(items) == 3 and all(list(row) == ["ts", "level_name"] for row in items)


def test_entries_columns_repeated_and_mixed(jcli: Invoke, mini: str) -> None:
    repeated = jcli("entries", mini, "--columns", "id", "--columns", "level").json()["items"]
    assert repeated and all(set(row) == {"id", "level"} for row in repeated)
    mixed = jcli(
        "entries", mini, "--columns", "ts, logger", "--columns", "message,ts", "--limit", "1"
    ).json()["items"]
    assert list(mixed[0]) == ["ts", "logger", "message"]  # order kept, duplicate dropped


def test_entries_unknown_column(jcli: Invoke, mini: str) -> None:
    res = jcli("entries", mini, "--columns", "ts,lvl_name")
    assert res.code == 1
    err = res.json()["error"]
    assert err["code"] == "invalid_value"
    assert "'lvl_name'" in err["message"] and "level_name" in err["message"]
    assert "valid columns:" in err["hint"] and "exception_type" in err["hint"]


def test_entries_empty_columns(jcli: Invoke, mini: str) -> None:
    res = jcli("entries", mini, "--columns", ",")
    assert res.code == 1 and res.json()["error"]["code"] == "invalid_value"


def test_entries_columns_table(cli: Invoke, mini: str, store_dir: object) -> None:
    res = cli("--output", "table", "--store", store_dir, "entries", mini, "--columns", "ts,logger")
    assert res.code == 0
    header = next(line for line in res.stdout.splitlines() if " ts " in line)
    assert "logger" in header and "message" not in header


GROUP_BRIEF = [
    "signature",
    "template",
    "level_name",
    "logger",
    "exception_type",
    "count",
    "first_seen",
    "last_seen",
]
ENTRY_BRIEF = [
    "id",
    "ts",
    "level_name",
    "thread",
    "user",
    "request_id",
    "logger",
    "message",
    "exception_type",
    "signature",
]
DETAIL_BRIEF = [*ENTRY_BRIEF, "variables"]


def test_groups_columns(jcli: Invoke, mini: str) -> None:
    doc = jcli("groups", mini, "--columns", "signature,count", "--limit", "0").json()
    assert doc["items"] and all(list(g) == ["signature", "count"] for g in doc["items"])
    assert doc["total"] == doc["count"] and doc["truncated"] is False
    repeated = jcli("groups", mini, "--columns", "count", "--columns", "template").json()
    assert all(list(g) == ["count", "template"] for g in repeated["items"])
    coarse = jcli("groups", mini, "--coarse", "--columns", "signature,signatures").json()
    assert all(isinstance(g["signatures"], list) for g in coarse["items"])


def test_groups_unknown_column(jcli: Invoke, mini: str) -> None:
    res = jcli("groups", mini, "--columns", "signature,signatures")  # only with --coarse
    assert res.code == 1
    err = res.json()["error"]
    assert err["code"] == "invalid_value" and "'signatures'" in err["message"]
    assert "top_users" in err["hint"]


def test_brief_presets(jcli: Invoke, mini: str) -> None:
    groups = jcli("groups", mini, "--brief").json()["items"]
    assert groups and all(list(g) == GROUP_BRIEF for g in groups)
    entries = jcli("entries", mini, "--brief", "--limit", "3").json()
    assert entries["count"] == 3 and all(list(e) == ENTRY_BRIEF for e in entries["items"])


def test_brief_with_columns_is_usage_error(jcli: Invoke, mini: str) -> None:
    sig = jcli("groups", mini, "--limit", "1").json()["items"][0]["signature"]
    entry = jcli("entries", mini, "--limit", "1").json()["items"][0]["id"]
    for args in (
        ("groups", mini),
        ("entries", mini),
        ("show", mini, sig),
        ("context", mini, entry),
    ):
        res = jcli(*args, "--brief", "--columns", "signature")
        assert res.code == 1
        err = res.json()["error"]
        assert err["code"] == "usage" and "--brief" in err["message"]


def test_show_columns_and_brief(jcli: Invoke, mini: str) -> None:
    sig = jcli("groups", mini, "--level", "ERROR", "--limit", "1").json()["items"][0]["signature"]
    full = jcli("show", mini, sig).json()
    brief = jcli("show", mini, sig, "--brief").json()
    assert brief["count"] == full["count"] == 3
    assert all(list(row) == DETAIL_BRIEF for row in brief["items"])
    assert list(brief["group"]) == GROUP_BRIEF and len(full["group"]) > len(GROUP_BRIEF)
    assert [r["id"] for r in brief["items"]] == [r["id"] for r in full["items"]]
    picked = jcli("show", mini, sig, "--columns", "ts,variables").json()
    assert all(list(row) == ["ts", "variables"] for row in picked["items"])
    assert picked["group"] == full["group"]
    bad = jcli("show", mini, sig, "--columns", "nope")
    assert bad.code == 1 and bad.json()["error"]["code"] == "invalid_value"


def test_context_columns_and_brief(cli: Invoke, jcli: Invoke, mini: str, store_dir: Path) -> None:
    ids = [
        r["id"] for r in jcli("entries", mini, "--columns", "id", "--limit", "0").json()["items"]
    ]
    anchor = ids[4]
    full = jcli("context", mini, anchor, "--any-thread").json()
    brief = jcli("context", mini, anchor, "--any-thread", "--brief").json()
    assert all(list(row) == DETAIL_BRIEF for row in brief["items"])
    assert [r["id"] for r in brief["items"]] == [r["id"] for r in full["items"]]
    assert brief["thread"] == full["thread"] and brief["anchor"] == anchor
    picked = jcli("context", mini, anchor, "--any-thread", "--columns", "message").json()
    assert all(list(row) == ["message"] for row in picked["items"])
    args = ("--store", store_dir, "context", mini, anchor, "--any-thread", "--columns", "message")
    rows = cli("--output", "jsonl", *args).jsonl()
    position = [r["id"] for r in full["items"]].index(anchor)
    assert [i for i, r in enumerate(rows) if r.get("anchor")] == [position]
    table = cli("--output", "table", *args)
    assert table.code == 0 and sum("\u25b6" in line for line in table.stdout.splitlines()) == 1


def test_brief_table(cli: Invoke, mini: str, store_dir: object) -> None:
    res = cli("--output", "table", "--width", 200, "--store", store_dir, "groups", mini, "--brief")
    assert res.code == 0
    header = next(line for line in res.stdout.splitlines() if "signature" in line)
    assert "template" in header and "top_users" not in header
