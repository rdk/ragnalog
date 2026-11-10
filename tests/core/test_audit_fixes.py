"""Header, masking, exception-chain, signature and rotation fixes (jira@4, mask3, sig2)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from atlas_log_analyzer.core.formats.jira import HEADER_RE, JIRA_RULES, match_header
from atlas_log_analyzer.core.java import parse_exceptions
from atlas_log_analyzer.core.masking import GENERIC_RULES, Masker
from atlas_log_analyzer.core.models import LogEntry, LogLevel, SourceRef
from atlas_log_analyzer.core.reader import discover_sources
from atlas_log_analyzer.core.signature import Signer

TAB = "\t"
BS = "\\"
MASKER = Masker((*JIRA_RULES, *GENERIC_RULES))


@pytest.mark.parametrize(
    ("line", "ctx", "logger", "msg"),
    [
        (
            "2026-10-29 02:19:41,415+0100 exec-1 url: /a/[b]/c INFO anonymous 1x2x3 abc"
            " 1.2.3.4 /a/[b]/c [c.d.E] hello",
            "anonymous 1x2x3 abc 1.2.3.4 /a/[b]/c",
            "c.d.E",
            "hello",
        ),
        (
            "2026-10-29 02:19:41,415+0100 main WARN u 1x2x3 s 1.2.3.4 /x [a b] [c.d.E] msg",
            "u 1x2x3 s 1.2.3.4 /x [a b]",
            "c.d.E",
            "msg",
        ),
        (
            "2026-10-29 02:19:41,415+0100 main INFO [c.d.E] hi [x.y] there",
            None,
            "c.d.E",
            "hi [x.y] there",
        ),
    ],
)
def test_logger_is_the_first_bracketed_token_after_the_level(
    line: str, ctx: str | None, logger: str, msg: str
) -> None:
    groups = match_header(line)
    m = HEADER_RE.match(line)
    assert m is not None and groups == m.groups()
    assert groups is not None and (groups[4], groups[5], groups[6]) == (ctx, logger, msg)


@pytest.mark.parametrize(
    ("text", "template"),
    [
        ("connect fe80::1ff:fe23:4567:890a failed", "connect <IP> failed"),
        ("bind [::1]:8080", "bind [<IP>]:<NUM>"),
        ("full 2001:db8:0:0:0:0:2:1 ok", "full <IP> ok"),
        ("at 10:22:33 ran", "at <NUM>:<NUM>:<NUM> ran"),
        ("List::add and Dead::beef", "List::add and Dead::beef"),
        ("mail Bob.O'Neil@ex.co.uk", "mail <EMAIL>"),
        ("'quoted@x.org' and root@localhost", "'<EMAIL>' and <EMAIL>"),
        ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc_DEF-1 end", "token <TOKEN> end"),
        (f"file C:{BS}Users{BS}jdoe{BS}x.log", f"file C:{BS}Users{BS}<USER>{BS}x.log"),
        ("/home/jdoe/a and /Users/jdoe/b", "/home/<USER>/a and /Users/<USER>/b"),
        ("plugin 9.4.12.1 from 10.0.0.1", "plugin <IP> from <IP>"),
        ("not 1.2.300.4", "not <NUM>.<NUM>"),
    ],
)
def test_masking(text: str, template: str) -> None:
    assert MASKER.apply(text)[0] == template


def test_chain_after_the_jvm_prefix_and_across_blank_lines() -> None:
    uncaught = [
        'Exception in thread "main" java.lang.IllegalStateException: hi',
        f"{TAB}at a.B.c(B.java:1)",
    ]
    assert [(e.type, e.message) for e in parse_exceptions(uncaught)] == [
        ("java.lang.IllegalStateException", "hi")
    ]
    gap = [
        "x.YException: top",
        f"{TAB}at a.B.c(B.java:1)",
        "",
        "Caused by: x.ZError: below",
        f"{TAB}at a.B.d(B.java:2)",
        "",
        "after",
    ]
    assert [e.type for e in parse_exceptions(gap)] == ["x.YException", "x.ZError"]
    ends = [
        "x.YException: top",
        f"{TAB}at a.B.c(B.java:1)",
        "",
        "other text",
        "Caused by: x.ZError: no",
    ]
    assert [e.type for e in parse_exceptions(ends)] == ["x.YException"]


def _entry(message: str) -> LogEntry:
    return LogEntry(
        id="1",
        format="jira",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        level=LogLevel.INFO,
        thread="t",
        logger="a.B",
        message=message,
        body="",
        exceptions=(),
        source=SourceRef("x", 1, 0),
        raw=message,
    )


def test_trailing_space_and_separators_in_signatures() -> None:
    signer = Signer(MASKER)
    assert signer.sign(_entry("done")).hash == signer.sign(_entry("done  ")).hash
    # a template holding the separator cannot run into the next part
    a = signer.sign(_entry("x\x1fy"))
    b = signer.sign(_entry("x"))
    assert a.hash != b.hash


def test_date_stamped_rotations_sort_oldest_first(tmp_path: Path) -> None:
    for i, name in enumerate(["app.log.20261102", "app.log", "app.log.20261101"]):
        (tmp_path / name).write_text("x\n", encoding="utf-8")
        os.utime(tmp_path / name, (1_700_000_000 + i, 1_700_000_000 + i))
    names = [s.path.name for s in discover_sources([tmp_path])]
    assert names == ["app.log.20261101", "app.log.20261102", "app.log"]
    for name in ("app.log.2", "app.log.1"):
        (tmp_path / name).write_text("x\n", encoding="utf-8")
    counters = [
        s.path.name for s in discover_sources([tmp_path / "app.log.1", tmp_path / "app.log.2"])
    ]
    assert counters == ["app.log.2", "app.log.1"]
