from __future__ import annotations

import itertools
from datetime import datetime

import pytest

from ragnalog.core.formats.jira import JiraFormat
from ragnalog.core.grouping import Grouper
from ragnalog.core.masking import Masker
from ragnalog.core.models import LogEntry, LogLevel, SourceRef
from ragnalog.core.parser import parse_file
from ragnalog.core.signature import Signer

from .conftest import SAMPLE


def _entry(jira: JiraFormat, line: str, path: str = "a.log") -> LogEntry:
    return jira.build(jira.parse_header(line), line, [], SourceRef(path, 1, 0))


def test_group_stats(jira: JiraFormat, masker: Masker) -> None:
    ts = "2026-10-29 0{}:00,000+0100"
    lines = [
        f"{ts.format('1:00')} IssueIndexer:thread-1 ERROR      [x.Y] failed on issue ABC-1",
        f"{ts.format('3:00')} IssueIndexer:thread-2 ERROR jdoe     [x.Y] failed on issue ABC-2",
        f"{ts.format('2:00')} IssueIndexer:thread-1 ERROR jdoe     [x.Y] failed on issue ZZ-3",
        f"{ts.format('2:30')} IssueIndexer:thread-1 WARN alice     [x.Y] failed on issue ZZ-3",
    ]
    entries = [_entry(jira, line, "b.log" if i == 3 else "a.log") for i, line in enumerate(lines)]
    grouper = Grouper(Signer(masker), max_examples=2)
    signed = list(grouper.sign(entries))
    assert [s.entry for s in signed] == entries
    assert len(grouper.groups) == 2
    top = grouper.sorted_groups()[0]
    assert top.count == 3
    assert top.level is LogLevel.ERROR
    assert top.logger == "x.Y"
    assert top.template == "failed on issue <ISSUE>"
    assert top.exception_type is None
    assert top.first_seen == entries[0].timestamp
    assert top.last_seen == entries[1].timestamp
    assert top.users == {"jdoe": 2}
    assert top.threads == {"IssueIndexer:thread-1": 2, "IssueIndexer:thread-2": 1}
    assert top.sources == {"a.log": 3}
    assert top.examples == [entries[0].id, entries[2].id]  # first ones plus the latest
    assert top.coarse_signature == signed[0].coarse.hash

    by_first = grouper.sorted_groups("first_seen")
    assert by_first[0].signature == top.signature
    by_last = grouper.sorted_groups("last_seen")
    assert by_last[0].last_seen == max(g.last_seen for g in grouper.groups.values() if g.last_seen)
    with pytest.raises(ValueError, match="unknown sort key"):
        grouper.sorted_groups("nope")


def test_group_sample(jira: JiraFormat, masker: Masker) -> None:
    grouper = Grouper(Signer(masker))
    count = sum(1 for _ in grouper.sign(parse_file(SAMPLE, jira)))
    assert sum(g.count for g in grouper.groups.values()) == count
    groovy = [g for g in grouper.groups.values() if g.template.startswith("Script field failed")]
    assert len(groovy) == 1
    assert groovy[0].count == 3
    assert groovy[0].exception_type == (
        "org.codehaus.groovy.control.MultipleCompilationErrorsException"
    )
    assert all(isinstance(g.first_seen, datetime) for g in grouper.groups.values())


def _state(grouper: Grouper) -> dict[str, tuple[object, ...]]:
    """Everything a grouper writes, counters in insertion order (``most_common`` ties)."""
    return {
        key: (
            g.coarse_signature,
            g.template,
            g.level,
            g.logger,
            g.exception_type,
            g.count,
            g.first_seen,
            g.last_seen,
            list(g.users.items()),
            list(g.threads.items()),
            list(g.sources.items()),
            g.users.most_common(10),
            g.examples,
        )
        for key, g in grouper.groups.items()
    }


@pytest.mark.parametrize("max_examples", [1, 2, 3, 5])
def test_merge_per_file_groupers_equals_one_run(
    jira: JiraFormat, masker: Masker, max_examples: int
) -> None:
    from .conftest import EDGE_CASES

    entries = [*parse_file(SAMPLE, jira), *parse_file(EDGE_CASES, jira)]
    whole = Grouper(Signer(masker), max_examples=max_examples)
    for _ in whole.sign(entries):
        pass
    # files, and also arbitrary cuts of the stream (a group spread over several parts)
    n = len(entries)
    for cuts in ([len(list(parse_file(SAMPLE, jira)))], [1, 2, n // 3, n // 2, n - 1]):
        bounds = [0, *cuts, n]
        merged = Grouper(Signer(masker), max_examples=max_examples)
        for lo, hi in itertools.pairwise(bounds):
            part = Grouper(Signer(masker), max_examples=max_examples)
            for _ in part.sign(entries[lo:hi]):
                pass
            merged.merge(part)
        assert _state(merged) == _state(whole)
        assert [g.signature for g in merged.sorted_groups()] == [
            g.signature for g in whole.sorted_groups()
        ]


def test_merge_copies_and_checks(jira: JiraFormat, masker: Masker) -> None:
    entries = list(parse_file(SAMPLE, jira))
    a, b = Grouper(Signer(masker)), Grouper(Signer(masker))
    for _ in b.sign(entries):
        pass
    before = _state(b)
    a.merge(b)
    a.merge(b)  # the second merge must not touch b's groups through shared objects
    assert _state(b) == before
    assert all(a.groups[k].count == 2 * g.count for k, g in b.groups.items())
    with pytest.raises(ValueError, match="max_examples"):
        a.merge(Grouper(Signer(masker), max_examples=2))
