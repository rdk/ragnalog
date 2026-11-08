"""Unique signature prefixes (4+ hex characters) in ``show``, ``frames`` and ``entries``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from .conftest import MINI_LOG, Invoke
from .synth import write_log
from .test_diff_rate import wide


@pytest.fixture
def mini(jcli: Invoke) -> str:
    assert jcli("ingest", MINI_LOG, "--name", "mini").code == 0
    return "mini"


@pytest.fixture
def crowded(jcli: Invoke, tmp_path: Path) -> str:
    """A 1500-group set and a 4-hex prefix shared by several of its signatures."""
    log = write_log(tmp_path / "crowded" / "atlassian-jira.log", wide(1500))
    assert jcli("ingest", log, "--name", "crowded").code == 0
    sql = (
        "select substr(signature, 1, 4) p from groups group by 1 having count(*) > 1"
        " order by count(*) desc, 1 limit 1"
    )
    return str(jcli("query", "crowded", sql).json()["items"][0]["p"])


def _sig(jcli: Invoke, *args: str) -> str:
    return str(jcli("groups", "mini", *args, "--limit", "1").json()["items"][0]["signature"])


def test_show_accepts_unique_prefix(jcli: Invoke, mini: str) -> None:
    sig = _sig(jcli, "--level", "ERROR")
    full = jcli("show", mini, sig).json()
    short = jcli("show", mini, sig[:6]).json()
    assert short == full and short["signature"] == sig


def test_entries_signature_prefix(jcli: Invoke, mini: str) -> None:
    sig = _sig(jcli)
    full = jcli("entries", mini, "--signature", sig).json()
    assert jcli("entries", mini, "--signature", sig[:4]).json() == full and full["total"] > 0


def test_frames_prefix_and_entry_id(jcli: Invoke, mini: str) -> None:
    sig = _sig(jcli, "--exception", "DataAccess")
    full = jcli("frames", mini, sig).json()
    assert jcli("frames", mini, sig[:5]).json() == full
    assert jcli("frames", mini, full["entry"]).json() == full  # entry ids still work


def test_short_or_unknown_prefix_is_not_found(jcli: Invoke, mini: str) -> None:
    sig = _sig(jcli)
    assert jcli("show", mini, sig[:3]).code == 2  # below 4 characters: no prefix lookup
    unused = next(p for p in ("0000", "ffff", "1234", "abcd") if not sig.startswith(p))
    res = jcli("show", mini, unused)
    assert res.code == 2 and res.json()["error"]["code"] == "not_found"
    assert jcli("frames", mini, unused).code == 2


def _ambiguous(doc: Any, prefix: str) -> None:
    err = doc["error"]
    assert err["code"] == "ambiguous" and prefix in err["message"]
    assert 2 <= len(err["candidates"]) <= 5
    assert err["candidates"] == sorted(err["candidates"])
    assert all(c.startswith(prefix) and len(c) == 16 for c in err["candidates"])
    assert "more characters" in err["hint"]


def test_ambiguous_prefix(jcli: Invoke, crowded: str) -> None:
    for args in (
        ("show", "crowded", crowded),
        ("frames", "crowded", crowded),
        ("entries", "crowded", "--signature", crowded),
    ):
        res = jcli(*args)
        assert res.code == 1, args
        _ambiguous(res.json(), crowded)
    first = jcli("show", "crowded", crowded).json()["error"]["candidates"][0]
    assert jcli("show", "crowded", first).json()["signature"] == first  # full ones still work


def test_upper_case_signatures_match(jcli: Invoke, mini: str) -> None:
    """``--signature 83D1…`` silently matched nothing."""
    sig = _sig(jcli, "--level", "ERROR")
    lower = jcli("entries", mini, "--signature", sig).json()
    assert lower["total"] > 0
    assert jcli("entries", mini, "--signature", sig.upper()).json()["total"] == lower["total"]
    assert jcli("entries", mini, "--signature", sig[:6].upper()).json()["total"] == lower["total"]
    assert jcli("show", mini, sig[:6].upper()).json()["signature"] == sig
    assert jcli("frames", mini, sig.upper()).code == jcli("frames", mini, sig).code
