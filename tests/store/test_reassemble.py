"""``upgrade`` of sets built by an older Jira format: banners and titles from stored entries."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest

from atlas_log_analyzer.core.formats.base import CTX_PREAMBLE
from atlas_log_analyzer.core.formats.jira import JiraFormat
from atlas_log_analyzer.core.parser import attach_banners, parse_file
from atlas_log_analyzer.core.pipeline import Recipe
from atlas_log_analyzer.core.signature import SignatureSpec
from atlas_log_analyzer.store import LogSet, Store
from atlas_log_analyzer.store.pipeline import ingest_paths
from atlas_log_analyzer.store.regroup import resign_entries

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "jira"
MINI = Path(__file__).resolve().parents[1] / "cli" / "data" / "mini.log"
STARS = "*" * 50


class JiraV1(JiraFormat):
    """Jira as format version 1 built it: no banners attached, no titles from the body."""

    version = 1
    banner_window_ms = 0
    titles_from_body = False


def _line(ms: str, thread: str, logger: str, message: str) -> str:
    return f"2026-10-29 11:00:{ms}+0100 {thread} ERROR      [{logger}] {message}"


@pytest.fixture
def logs(tmp_path: Path) -> list[Path]:
    """Copies of the fixtures plus a log whose first entry is a banner and one of a run."""
    folder = tmp_path / "logs"
    folder.mkdir()
    out = [shutil.copy(p, folder / p.name) for p in (FIXTURES / "banners.log", MINI)]
    out.append(shutil.copy(FIXTURES / "sample.log", folder / "sample.log"))
    run = folder / "run.log"
    banners = [_line(f"00,00{i}", "exec-1", "org.acme.A", "=" * (20 + i)) for i in range(3)]
    run.write_text(
        "\n".join(
            [
                _line("00,000", "exec-9", "org.acme.B", STARS),  # first entry of the file
                _line("00,001", "exec-9", "org.acme.B", "Field failed for erin"),
                *banners,
                _line("00,004", "exec-1", "org.acme.A", "Field failed for dave"),
                _line("00,006", "exec-2", "org.acme.A", STARS),  # last entry of the file
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return [Path(p) for p in (*out, run)]


def _tables(ls: LogSet) -> tuple[pl.DataFrame, pl.DataFrame]:
    return pl.read_parquet(ls.entries_path), pl.read_parquet(ls.groups_path)


def _manifest(ls: LogSet) -> dict[str, object]:
    doc = ls.manifest.to_dict()
    doc.pop("created_at")
    return doc


def test_upgrade_equals_ingest_and_needs_no_log_files(tmp_path: Path, logs: list[Path]) -> None:
    new = ingest_paths(Store(tmp_path / "new"), logs, name="fx").logset
    old_store = Store(tmp_path / "old")
    old = ingest_paths(old_store, logs, name="fx", fmt=JiraV1()).logset
    assert old.manifest.recipe_key == "jira@1|L+G+E+F0|sig1|mask2"
    assert new.manifest.recipe_key == "jira@3|L+G+E+F0|sig1|mask2"
    assert old.manifest.entry_count == new.manifest.entry_count + 10
    shutil.rmtree(logs[0].parent)  # upgrade reads the stored entries only

    result = old_store.upgrade("fx")
    assert result.upgraded and result.previous_recipe_key == old.manifest.recipe_key
    up = old_store.open("fx")
    entries, groups = _tables(up)
    want_entries, want_groups = _tables(new)
    assert entries.equals(want_entries)
    assert groups.equals(want_groups)
    assert _manifest(up) == _manifest(new)
    run = next(s for s in up.manifest.sources if s.path.endswith("run.log"))
    assert run.entries == 3  # the leading banner and the run of three joined their entries
    assert run.first_ts is not None and run.first_ts.microsecond == 1000  # 11:00:00,001


def test_preambles_and_titles_after_upgrade(tmp_path: Path, logs: list[Path]) -> None:
    store = Store(tmp_path / "old")
    ingest_paths(store, logs, name="fx", fmt=JiraV1())
    store.upgrade("fx")
    ls = store.open("fx")
    rows = (
        ls.entries_lazy()
        .filter(pl.col("context").map.get(CTX_PREAMBLE).is_not_null())
        .select("message", pl.col("context").map.get(CTX_PREAMBLE).alias("pre"))
        .collect()
    )
    pre = dict(rows.iter_rows())
    assert pre["Field failed for dave"].count("\n") == 2  # three banners, in order
    assert pre["Field failed for erin"] == _line("00,000", "exec-9", "org.acme.B", STARS)
    templates = set(ls.groups()["template"].to_list())
    assert {"Acme starting...", "ROLLOVER"} <= templates
    # the stored message stays the header remainder
    blank = ls.entries(logger="org.acme.startup.StartupLogger")["message"].to_list()
    assert blank and all(not m.strip() for m in blank)


def test_regroup_of_a_current_set_keeps_its_entries(tmp_path: Path, logs: list[Path]) -> None:
    store = Store(tmp_path / "store")
    ls = ingest_paths(store, logs, name="fx").logset
    entries, groups = _tables(ls)
    framed = store.regroup("fx", SignatureSpec(frames=2))
    assert framed.manifest.entry_count == ls.manifest.entry_count
    back = store.regroup("fx", SignatureSpec())
    assert _tables(back)[0].equals(entries) and _tables(back)[1].equals(groups)
    assert _manifest(back) == _manifest(ls)


def test_the_cap_matches_the_parser(tmp_path: Path, logs: list[Path]) -> None:
    run = logs[-1]
    old = ingest_paths(Store(tmp_path / "old"), [run], fmt=JiraV1()).logset
    out = tmp_path / "entries.parquet"
    recipe = Recipe(JiraFormat(), SignatureSpec())
    count = resign_entries(old.entries_path, out, recipe, "jira", reassemble=True, max_banners=2)
    parsed = list(attach_banners(parse_file(run, JiraV1()), 5, max_banners=2))
    assert count == len(parsed) == 4
    stored = pq.read_table(out).to_pydict()
    assert stored["id"] == [e.id for e in parsed]
    pre = [dict(c).get(CTX_PREAMBLE) for c in stored["context"]]
    assert pre == [e.context.get(CTX_PREAMBLE) for e in parsed]


class JiraV2(JiraFormat):
    """Jira as format version 2 recorded it (the parse is patched in by the test)."""

    version = 2


SUPPRESSED = "\n".join(
    [
        "java.lang.IllegalStateException: top",
        "\tat org.acme.A.run(A.java:1)",
        "\tSuppressed: java.io.IOException: close",
        "\t\tat org.acme.B.close(B.java:2)",
        "\tCaused by: java.net.SocketException: reset",
        "\t\tat org.acme.C.read(C.java:3)",
    ]
)


@pytest.fixture
def v3_log(tmp_path: Path) -> Path:
    """Entries jira@2 built differently: a two-word user's request, a suppressed cause."""
    head = "2026-10-29 11:00:0{}+0100"
    http = "http-nio-8080-exec-4 url: /rest/api/2/issue/ABC-1"
    lines = [
        f"{head.format('0,000')} {http} WARN Automation Robot 660x10x1 1zz9 10.0.0.9 "
        "/rest/api/2/issue/ABC-1 [org.acme.Rest] Request rejected",
        f"{head.format('1,000')} exec-2 ERROR Anonymous user [org.acme.Job] Job ran",
        _line("02,000", "exec-3", "org.acme.Close", STARS),
        f"{head.format('2,002')} exec-3 ERROR      [org.acme.Close] Close failed\n{SUPPRESSED}",
        f"{head.format('3,000')} exec-5 ERROR jdoe 661x11x1 2yy8 10.0.0.8 /x [org.acme.Rest] ok",
    ]
    path = tmp_path / "v3.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _v2_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse as jira@2 did: one-word users only, a suppressed cause in the main chain."""
    import re

    from atlas_log_analyzer.core.formats import jira
    from atlas_log_analyzer.core.models import ExceptionInfo

    current = jira.parse_exceptions

    def old_chain(lines: Sequence[str]) -> tuple[ExceptionInfo, ...]:
        chain = current(lines)
        if any("Suppressed: " in line for line in lines):
            chain = (*chain, ExceptionInfo("java.net.SocketException", "reset"))
        return chain

    old_ctx = r"(?P<user>\S+) (?P<reqid>\d+x\d+x\d+|-) (?P<session>\S+) (?P<ip>\S+) (?P<url>\S.*)"
    monkeypatch.setattr(jira, "_REQUEST_CTX_RE", re.compile(old_ctx))
    monkeypatch.setattr(jira, "parse_exceptions", old_chain)


def test_upgrade_from_v2_rebuilds_entries_from_raw(
    tmp_path: Path, v3_log: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    new = ingest_paths(Store(tmp_path / "new"), [v3_log], name="fx").logset
    old_store = Store(tmp_path / "old")
    with monkeypatch.context() as patch:
        _v2_parse(patch)
        old = ingest_paths(old_store, [v3_log], name="fx", fmt=JiraV2()).logset
    stale = pl.read_parquet(old.entries_path)
    assert stale["user"][0].startswith("Automation Robot 660x10x1")
    assert stale["exception_chain"].list.len().to_list()[2] == 2
    v3_log.unlink()  # upgrade reads the stored entries only

    assert old_store.upgrade("fx").upgraded
    up = old_store.open("fx")
    entries, groups = _tables(up)
    want_entries, want_groups = _tables(new)
    assert entries.equals(want_entries)
    assert groups.equals(want_groups)
    assert _manifest(up) == _manifest(new)
    first = entries.row(0, named=True)
    assert (first["user"], first["request_id"], first["ip"]) == (
        "Automation Robot",
        "660x10x1",
        "10.0.0.9",
    )
    pre = dict(entries.row(2, named=True)["context"])
    assert pre[CTX_PREAMBLE].endswith(STARS)  # the banner stays attached
