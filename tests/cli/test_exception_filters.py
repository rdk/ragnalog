"""``--with-exception`` / ``--exception`` on groups and entries, ``groups --contains``."""

from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import Invoke
from .synth import EXCEPTION_MIX, write_log


@pytest.fixture
def mix(jcli: Invoke, tmp_path: Path) -> str:
    log = write_log(tmp_path / "logs" / "atlassian-jira.log", EXCEPTION_MIX)
    assert jcli("ingest", log, "--name", "mix").code == 0
    return "mix"


def _loggers(doc: dict[str, object]) -> set[str]:
    items = doc["items"]
    assert isinstance(items, list)
    return {str(row["logger"]) for row in items}


def test_groups_with_exception_finds_debug(jcli: Invoke, mix: str) -> None:
    warn_up = jcli("groups", mix, "--min-level", "WARN").json()
    assert "c.q.r.stats.type.StatTypeHelper" not in _loggers(warn_up)
    doc = jcli("groups", mix, "--with-exception").json()
    assert _loggers(doc) == {"c.q.r.stats.type.StatTypeHelper", "c.a.j.rest.ExceptionInterceptor"}
    assert doc["total"] == 2
    debug = next(g for g in doc["items"] if g["level_name"] == "DEBUG")
    assert debug["exception_type"] == "com.qoti.rf.exceptions.InternalException"
    both = jcli("groups", mix, "--with-exception", "--min-level", "WARN").json()
    assert _loggers(both) == {"c.a.j.rest.ExceptionInterceptor"}


def test_groups_exception_substring(jcli: Invoke, mix: str) -> None:
    doc = jcli("groups", mix, "--exception", "internalexception").json()
    assert _loggers(doc) == {"c.q.r.stats.type.StatTypeHelper"}
    assert jcli("groups", mix, "--exception", "java.lang.").json()["total"] == 1
    assert jcli("groups", mix, "--exception", "NoSuchThing").json()["total"] == 0


def test_groups_contains_template(jcli: Invoke, mix: str) -> None:
    doc = jcli("groups", mix, "--contains", "STAT TYPE failed").json()
    assert _loggers(doc) == {"c.q.r.stats.type.StatTypeHelper"}
    # the template is masked, the raw text is not searched by groups --contains
    assert jcli("groups", mix, "--contains", "1500").json()["total"] == 0
    assert jcli("groups", mix, "--contains", "slow query").json()["total"] == 1


def test_entries_exception_filters(jcli: Invoke, mix: str) -> None:
    doc = jcli("entries", mix, "--with-exception").json()
    assert doc["total"] == 2
    assert all(row["exception_type"] for row in doc["items"])
    one = jcli("entries", mix, "--exception", "illegalstate", "--brief").json()
    assert [row["logger"] for row in one["items"]] == ["c.a.j.rest.ExceptionInterceptor"]
    paged = jcli("entries", mix, "--with-exception", "--limit", "1").json()
    assert paged["total"] == 2 and paged["truncated"] is True


def test_store_counts_match(jcli: Invoke, mix: str, store_dir: Path) -> None:
    from atlas_log_analyzer.store import Store

    ls = Store(store_dir).open("mix")
    assert ls.count_groups(with_exception=True) == ls.groups(with_exception=True).height == 2
    assert ls.count_entries(exception="QOTI") == ls.entries(exception="QOTI").height == 1
    assert ls.count_groups(contains="slow") == 1
