from __future__ import annotations

from datetime import datetime

import pytest

from atlas_log_analyzer.core.formats.jira import JiraFormat
from atlas_log_analyzer.core.grouping import Grouper
from atlas_log_analyzer.core.masking import Masker
from atlas_log_analyzer.core.models import LogEntry, LogLevel, SourceRef
from atlas_log_analyzer.core.parser import parse_file
from atlas_log_analyzer.core.signature import Signer

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
