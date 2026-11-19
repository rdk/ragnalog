"""Entry assembly of decorated logs: banners attached to the next entry, titles from the body."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ragnalog.core.decoration import is_banner_text, is_decoration, lead_line
from ragnalog.core.formats.base import CTX_PREAMBLE, banner_window_ms, titles_from_body
from ragnalog.core.formats.jira import JiraFormat
from ragnalog.core.masking import GENERIC_RULES, Masker
from ragnalog.core.models import LogEntry, LogLevel
from ragnalog.core.parser import attach_banners, is_banner, parse, parse_file
from ragnalog.core.pipeline import Recipe
from ragnalog.core.signature import DEFAULT_SPEC, Signer

from .conftest import FIXTURES

BANNERS = FIXTURES / "banners.log"
STARS = "*" * 50
AT = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)


class PlainJira(JiraFormat):
    """Jira without the opt-ins (format version 1)."""

    version = 1
    banner_window_ms = 0
    titles_from_body = False


def _write(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "atlassian-jira.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _line(ms: str, thread: str, logger: str, message: str, level: str = "ERROR") -> str:
    return f"2026-10-29 10:00:{ms}+0100 {thread} {level}      [{logger}] {message}"


def _by_message(entries: list[LogEntry]) -> dict[str, LogEntry]:
    return {e.message: e for e in entries if not is_banner(e)}


# --- decoration


@pytest.mark.parametrize(
    "text", ["***", "  ----  ", "=====", "_____", "# # #", "═══", "*" * 85, "<>"]
)
def test_decoration_lines(text: str) -> None:
    assert is_decoration(text)


@pytest.mark.parametrize("text", ["", "   ", "Acme starting...", "___ Environment ___", "*1*", "é"])
def test_lines_with_text_are_no_decoration(text: str) -> None:
    assert not is_decoration(text)


def test_banner_text_needs_ten_characters() -> None:
    assert is_banner_text("*" * 10) and is_banner_text("  " + "-" * 40 + "  ")
    assert not is_banner_text("*" * 9)
    assert not is_banner_text("** note **")


def test_lead_line_skips_blank_and_decoration_lines() -> None:
    body = "\n    \n    ****************\n    Acme starting...\n    ****************\n    "
    assert lead_line(body) == "Acme starting..."
    assert lead_line("    ___ Environment ___\n    x") == "___ Environment ___"
    assert lead_line("\n  ****\n  ]\n") == ""
    assert lead_line("") == ""
    assert lead_line("first\nsecond") == "first"


def test_jira_opts_in_and_formats_without_the_attributes_do_not() -> None:
    assert banner_window_ms(JiraFormat()) == 5 and titles_from_body(JiraFormat())
    assert banner_window_ms(object()) == 0 and not titles_from_body(object())
    assert banner_window_ms(PlainJira()) == 0 and not titles_from_body(PlainJira())


# --- banners


def test_fixture_banners() -> None:
    plain = list(parse_file(BANNERS, PlainJira()))
    entries = list(parse_file(BANNERS, JiraFormat()))
    attached = {e.message: e.context[CTX_PREAMBLE] for e in entries if CTX_PREAMBLE in e.context}
    assert attached == {
        # same thread and logger, 3 ms later
        "Script field failed on issue: ABC-101, field: Due On": plain[0].raw,
        # two banners in a row, joined in order
        "Dropping a field of ABC-106": f"{plain[10].raw}\n{plain[11].raw}",
        # same millisecond
        "Script field failed on issue: ABC-107, field: Due On": plain[13].raw,
    }
    # left as entries: another thread, 6 ms later, another logger, at the end of the file
    left = [e for e in entries if is_banner(e)]
    assert [e.source.line for e in left] == [5, 7, 9, 37]
    assert len(entries) == len(plain) - 4
    # a decoration message with a body is no banner
    framed = next(e for e in entries if e.message.startswith("====="))
    assert not is_banner(framed) and framed.body == "    details for bob"


def test_attached_entries_keep_id_raw_header_and_source() -> None:
    plain = _by_message(list(parse_file(BANNERS, PlainJira())))
    for e in parse_file(BANNERS, JiraFormat()):
        if CTX_PREAMBLE not in e.context:
            continue
        before = plain[e.message]
        assert (e.id, e.raw, e.source, e.timestamp, e.thread, e.logger, e.body) == (
            before.id,
            before.raw,
            before.source,
            before.timestamp,
            before.thread,
            before.logger,
            before.body,
        )
        assert {k: v for k, v in e.context.items() if k != CTX_PREAMBLE} == dict(before.context)


def test_ids_of_every_entry_are_kept() -> None:
    kept = {e.id for e in parse_file(BANNERS, JiraFormat())}
    assert kept <= {e.id for e in parse_file(BANNERS, PlainJira())}


@pytest.mark.parametrize(
    ("thread", "logger", "ms", "attached"),
    [
        ("exec-1", "org.acme.A", "00,005", True),  # 5 ms: still within the window
        ("exec-1", "org.acme.A", "00,006", False),  # 6 ms
        ("exec-2", "org.acme.A", "00,001", False),  # another thread
        ("exec-1", "org.acme.B", "00,001", False),  # another logger
    ],
)
def test_banner_window_thread_and_logger(
    tmp_path: Path, thread: str, logger: str, ms: str, attached: bool
) -> None:
    path = _write(
        tmp_path,
        _line("00,000", "exec-1", "org.acme.A", STARS),
        _line(ms, thread, logger, "Field failed for alice"),
    )
    entries = list(parse_file(path, JiraFormat()))
    assert len(entries) == (1 if attached else 2)
    assert (CTX_PREAMBLE in entries[-1].context) is attached


def test_banner_before_an_earlier_entry_stays(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _line("00,002", "exec-1", "org.acme.A", STARS),
        _line("00,001", "exec-1", "org.acme.A", "Field failed for alice"),
    )
    assert len(list(parse_file(path, JiraFormat()))) == 2


def test_banner_with_a_body_or_a_parse_error_stays(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _line("00,000", "exec-1", "org.acme.A", STARS),
        "  more text for bob",
        _line("00,001", "exec-1", "org.acme.A", "Field failed for bob"),
    )
    entries = list(parse_file(path, JiraFormat()))
    assert len(entries) == 2 and not any(CTX_PREAMBLE in e.context for e in entries)
    orphan = _write(tmp_path, STARS, _line("00,001", "exec-1", "org.acme.A", "Field failed"))
    entries = list(parse_file(orphan, JiraFormat()))
    assert entries[0].parse_error is not None and len(entries) == 2


def test_runs_of_banners(tmp_path: Path) -> None:
    banners = [_line(f"00,00{i}", "exec-1", "org.acme.A", "=" * (20 + i)) for i in range(3)]
    target = _line("00,004", "exec-1", "org.acme.A", "Field failed for carol")
    path = _write(tmp_path, *banners, target)
    entries = list(parse_file(path, JiraFormat()))
    assert len(entries) == 1 and entries[0].context[CTX_PREAMBLE] == "\n".join(banners)
    # at most max_banners per entry: the earlier ones stay entries
    plain = list(parse_file(path, PlainJira()))
    capped = list(attach_banners(plain, 5, max_banners=2))
    assert [e.message for e in capped] == ["=" * 20, "Field failed for carol"]
    assert capped[1].context[CTX_PREAMBLE] == "\n".join(banners[1:])
    # a run that ends on a banner (no entry within the window) stays as it is
    path = _write(tmp_path, *banners, _line("00,009", "exec-1", "org.acme.A", "late"))
    assert len(list(parse_file(path, JiraFormat()))) == 4


def test_banners_attach_within_a_file_only(tmp_path: Path) -> None:
    first = tmp_path / "atlassian-jira.log.1"
    first.write_text(_line("00,000", "exec-1", "org.acme.A", STARS) + "\n", encoding="utf-8")
    second = tmp_path / "atlassian-jira.log"
    second.write_text(_line("00,001", "exec-1", "org.acme.A", "Field failed") + "\n")
    entries = list(parse([tmp_path], JiraFormat()))
    assert len(entries) == 2 and not any(CTX_PREAMBLE in e.context for e in entries)


def test_attach_is_a_no_op_without_a_window() -> None:
    plain = list(parse_file(BANNERS, PlainJira()))
    assert list(attach_banners(plain, 0)) == plain


# --- titles from the body


def _signer(titles: bool) -> Signer:
    return Signer(Masker(GENERIC_RULES), DEFAULT_SPEC, titles_from_body=titles)


def test_blank_messages_take_their_title_from_the_body() -> None:
    signer = Recipe(JiraFormat(), DEFAULT_SPEC).signer()
    assert signer.titles_from_body
    templates = {}
    for e in parse_file(BANNERS, JiraFormat()):
        if e.logger.startswith("org.acme.st"):
            assert not e.message.strip()  # the stored message stays the header remainder
            templates[(e.logger, e.source.line)] = signer.sign(e).template
    assert templates == {
        ("org.acme.startup.StartupLogger", 19): "Acme starting...",
        ("org.acme.startup.StartupLogger", 25): "___ Environment _____________________________",
        (
            "org.acme.stats.TimeSlice",
            30,
        ): "From timeSlice=MONTH, direction=PAST and sliceCount=<NUM> I got these boundaries: [",
        ("org.acme.startup.StartupLogger", 34): "",  # decoration only: stays blank
    }


def test_titles_from_the_body_split_groups_and_mask_variables() -> None:
    def entry(body: str, message: str = "") -> LogEntry:
        return LogEntry("x", "jira", AT, LogLevel.INFO, "main", "org.acme.S", message, body)

    on, off = _signer(True), _signer(False)
    a, b = entry("\n  Acme 1.2 starting\n"), entry("\n  ___ Plugins ___\n")
    assert on.sign(a).template == "Acme <NUM> starting"
    assert on.sign(a).variables == ("1.2",)
    assert on.sign(a).hash != on.sign(b).hash
    assert off.sign(a).hash == off.sign(b).hash and off.sign(a).template == ""
    # a message that is not blank is the title, whatever the body says
    c = entry("\n  Acme starting\n", message="Started")
    assert on.sign(c) == off.sign(c)
