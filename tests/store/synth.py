"""Synthetic entries, signatures and groups for store tests (no dependency on core logic)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType

import xxhash

from atlas_log_analyzer.core.formats.jira import JiraFormat
from atlas_log_analyzer.core.grouping import Group, SignedEntry
from atlas_log_analyzer.core.masking import MaskRule
from atlas_log_analyzer.core.models import ExceptionInfo, Frame, LogEntry, LogLevel, SourceRef
from atlas_log_analyzer.core.reader import SourceFile
from atlas_log_analyzer.core.signature import Signature, SignatureSpec

TZ = timezone(timedelta(hours=2))
BASE = datetime(2026, 10, 1, 8, 0, 0, tzinfo=TZ)


def h(*parts: object) -> str:
    return xxhash.xxh3_64_hexdigest("\x1f".join(map(str, parts)).encode())


@dataclass(frozen=True)
class Kind:
    level: LogLevel
    logger: str
    template: str  # one "{}" for the variable
    placeholder: str
    exceptions: tuple[ExceptionInfo, ...] = ()


CHAIN = (
    ExceptionInfo(
        "java.lang.IllegalStateException",
        "boom",
        (
            Frame("com.example.Foo.bar", "Foo.java", 42, None),
            Frame("java.lang.Thread.run", None, None, "java.base"),
        ),
        7,
    ),
    ExceptionInfo("java.sql.SQLException", "connection refused", (), 0),
)

KINDS: dict[str, Kind] = {
    "script": Kind(
        LogLevel.ERROR,
        "com.onresolve.scriptrunner.Script",
        "Script field failed on issue: {}, field: Finished On",
        "<ISSUE>",
        CHAIN,
    ),
    "slow": Kind(LogLevel.WARN, "com.atlassian.jira.Slow", "Slow query took {} ms", "<NUM>"),
    "login": Kind(LogLevel.INFO, "com.atlassian.jira.login.Login", "User {} logged in", "<USER>"),
    "index": Kind(LogLevel.INFO, "com.atlassian.jira.index.Indexer", "Indexed {} issues", "<NUM>"),
    "npe": Kind(
        LogLevel.ERROR,
        "com.atlassian.jira.web.Servlet",
        "Request failed for {}",
        "<PATH>",
        (ExceptionInfo("java.lang.NullPointerException", None, (), 0),),
    ),
    # Same coarse signature as "npe" but a different exception -> different primary signature.
    "npe2": Kind(
        LogLevel.ERROR,
        "com.atlassian.jira.web.Servlet",
        "Request failed for {}",
        "<PATH>",
        (ExceptionInfo("java.io.IOException", "Broken pipe", (), 0),),
    ),
    "fatal": Kind(LogLevel.FATAL, "com.atlassian.jira.startup", "Startup failed: {}", "<*>"),
    "debug": Kind(LogLevel.DEBUG, "com.atlassian.cache", "Cache {} evicted", "<*>"),
}


def signatures(kind: Kind, value: str) -> tuple[Signature, Signature]:
    template = kind.template.format(kind.placeholder)
    exc = "|".join(f"{e.type}:{e.message}" for e in kind.exceptions)
    coarse_parts = ("jira", kind.level.name, kind.logger, template)
    parts = (*coarse_parts, exc)
    return (
        Signature(h(*parts), template, parts, (value,)),
        Signature(h(*coarse_parts), template, coarse_parts, (value,)),
    )


def value_for(i: int, kind_name: str) -> str:
    return f"ABC-{i}" if KINDS[kind_name].placeholder == "<ISSUE>" else str(i)


def make_entry(i: int, kind_name: str, source: str, line: int, *, minutes: float = 0.0) -> LogEntry:
    kind = KINDS[kind_name]
    value = value_for(i, kind_name)
    message = kind.template.format(value)
    ts = BASE + timedelta(milliseconds=1500 * i + int(minutes * 60_000))
    thread = f"http-nio-8080-exec-{i % 7}"
    ctx: dict[str, str] = {}
    if i % 3:
        ctx = {"user": f"user{i % 5}", "request_id": f"{i}x1", "session": "s1", "ip": "10.0.0.1"}
        if i % 4 == 0:
            ctx["url"] = "/rest/api/2/issue"
            ctx["custom"] = f"c{i % 2}"
    body = ""
    if kind.exceptions:
        body = "\n".join(f"{e.type}: {e.message}" for e in kind.exceptions)
    raw = f"{ts.isoformat()} {thread} {kind.level.name} [{kind.logger}] {message}"
    if body:
        raw += "\n" + body
    return LogEntry(
        id=h("jira", ts.isoformat(), thread, kind.logger, raw),
        format="jira",
        timestamp=ts,
        level=kind.level,
        thread=thread,
        logger=kind.logger,
        message=message,
        body=body,
        exceptions=kind.exceptions,
        context=MappingProxyType(ctx) if ctx else MappingProxyType({}),
        source=SourceRef(source, line, line * 100),
        raw=raw,
        parse_error=None,
    )


def sign(entry: LogEntry, kind_name: str, value: str) -> SignedEntry:
    sig, coarse = signatures(KINDS[kind_name], value)
    return SignedEntry(entry, sig, coarse)


def parse_error_entry(i: int, source: str, line: int) -> SignedEntry:
    ts = BASE + timedelta(milliseconds=1500 * i)
    raw = f"garbage line {i}"
    e = LogEntry(
        id=h("jira-bad", i, raw),
        format="jira",
        timestamp=ts,
        level=LogLevel.UNKNOWN,
        thread="",
        logger="",
        message=raw,
        source=SourceRef(source, line, line * 100),
        raw=raw,
        parse_error="unrecognised header",
    )
    s = Signature(h("bad"), "garbage line <NUM>", ("bad",), (str(i),))
    return SignedEntry(e, s, s)


@dataclass
class MiniGrouper:
    """Reference aggregation; ignores entries whose id was already seen (like the writer)."""

    max_examples: int = 3
    dedup: bool = True
    groups: dict[str, Group] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)

    def track(self, signed: Iterable[SignedEntry]) -> Iterator[SignedEntry]:
        for se in signed:
            self.add(se)
            yield se

    def add(self, se: SignedEntry) -> None:
        e = se.entry
        if self.dedup and e.id in self.seen:
            return
        self.seen.add(e.id)
        g = self.groups.get(se.signature.hash)
        if g is None:
            g = self.groups[se.signature.hash] = Group(
                signature=se.signature.hash,
                coarse_signature=se.coarse.hash,
                template=se.signature.template,
                level=e.level,
                logger=e.logger,
                exception_type=e.exception_type,
            )
        g.count += 1
        if g.first_seen is None or e.timestamp < g.first_seen:
            g.first_seen = e.timestamp
        if g.last_seen is None or e.timestamp > g.last_seen:
            g.last_seen = e.timestamp
        if user := e.context.get("user"):
            g.users[user] += 1
        if e.thread:  # as Grouper: an empty thread is not counted
            g.threads[e.thread] += 1
        g.sources[e.source.path] += 1
        if len(g.examples) < self.max_examples:
            g.examples.append(e.id)


def source_files(names: Sequence[str], root: str = "/logs") -> list[SourceFile]:
    return [
        SourceFile(Path(root) / n, 1000 + k, h("file", root, n), k) for k, n in enumerate(names)
    ]


def generate(
    sources: Sequence[SourceFile],
    weights: dict[str, int],
    n_per_source: int,
    *,
    duplicates: int = 0,
    parse_errors: int = 0,
    offset: int = 0,
) -> Iterator[SignedEntry]:
    """Yield entries round-robin over ``weights`` (kind -> repetitions) for each source.

    The first ``duplicates`` entries of the first source are re-emitted under the
    second source (same id, different provenance) to exercise deduplication.
    """
    cycle = [k for k, w in weights.items() for _ in range(w)]
    i = offset
    first: list[SignedEntry] = []
    for s_idx, sf in enumerate(sources):
        path = str(sf.path)
        for line in range(1, n_per_source + 1):
            kind = cycle[i % len(cycle)]
            se = sign(make_entry(i, kind, path, line), kind, value_for(i, kind))
            if s_idx == 0 and len(first) < duplicates:
                first.append(se)
            yield se
            i += 1
        if s_idx == 1:
            for se in first:
                e = se.entry
                moved = LogEntry(
                    id=e.id,
                    format=e.format,
                    timestamp=e.timestamp,
                    level=e.level,
                    thread=e.thread,
                    logger=e.logger,
                    message=e.message,
                    body=e.body,
                    exceptions=e.exceptions,
                    context=e.context,
                    source=SourceRef(path, 9999, 0),
                    raw=e.raw,
                )
                yield SignedEntry(moved, se.signature, se.coarse)
    for k in range(parse_errors):
        yield parse_error_entry(i + k, str(sources[-1].path), 5000 + k)


class FakeFormat:
    name = "jira"
    version = JiraFormat.version  # sets of the current recipe, not stale ones
    description = "fake"
    mask_rules: Sequence[MaskRule] = ()
    signature_spec = SignatureSpec()


WEIGHTS_A = {"script": 3, "slow": 2, "login": 5, "index": 2, "npe": 2, "npe2": 1, "debug": 1}
# B: "debug" vanishes, "fatal" is new, "script" grows, "slow" shrinks, others steady-ish.
WEIGHTS_B = {"script": 9, "slow": 1, "login": 5, "index": 2, "npe": 2, "npe2": 1, "fatal": 1}
