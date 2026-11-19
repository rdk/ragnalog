"""Request models (``service/params.py``): one parser per value for both front ends."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from ragnalog.service.errors import OpError
from ragnalog.service.params import (
    DEFAULT_MAX_RAW,
    MAX_COUNT,
    EntryQuery,
    GroupQuery,
    Page,
    Select,
    TextCap,
    blank,
    check_count,
    check_out,
    hex_ref,
    parse_columns,
    parse_level,
    parse_time,
)

LAST = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
MANIFEST: Any = SimpleNamespace(last_ts=LAST)
VALID = ("id", "ts", "level_name", "message")


def _error(call: Any) -> OpError:
    with pytest.raises(OpError) as info:
        call()
    return info.value


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-10-29T09:00", datetime(2026, 10, 29, 9, 0, tzinfo=UTC)),  # naive = UTC
        ("2026-10-29", datetime(2026, 10, 29, tzinfo=UTC)),
        ("2026-10-29T10:00:00+01:00", datetime(2026, 10, 29, 9, 0, tzinfo=UTC)),
        ("2h", LAST - timedelta(hours=2)),
        (" 30M ", LAST - timedelta(minutes=30)),
        ("1d", LAST - timedelta(days=1)),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_parse_time_forms(text: str | None, expected: datetime | None) -> None:
    assert parse_time(text, "since", LAST, front="cli") == expected


def test_parse_time_relative_without_anchor_counts_from_now() -> None:
    got = parse_time("1h", "since", None, front="api")
    assert got is not None and abs(datetime.now(UTC) - timedelta(hours=1) - got) < timedelta(1)


@pytest.mark.parametrize(("front", "name"), [("cli", "--until"), ("api", "until")])
def test_parse_time_error_names_the_parameter(front: Any, name: str) -> None:
    err = _error(lambda: parse_time("yesterday", "until", LAST, front=front))
    assert err.code == "invalid_value" and err.message.startswith(f"{name} 'yesterday'")
    assert "30m" in err.message


def test_parse_level() -> None:
    assert parse_level(" warn, Error ", "level", front="cli") == "WARN,ERROR"
    assert parse_level("UNKNOWN", "level", front="api") == "UNKNOWN"
    assert parse_level(" ", "level", front="cli") is None
    err = _error(lambda: parse_level("SEVERE", "min_level", front="api"))
    assert err.code == "invalid_level" and err.message.startswith("min_level: unknown level")


def test_parse_columns() -> None:
    assert parse_columns(None, VALID, front="cli") is None
    assert parse_columns(["ts,message", "ts", "id"], VALID, front="cli") == ["ts", "message", "id"]
    err = _error(lambda: parse_columns(["lvl_name,nope"], VALID, front="cli"))
    assert err.code == "invalid_value"
    assert err.message == (
        "unknown columns 'lvl_name' (did you mean 'level_name'?), 'nope' in --columns"
    )
    assert err.hint == "valid columns: id, ts, level_name, message"
    assert _error(lambda: parse_columns([","], VALID, front="api")).message == "columns is empty"


def test_select() -> None:
    preset = ("id", "ts")
    assert Select().resolve(VALID, preset, front="cli") is None
    assert Select(brief=True).resolve(VALID, preset, front="cli") == ["id", "ts"]
    assert Select(columns=["message"]).resolve(VALID, preset, front="cli") == ["message"]
    err = _error(lambda: Select(columns=["id"], brief=True).resolve(VALID, preset, front="cli"))
    assert (err.code, err.message, err.hint) == (
        "usage",
        "--brief and --columns cannot be combined",
        "--brief is --columns id,ts",
    )
    api = _error(lambda: Select(columns=["id"], brief=True).resolve(VALID, preset, front="api"))
    assert api.message == "brief and columns cannot be combined"


def test_page() -> None:
    assert Page.parse(0, front="cli") == Page(None, 0)
    assert Page.parse(5, 10, front="cli") == Page(5, 10)
    assert _error(lambda: Page.parse(-1, front="cli")).message.startswith("--limit must be >= 0")
    assert _error(lambda: Page.parse(1, -1, front="api")).message == "offset must be >= 0"
    assert Page(None, 0).complete(7) and Page(10, 0).complete(9)
    assert not Page(10, 0).complete(10) and not Page(None, 5).complete(1)


@pytest.mark.parametrize("big", [MAX_COUNT + 1, 2**63, 10**20])
def test_counts_past_the_store_are_invalid_values(big: int) -> None:
    """Values past int32 (the API's bound) used to overflow the store: internal_error."""
    assert Page.parse(MAX_COUNT, MAX_COUNT, front="cli") == Page(MAX_COUNT, MAX_COUNT)
    for call in (
        lambda: Page.parse(big, front="cli"),
        lambda: Page.parse(1, big, front="api"),
        lambda: TextCap.parse(big, default=None, front="cli"),
        lambda: check_count(big, "before", front="cli"),
    ):
        err = _error(call)
        assert err.code == "invalid_value" and err.message.endswith(f"must be <= {MAX_COUNT}")
    assert check_count(0, "after", front="cli") == 0
    assert _error(lambda: check_count(-1, "after", front="api")).message == "after must be >= 0"


def test_check_out_refuses_a_directory(tmp_path: Any) -> None:
    check_out(None, front="cli")
    check_out(tmp_path / "r.html", front="cli")
    err = _error(lambda: check_out(tmp_path, front="cli"))
    assert err.code == "usage" and err.message.startswith("--out ") and "directory" in err.message


def test_hex_refs_are_lowercased() -> None:
    assert hex_ref("83D1828C6AFB2EAD") == "83d1828c6afb2ead" and hex_ref(" ABCD ") == "abcd"
    assert hex_ref("ABC") == "ABC" and hex_ref("NODE1") == "NODE1"  # not a signature
    q = EntryQuery(signature="83D1", coarse_signature="76AC9B8F87C9F7B3")
    where = q.resolve(MANIFEST, front="cli")
    assert (where.signature, where.coarse_signature) == ("83d1", "76ac9b8f87c9f7b3")


def test_text_cap() -> None:
    assert TextCap.parse(None, default=DEFAULT_MAX_RAW, front="cli") == TextCap(4000)
    assert TextCap.parse(0, default=4000, front="api") == TextCap(None, "use max_raw=0")
    assert TextCap.parse(None, default=None, front="cli").limit is None
    assert _error(lambda: TextCap.parse(-1, default=None, front="cli")).code == "invalid_value"
    rows = TextCap(5, "use max_raw=0").rows([{"raw": "abcdefgh", "body": "xy"}])
    assert rows == [
        {
            "raw": "abcde… [truncated: 8 chars total, use max_raw=0]",
            "body": "xy",
            "body_truncated": True,
            "raw_chars": 8,
        }
    ]
    assert TextCap(3).text("abcdef") == "abc… [truncated: 6 chars total, use --max-raw 0]"
    assert TextCap(None).text("abcdef") == "abcdef"


def test_group_query_resolves_against_the_set() -> None:
    q = GroupQuery(level="error", since="2h", until="", logger=" ", contains="x")
    where = q.resolve(MANIFEST, front="cli")
    assert where.level == "ERROR" and where.since == LAST - timedelta(hours=2)
    assert where.until is None and where.logger is None and where.contains == "x"


def test_entry_query_blank_values_are_unset() -> None:
    q = EntryQuery(signature="", user=" ", thread="t1", request_id="", with_exception=True)
    where = q.resolve(MANIFEST, front="api")
    assert where.signature is None and where.user is None and where.request_id is None
    assert where.thread == "t1" and where.with_exception


def test_check_runs_without_a_set() -> None:
    EntryQuery(level="WARN", since="2h").check(front="cli")
    assert _error(lambda: GroupQuery(min_level="loud").check(front="cli")).code == "invalid_level"
    assert _error(lambda: EntryQuery(until="soon").check(front="api")).code == "invalid_value"


def test_blank() -> None:
    assert blank(None) is None and blank("") is None and blank(" \t") is None
    assert blank(" a ") == " a "


@pytest.mark.parametrize("text", ["0001-01-01T00:00+01:00", "9" * 5000 + "d", "99999999999d"])
def test_times_out_of_range_are_invalid_values(text: str) -> None:
    err = _error(lambda: parse_time(text, "since", LAST, front="api"))
    assert err.code == "invalid_value"
