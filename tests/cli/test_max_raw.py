"""``--max-raw`` on ``show`` and ``entries``: long raw/body texts are cut in JSON by default."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from .conftest import Invoke
from .synth import E, long_body, write_log

SUFFIX = "chars total, use --max-raw 0]"


@pytest.fixture
def long_set(jcli: Invoke, tmp_path: Path) -> str:
    log = write_log(
        tmp_path / "logs" / "atlassian-jira.log",
        [
            E("09:00:00,000", "Caesium-1-1", "INFO", "c.a.jira.Sched", "Job ran"),
            E(
                "09:00:01,000",
                "http-nio-8080-exec-1",
                "DEBUG",
                "c.e.reports.StatHelper",
                "Stat dump follows",
                body=long_body(200),
            ),
        ],
    )
    assert jcli("ingest", log, "--name", "long").code == 0
    return "long"


def _by_level(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["level_name"]: row for row in items}


def test_entries_json_default_cap(jcli: Invoke, long_set: str) -> None:
    rows = _by_level(jcli("entries", long_set).json()["items"])
    long, short = rows["DEBUG"], rows["INFO"]
    assert long["body_truncated"] is True
    assert long["raw_chars"] > 16_000
    assert long["raw"].endswith(f"… [truncated: {long['raw_chars']} {SUFFIX}")
    assert len(long["raw"]) < 4100 and len(long["body"]) < 4100
    assert long["body"].endswith(SUFFIX)
    assert "body_truncated" not in short and "raw_chars" not in short


def test_entries_max_raw_explicit_and_unlimited(jcli: Invoke, long_set: str) -> None:
    full = _by_level(jcli("entries", long_set, "--max-raw", "0").json()["items"])["DEBUG"]
    assert "body_truncated" not in full and len(full["raw"]) > 16_000
    cut = _by_level(jcli("entries", long_set, "--max-raw", "100").json()["items"])["DEBUG"]
    assert cut["raw"] == full["raw"][:100] + f"… [truncated: {len(full['raw'])} {SUFFIX}"
    assert cut["raw_chars"] == len(full["raw"])


def test_entries_columns_without_text_are_untouched(jcli: Invoke, long_set: str) -> None:
    rows = jcli("entries", long_set, "--columns", "id,level_name").json()["items"]
    assert all(set(row) == {"id", "level_name"} for row in rows)
    body_only = jcli("entries", long_set, "--columns", "body", "--max-raw", "10").json()["items"]
    cut = next(r for r in body_only if "body_truncated" in r)
    assert cut["raw_chars"] > 16_000 - 100  # the body's length when raw is not selected


def test_entries_jsonl_caps_too(cli: Invoke, long_set: str, store_dir: Path) -> None:
    rows = cli("--output", "jsonl", "--store", store_dir, "entries", long_set).jsonl()
    assert any(r.get("body_truncated") for r in rows)


def test_raw_mode_unlimited_unless_given(cli: Invoke, long_set: str, store_dir: Path) -> None:
    base = ("--color", "never", "--store", store_dir, "entries", long_set, "--level", "DEBUG")
    full = cli(*base, "--raw")
    assert "detail 00199" in full.stdout and SUFFIX not in full.stdout
    cut = cli(*base, "--raw", "--max-raw", "50")
    assert SUFFIX in cut.stdout and "detail 00199" not in cut.stdout


def test_show_caps(jcli: Invoke, cli: Invoke, long_set: str, store_dir: Path) -> None:
    sig = jcli("groups", long_set, "--level", "DEBUG").json()["items"][0]["signature"]
    doc = jcli("show", long_set, sig).json()
    assert doc["items"][0]["body_truncated"] is True
    assert "body_truncated" not in jcli("show", long_set, sig, "--max-raw", "0").json()["items"][0]
    raw = cli("--color", "never", "--store", store_dir, "show", long_set, sig, "--raw")
    assert "detail 00199" in raw.stdout
    capped = cli("--store", store_dir, "show", long_set, sig, "--raw", "--max-raw", "40")
    assert SUFFIX in capped.stdout


def test_negative_max_raw(jcli: Invoke, long_set: str) -> None:
    res = jcli("entries", long_set, "--max-raw", "-1")
    assert res.code == 1 and res.json()["error"]["code"] == "invalid_value"
