"""Jira Data Center ``atlassian-jira.log`` format.

Header layout::

    <ts> <thread>[ url: <urls>] <LEVEL> [<context>] [<logger>] <message>

``<context>`` is one of

* empty or ``-`` (no user),
* ``<user>`` (may contain one space: ``Anonymous user``, ``Automation Robot``),
* ``<user> <reqid> <session> <ip>[,<ip>…] <url>`` on HTTP threads,
* ``<user>  <service name>`` (two or more spaces) on scheduled services, e.g.
  ``anonymous    Mail Queue Service``; the service lands in ``context["service"]``.
  ``ServiceRunner`` in the user slot is Jira's scheduler, not a user: it is
  recorded as the service (``ServiceRunner`` alone, or the service name that
  follows it) and no user is set.

The ``url:`` segment may carry ``; user: <name>``, which is dropped from the URL
(the user is also present in ``<context>``).
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType

import xxhash

from atlas_log_analyzer.core.formats.base import (
    CTX_IP,
    CTX_REQUEST_ID,
    CTX_REQUEST_URL,
    CTX_SESSION,
    CTX_URL,
    CTX_USER,
    HeaderMatch,
)
from atlas_log_analyzer.core.java import parse_exceptions
from atlas_log_analyzer.core.masking import MaskRule
from atlas_log_analyzer.core.models import EMPTY_CONTEXT, LogEntry, LogLevel, SourceRef
from atlas_log_analyzer.core.signature import DEFAULT_SPEC, SignatureSpec

__all__ = [
    "CTX_SERVICE",
    "HEADER_RE",
    "JIRA_RULES",
    "UNKNOWN_TIMESTAMP",
    "JiraFormat",
    "parse_timestamp",
]

CTX_SERVICE = "service"
"""Context key for the scheduled-service name (``anonymous    Mail Queue Service``)."""

UNKNOWN_TIMESTAMP = datetime(1970, 1, 1, tzinfo=UTC)
"""Timestamp of ``parse_error`` entries whose line does not start with a timestamp."""

HEADER_RE = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}[+-]\d{4})"
    r" (?P<thread>.+?)"
    r"(?: url: (?P<url>.+?))?"
    r" (?P<level>TRACE|DEBUG|INFO|WARN|ERROR|FATAL)"
    r" +(?:(?P<ctx>[^\[\s][^\[]*?) +)?"
    r"\[(?P<logger>[^\]\s]+)\] ?(?P<msg>.*)"
)
"""Anchored header regex; ``thread``/``url``/``ctx`` are lazy, ``LEVEL`` and ``[logger]`` anchor."""

_REQUEST_CTX_RE = re.compile(
    r"(?P<user>\S+) (?P<reqid>\d+x\d+x\d+|-) (?P<session>\S+) (?P<ip>\S+) (?P<url>\S.*)"
)
_TS_PREFIX_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}[+-]\d{4}")
_SERVICE_SPLIT_RE = re.compile(r"\s{2,}")

_LEVELS = {name: LogLevel[name] for name in ("TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL")}
_NO_USER = frozenset({"-", ""})
_SERVICE_RUNNER = "ServiceRunner"
"""Jira's scheduler in the user slot; recorded as the service, not as a user."""

JIRA_RULES: tuple[MaskRule, ...] = (
    MaskRule.make(
        "issue",
        r"\b(?!(?:UTF|ISO|SHA|MD|HTTP|TLS|SSL|CP|AES|RSA|RFC|JSR|IEC|X|WINDOWS|LATIN)-)"
        r"[A-Z][A-Z0-9_]+-\d+\b",
    ),
    MaskRule.make("reqid", r"\b\d+x\d+x\d+\b"),
    MaskRule.make("customfield", r"\bcustomfield_\d+\b"),
    MaskRule.make("obj", r"(?<=@)[0-9a-f]{6,}\b"),
    MaskRule.make("duration", r"\bPT(?:\d+(?:\.\d+)?[HMS])+\b"),
    MaskRule.make("stack", r"(?<=thread/stack: )\S+?(?=\.?(?:\s|$))", "<THREAD>"),
)
"""Jira-specific masking rules; they run before :data:`GENERIC_RULES`.

Thread names are masked only in ``RequestCacheRecorderImpl`` messages
(``Invalid use of RequestCache by thread/stack: RMI-d6tcld``) whose suffix is a
random id. Elsewhere the generic ``num`` rule turns ``thread-8`` into
``thread-<NUM>``.
"""


def parse_timestamp(ts: str) -> datetime:
    """Parse ``2026-10-29 02:19:41,415+0100`` into a tz-aware datetime.

    Only the first 28 characters are used, so a whole header line may be passed.

    Raises:
        ValueError: When the text is not a valid timestamp.

    """
    return datetime.fromisoformat(ts[:28])


def _iso(ts: str) -> str:
    """``datetime.isoformat()`` of :func:`parse_timestamp` computed from the text (faster)."""
    ms = ts[20:23]
    frac = "" if ms == "000" else f".{ms}000"
    return f"{ts[:10]}T{ts[11:19]}{frac}{ts[23:26]}:{ts[26:28]}"


@dataclass(frozen=True, slots=True)
class _JiraHeader(HeaderMatch):
    iso: str = ""
    """``timestamp.isoformat()``, precomputed."""


def _parse_context(ctx: str | None, url: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if url is not None:
        base, sep, _user = url.partition("; user: ")
        out[CTX_URL] = base if sep else url
    if not ctx or ctx in _NO_USER:
        return out
    m = _REQUEST_CTX_RE.fullmatch(ctx)
    if m is not None:
        user = m.group("user")
        if user not in _NO_USER:
            out[CTX_USER] = user
        for key, group in (
            (CTX_REQUEST_ID, "reqid"),
            (CTX_SESSION, "session"),
            (CTX_IP, "ip"),
            (CTX_REQUEST_URL, "url"),
        ):
            value = m.group(group)
            if value != "-":
                out[key] = value
        return out
    parts = _SERVICE_SPLIT_RE.split(ctx, maxsplit=1)
    who = parts[0]
    service = parts[1] if len(parts) > 1 and parts[1] else None
    if who == _SERVICE_RUNNER:
        out[CTX_SERVICE] = service or who
        return out
    if who not in _NO_USER:
        out[CTX_USER] = who
    if service:
        out[CTX_SERVICE] = service
    return out


def _entry_id(fmt: str, iso: str, thread: str, logger: str, raw: str) -> str:
    text = f"{fmt}\x1f{iso}\x1f{thread}\x1f{logger}\x1f{raw}"
    return xxhash.xxh3_64_hexdigest(text.encode("utf-8", "surrogatepass"))


class JiraFormat:
    """Jira Data Center ``atlassian-jira.log`` (log4j layout with Jira MDC fields)."""

    name = "jira"
    description = "Jira Data Center atlassian-jira.log"
    mask_rules: Sequence[MaskRule] = JIRA_RULES
    signature_spec: SignatureSpec = DEFAULT_SPEC

    def __init__(self) -> None:
        # (line, match) of the last successful is_header call; one tuple so that
        # concurrent use of an instance can never pair a line with another match.
        self._last: tuple[str, re.Match[str]] | None = None

    def is_header(self, line: str) -> bool:
        """Full header match (with a cheap ``"20"`` pre-check)."""
        if line[:2] != "20":
            return False
        m = HEADER_RE.match(line)
        if m is None:
            return False
        self._last = (line, m)
        return True

    def parse_header(self, line: str) -> HeaderMatch | None:
        """Parse a header line into its fields; ``None`` when it does not match."""
        last = self._last
        if last is not None and last[0] is line:
            m: re.Match[str] | None = last[1]
        elif line[:2] == "20":
            m = HEADER_RE.match(line)
        else:
            return None
        if m is None:
            return None
        ts, thread, url, level, ctx, logger, msg = m.groups()
        try:
            timestamp = parse_timestamp(ts)
        except ValueError:
            return None
        context = _parse_context(ctx, url) if (ctx or url) else None
        return _JiraHeader(
            timestamp=timestamp,
            level=_LEVELS[level],
            thread=thread,
            logger=logger,
            message=msg,
            context=MappingProxyType(context) if context else EMPTY_CONTEXT,
            iso=_iso(ts),
        )

    def build(
        self,
        header: HeaderMatch | None,
        header_line: str,
        continuation: Sequence[str],
        source: SourceRef,
    ) -> LogEntry:
        """Assemble a :class:`LogEntry`; see :meth:`LogFormat.build`."""
        if continuation:
            body = "\n".join(continuation)
            raw = f"{header_line}\n{body}"
            exceptions = parse_exceptions(continuation)
        else:
            body = ""
            raw = header_line
            exceptions = ()
        if header is None:
            ts = UNKNOWN_TIMESTAMP
            if _TS_PREFIX_RE.match(header_line):
                with contextlib.suppress(ValueError):
                    ts = parse_timestamp(header_line)
            return LogEntry(
                id=_entry_id(self.name, ts.isoformat(), "", "", raw),
                format=self.name,
                timestamp=ts,
                level=LogLevel.UNKNOWN,
                thread="",
                logger="",
                message=header_line,
                body=body,
                exceptions=exceptions,
                source=source,
                raw=raw,
                parse_error="unrecognised header",
            )
        iso = header.iso if type(header) is _JiraHeader else header.timestamp.isoformat()
        return LogEntry(
            id=_entry_id(self.name, iso, header.thread, header.logger, raw),
            format=self.name,
            timestamp=header.timestamp,
            level=header.level,
            thread=header.thread,
            logger=header.logger,
            message=header.message,
            body=body,
            exceptions=exceptions,
            context=header.context,
            source=source,
            raw=raw,
        )

    def sniff(self, lines: Sequence[str]) -> float:
        """Fraction of non-empty ``lines`` that are Jira headers."""
        considered = [line for line in lines if line.strip()]
        if not considered:
            return 0.0
        return sum(1 for line in considered if self.is_header(line)) / len(considered)
