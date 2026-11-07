"""Multi-line entry assembly: lines → :class:`LogEntry` objects."""

from __future__ import annotations

import dataclasses
from collections import OrderedDict, deque
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta
from itertools import islice
from pathlib import Path
from types import MappingProxyType

import xxhash

from atlas_log_analyzer.core.decoration import is_banner_text
from atlas_log_analyzer.core.formats.base import CTX_PREAMBLE, LogFormat, banner_window_ms
from atlas_log_analyzer.core.models import LogEntry, SourceRef
from atlas_log_analyzer.core.reader import Line, SourceFile, discover_sources, iter_raw_lines

__all__ = [
    "MAX_BANNERS",
    "MAX_ENTRY_CHARS",
    "MAX_ENTRY_LINES",
    "ORPHAN_ERROR",
    "TRUNCATED_ERROR",
    "attach_banners",
    "is_banner",
    "parse",
    "parse_file",
    "parse_lines",
    "parse_sources",
]

ORPHAN_ERROR = "continuation lines before the first header"
"""``parse_error`` of the entry made of lines that precede the first header of a source."""
TRUNCATED_ERROR = "entry truncated: {lines} lines, {bytes} bytes"
"""``parse_error`` of an entry cut at :data:`MAX_ENTRY_CHARS` or :data:`MAX_ENTRY_LINES`;
``lines`` and ``bytes`` (UTF-8, lines joined with ``\\n``) describe the whole entry."""
MAX_ENTRY_CHARS = 4 << 20
"""Default cap of the text kept per entry: 4 Mi characters, header line included."""
MAX_ENTRY_LINES = 100_000
"""Default cap of the lines kept per entry, header line included."""
RECENT_TIMESTAMPS = 4096
"""Milliseconds whose issued ids are remembered to keep ids unique (see :func:`_distinct_ids`)."""
IDS_PER_MILLISECOND = 128
"""Ids remembered per millisecond (the 191 MB sample has at most 101 entries in one)."""

DETECT_LINES = 200
"""Number of leading lines handed to :func:`formats.detect`."""
MAX_BANNERS = 16
"""Most banners attached to one entry; earlier ones of a longer run stay entries."""


def _utf8_len(text: str) -> int:
    return len(text) if text.isascii() else len(text.encode("utf-8", "surrogatepass"))


def _mark(
    entry: LogEntry, orphan: bool, cut: list[int] | None, header_line: str, cont: list[str]
) -> LogEntry:
    """Set ``parse_error`` of an orphan or truncated entry; a truncated one gets a new id."""
    error = ORPHAN_ERROR if orphan else entry.parse_error
    if cut is None:
        return dataclasses.replace(entry, parse_error=error)
    lines = 1 + len(cont) + cut[0]
    size = _utf8_len(header_line) + sum(_utf8_len(t) + 1 for t in cont) + cut[1]
    note = TRUNCATED_ERROR.format(lines=lines, bytes=size)
    return dataclasses.replace(
        entry,
        # the kept text alone could match another truncated entry: hash its full size too
        id=xxhash.xxh3_64_hexdigest(f"{entry.id}:{lines}:{size}".encode()),
        parse_error=f"{error}; {note}" if error else note,
    )


def _assemble(
    lines: Iterable[tuple[int, int, str]],
    fmt: LogFormat,
    path: str,
    *,
    max_chars: int = MAX_ENTRY_CHARS,
    max_lines: int = MAX_ENTRY_LINES,
) -> Iterator[LogEntry]:
    is_header = fmt.is_header
    parse_header = fmt.parse_header
    build = fmt.build
    header = None
    header_line: str | None = None
    source = SourceRef(path, 0, 0)
    orphan = False
    cont: list[str] = []
    append = cont.append
    room = 0  # characters the entry may still keep
    left = 0  # continuation lines the entry may still keep
    cut: list[int] | None = None  # [lines, bytes] not kept, once the entry hit a cap
    for number, offset, text in lines:
        if not is_header(text):
            if header_line is None:
                # leading continuation lines: the first one stands in for the header
                header_line = text
                source = SourceRef(path, number, offset)
                orphan = True
                room = max_chars - len(text)
                left = max_lines - 1
            elif cut is None:
                room -= len(text) + 1
                left -= 1
                if room >= 0 and left >= 0:
                    append(text)
                else:
                    cut = [1, _utf8_len(text) + 1]
            else:
                cut[0] += 1
                cut[1] += _utf8_len(text) + 1
            continue
        if header_line is not None:
            entry = build(header, header_line, cont, source)
            yield (
                entry
                if not orphan and cut is None
                else _mark(entry, orphan, cut, header_line, cont)
            )
            orphan = False
            cut = None
            cont = []
            append = cont.append
        header = parse_header(text)
        header_line = text
        source = SourceRef(path, number, offset)
        room = max_chars - len(text)
        left = max_lines - 1
    if header_line is not None:
        entry = build(header, header_line, cont, source)
        yield entry if not orphan and cut is None else _mark(entry, orphan, cut, header_line, cont)


def _with_id(entry: LogEntry, entry_id: str) -> LogEntry:
    """``dataclasses.replace(entry, id=entry_id)`` at a third of its cost (per-entry path)."""
    return LogEntry(
        entry_id,
        entry.format,
        entry.timestamp,
        entry.level,
        entry.thread,
        entry.logger,
        entry.message,
        entry.body,
        entry.exceptions,
        entry.context,
        entry.source,
        entry.raw,
        entry.parse_error,
    )


def _distinct_ids(entries: Iterable[LogEntry]) -> Iterator[LogEntry]:
    """Make ids unique within one millisecond without remembering every entry.

    Entry ids hash ``(format, timestamp, thread, logger, raw)``, so a line that
    Jira writes twice in the same millisecond (separator banners, repeated
    warnings) would collapse into one entry when the store deduplicates. Every
    entry after the first of a run of adjacent entries in one millisecond is
    re-hashed with its ordinal ``k`` in the run: ``xxh3(id, 0x1f, k)``.

    A millisecond can also recur after other timestamps (threads log slightly
    out of order), and a repeated line there would get the id of its first
    occurrence. The ids issued in each of the last :data:`RECENT_TIMESTAMPS`
    milliseconds are therefore kept (the first :data:`IDS_PER_MILLISECOND` of
    each, so memory stays bounded), and an id issued already in its
    millisecond is re-hashed as ``xxh3(id, 0x1e, j)`` with the smallest free
    ``j``. Only such collisions change, so ids of earlier versions stay valid.
    Both rules depend only on the file content before the entry, so the same
    entry keeps its id when the file shows up again in another snapshot.
    """
    recent: OrderedDict[datetime, set[str]] = OrderedDict()
    issued: set[str] = set()
    last_ts = None
    k = 0
    for entry in entries:
        ts = entry.timestamp
        eid = entry.id
        if ts != last_ts:
            last_ts = ts
            k = 0
            seen = recent.get(ts)
            if seen is None:
                issued = recent[ts] = set()
                if len(recent) > RECENT_TIMESTAMPS:
                    recent.popitem(last=False)
            else:
                issued = seen
                recent.move_to_end(ts)
        else:
            k += 1
            eid = xxhash.xxh3_64_hexdigest(f"{eid}\x1f{k}".encode())
        if eid in issued:
            base, j = eid, 0
            while eid in issued:
                j += 1
                eid = xxhash.xxh3_64_hexdigest(f"{base}\x1e{j}".encode())
        if len(issued) < IDS_PER_MILLISECOND:
            issued.add(eid)
        yield entry if eid is entry.id else _with_id(entry, eid)


def is_banner(entry: LogEntry) -> bool:
    """Whether ``entry`` is a banner: a decoration-only header message and no body.

    The message must be :func:`~atlas_log_analyzer.core.decoration.is_banner_text`
    (ten or more characters without a letter or digit, such as a row of asterisks),
    and the header must have been recognised.
    """
    return entry.parse_error is None and not entry.body and is_banner_text(entry.message)


def _follows(banner: LogEntry, entry: LogEntry, window: timedelta) -> bool:
    """Whether ``entry`` continues ``banner``: same thread and logger, at most ``window`` later."""
    if entry.thread != banner.thread or entry.logger != banner.logger:
        return False
    gap = entry.timestamp - banner.timestamp
    return timedelta(0) <= gap <= window


def _with_preamble(entry: LogEntry, banners: Iterable[LogEntry]) -> LogEntry:
    context = dict(entry.context)
    context[CTX_PREAMBLE] = "\n".join(b.raw for b in banners)
    return dataclasses.replace(entry, context=MappingProxyType(context))


def attach_banners(
    entries: Iterable[LogEntry], window_ms: int, *, max_banners: int = MAX_BANNERS
) -> Iterator[LogEntry]:
    r"""Attach banner entries to the entry they announce (the entries of one file, in order).

    A banner (:func:`is_banner`) is followed by the next entry of the file when that
    entry has the same thread and logger and its timestamp is ``0..window_ms``
    milliseconds after the banner's. A run of banners, each followed by the next,
    that ends at an entry which is no banner is dropped, and that entry gets their
    ``raw`` texts, joined with ``\n``, as ``context["preamble"]``
    (:data:`~atlas_log_analyzer.core.formats.base.CTX_PREAMBLE`); its id, ``raw``,
    header and source stay as they are. Of a longer run only the last ``max_banners``
    are attached. Every other banner stays an entry. ``window_ms <= 0`` (or
    ``max_banners <= 0``) passes the entries through.

    Streaming with a lookahead of one entry (plus the pending run of banners).
    ``store.regroup.resign_entries`` applies the same rule to stored entries.
    """
    if window_ms <= 0 or max_banners <= 0:
        yield from entries
        return
    window = timedelta(milliseconds=window_ms)
    pending: deque[LogEntry] = deque()
    for entry in entries:
        if pending:
            if _follows(pending[-1], entry, window):
                if is_banner(entry):
                    pending.append(entry)
                    if len(pending) > max_banners:
                        yield pending.popleft()
                    continue
                yield _with_preamble(entry, pending)
                pending.clear()
                continue
            yield from pending
            pending.clear()
        if is_banner(entry):
            pending.append(entry)
        else:
            yield entry
    yield from pending


def parse_lines(
    lines: Iterable[Line],
    fmt: LogFormat,
    path: str,
    *,
    max_entry_chars: int = MAX_ENTRY_CHARS,
    max_entry_lines: int = MAX_ENTRY_LINES,
) -> Iterator[LogEntry]:
    """Assemble entries from already-read lines (first-line / continuation state machine).

    Lines before the first header become one entry with ``parse_error``.
    A line for which ``fmt.is_header`` is true but ``fmt.parse_header`` returns
    ``None`` becomes an entry with ``parse_error`` and is *not* merged into the
    previous entry. Entries after the first in a millisecond get ids re-hashed
    with their ordinal (see :func:`_distinct_ids`). When the format opts in
    (``banner_window_ms``), banners are then attached to the entry they announce
    (:func:`attach_banners`).

    An entry keeps at most ``max_entry_lines`` lines and ``max_entry_chars``
    characters (header included, whole lines only); the lines after the cap are
    counted, not stored, and ``parse_error`` says ``entry truncated: N lines, M
    bytes`` (:data:`TRUNCATED_ERROR`). The id of a truncated entry also hashes N
    and M, so it stays deterministic.
    """
    rows = ((ln.number, ln.offset, ln.text) for ln in lines)
    return attach_banners(
        _distinct_ids(
            _assemble(rows, fmt, path, max_chars=max_entry_chars, max_lines=max_entry_lines)
        ),
        banner_window_ms(fmt),
    )


def parse_file(
    path: Path | str,
    fmt: LogFormat,
    *,
    max_entry_chars: int = MAX_ENTRY_CHARS,
    max_entry_lines: int = MAX_ENTRY_LINES,
    max_bytes: int | None = None,
) -> Iterator[LogEntry]:
    """Stream entries from one file (lines over 1 MiB are cut, see :func:`iter_raw_lines`).

    As :func:`parse_lines`: ids made unique, then banners attached when the format
    opts in. ``max_bytes`` stops reading after that many bytes.
    """
    return attach_banners(
        _distinct_ids(
            _assemble(
                iter_raw_lines(path, max_bytes=max_bytes),
                fmt,
                str(path),
                max_chars=max_entry_chars,
                max_lines=max_entry_lines,
            )
        ),
        banner_window_ms(fmt),
    )


def parse_sources(sources: Iterable[SourceFile], fmt: LogFormat) -> Iterator[LogEntry]:
    """Stream entries from several sources in the given order (default caps).

    Each file is read up to the size it had when it was discovered and hashed
    (:attr:`~atlas_log_analyzer.core.reader.SourceFile.size`): lines appended to a live
    log meanwhile are not part of the set its digest names.
    """
    for source in sources:
        yield from parse_file(source.path, fmt, max_bytes=source.size)


def parse(paths: Iterable[Path | str], fmt: LogFormat | None = None) -> Iterator[LogEntry]:
    """Discover sources, detect the format when ``None`` and stream entries (convenience).

    Raises:
        ValueError: When ``fmt`` is ``None`` and no format recognises the first source.

    """
    sources = discover_sources(paths)
    if fmt is None:
        from atlas_log_analyzer.core.formats.registry import detect

        sample: list[str] = []
        for source in sources:
            raw_lines = iter_raw_lines(source.path)
            try:
                sample = [text for _, _, text in islice(raw_lines, DETECT_LINES)]
            finally:
                raw_lines.close()
            if sample:
                break
        detected = detect(sample)
        if detected is None:
            raise ValueError("could not detect the log format; pass one explicitly")
        fmt = detected
    return parse_sources(sources, fmt)
