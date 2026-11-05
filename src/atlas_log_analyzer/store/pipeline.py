"""Ingest pipeline: discover sources → detect format → parse → sign → store (→ mine).

This is the composition the CLI ``ingest`` command and the web app share::

    result = ingest_paths(Store(root), [Path("logs/")], name="prod", progress=print)
    result.logset.manifest.entry_count
"""

from __future__ import annotations

import dataclasses
import importlib
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import Any

from atlas_log_analyzer.core import formats
from atlas_log_analyzer.core.formats.base import LogFormat
from atlas_log_analyzer.core.models import LogEntry
from atlas_log_analyzer.core.parser import parse_sources
from atlas_log_analyzer.core.pipeline import Recipe, unique_entries
from atlas_log_analyzer.core.reader import SourceFile, discover_sources, iter_raw_lines
from atlas_log_analyzer.core.signature import SignatureSpec
from atlas_log_analyzer.store.logset import LogSet
from atlas_log_analyzer.store.store import Store, StoreError
from atlas_log_analyzer.store.writer import write_merges

__all__ = [
    "MAX_FOREIGN_BYTES",
    "PROGRESS_EVERY",
    "SNIFF_LINES",
    "FormatNotDetected",
    "IngestProgress",
    "IngestResult",
    "NoSourcesFound",
    "NotInFormat",
    "SourceSelection",
    "detect_format",
    "ingest_paths",
    "ingest_sources",
    "select_sources",
]

PROGRESS_EVERY = 50_000
"""Default number of entries between two progress callbacks."""
SNIFF_LINES = 200
"""Lines of the first source used to detect the format."""
MAX_FOREIGN_BYTES = 64 << 20
"""Size above which a file named explicitly must be in the format (see :func:`select_sources`)."""


class NoSourcesFound(StoreError):
    """The given paths contain no log files."""


class FormatNotDetected(StoreError):
    """No registered format recognises the first source."""


class NotInFormat(StoreError):
    """A file named explicitly is larger than :data:`MAX_FOREIGN_BYTES` and not in the format."""


@dataclass(frozen=True, slots=True)
class IngestProgress:
    """Progress of a running ingest, passed to the ``progress`` callback."""

    entries: int
    """Entries parsed so far."""
    bytes_read: int
    """Approximate bytes consumed (offset of the current entry within all sources)."""
    bytes_total: int
    """Total size of all sources."""
    current_file: str
    """Path of the file being parsed."""


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Outcome of :func:`ingest_sources` / :func:`ingest_paths`."""

    logset: LogSet
    parsed_entries: int
    """Entries parsed in this run (0 when an existing set was reused)."""
    parse_errors: int
    """Parsed entries whose ``parse_error`` is set."""
    elapsed_s: float
    existing: bool
    """The set already existed and was reused without parsing."""
    merges: list[dict[str, Any]] | None = field(default=None)
    """Merge proposals (``{"cluster_id", "signatures", "drain_template"}``) when mined."""
    skipped: tuple[str, ...] = ()
    """Paths of files found in directories but not in the format (see :func:`select_sources`)."""
    warnings: tuple[str, ...] = ()
    """Requests that were not honoured, e.g. a ``name`` ignored because the set existed."""

    def ingest_dict(self) -> dict[str, Any]:
        """Return the ``"ingest"`` object of the CLI's logset.v1 output."""
        return {
            "parsed_entries": self.parsed_entries,
            "parse_errors": self.parse_errors,
            "elapsed_s": round(self.elapsed_s, 3),
            "existing": self.existing,
            "skipped": list(self.skipped),
            "warnings": list(self.warnings),
        }


def _name_warning(name: str | None, logset: LogSet) -> tuple[str, ...]:
    """Warn when ``name`` was not applied because the set already existed under another one."""
    m = logset.manifest
    if name is None or m.name == name:
        return ()
    shown = repr(m.name) if m.name is not None else f"{m.id} (unnamed)"
    return (f"--name {name} ignored: set exists as {shown}",)


def _head(source: SourceFile) -> list[str]:
    """Return the first :data:`SNIFF_LINES` lines of a source."""
    lines = iter_raw_lines(source.path)
    try:
        return [text for _, _, text in islice(lines, SNIFF_LINES)]
    finally:
        lines.close()


def detect_format(sources: Sequence[SourceFile]) -> LogFormat:
    """Detect the format from the first :data:`SNIFF_LINES` lines of the sources, in order.

    The first source that some format recognises decides, so a directory whose
    oldest file is an access or GC log is still detected.

    Raises:
        FormatNotDetected: No format recognises any source.

    """
    for source in sources:
        fmt = formats.detect(_head(source))
        if fmt is not None:
            return fmt
    raise FormatNotDetected(
        f"could not detect the log format of {sources[0].path}; pass --format "
        "(see `atlas-log formats`)"
    )


@dataclass(frozen=True, slots=True)
class SourceSelection:
    """Result of :func:`select_sources`."""

    sources: list[SourceFile]
    """Files to ingest, in reading order."""
    skipped: list[SourceFile]
    """Files found in directories that the format does not recognise."""
    fmt: LogFormat


def select_sources(
    paths: Iterable[Path | str], fmt: str | LogFormat | None = None, *, force: bool = False
) -> SourceSelection:
    """Discover the files in ``paths``, resolve the format and drop foreign files.

    A Jira log directory also holds access, GC and JSON logs that ``*.log*``
    matches; parsed as Jira they would become one huge ``parse_error`` entry.
    Files found by scanning a directory are therefore kept only when the format
    recognises at least one of their first :data:`SNIFF_LINES` lines. Files named
    explicitly are kept, except one larger than :data:`MAX_FOREIGN_BYTES` that
    the format does not recognise at all (a wrong file or ``--format``), unless
    ``force`` is true.

    Raises:
        NoSourcesFound: ``paths`` holds no log files, or none in the format.
        FormatNotDetected: ``fmt`` is ``None`` and detection failed.
        NotInFormat: A large file named explicitly is not in the format.
        FileNotFoundError: A path does not exist.
        LookupError: Unknown format name (``UnknownFormatError``).

    """
    path_list = list(paths)
    found = discover_sources(path_list)
    shown = ", ".join(str(p) for p in path_list)
    if not found:
        raise NoSourcesFound(f"no log files found in {shown}")
    named = {Path(p).resolve() for p in path_list if Path(p).is_file()}
    if isinstance(fmt, str):
        fmt = formats.get(fmt)
    elif fmt is None:
        named_first = [s for s in found if s.path in named] + [
            s for s in found if s.path not in named
        ]
        fmt = detect_format(named_first)
    sources: list[SourceFile] = []
    skipped: list[SourceFile] = []
    for source in found:
        if source.path in named:
            if not force and source.size > MAX_FOREIGN_BYTES and fmt.sniff(_head(source)) == 0:
                raise NotInFormat(
                    f"{source.path.name} ({source.size / 1e6:.0f} MB) is not a {fmt.name} log: "
                    f"none of its first {SNIFF_LINES} lines is a {fmt.name} header"
                )
            sources.append(source)
        elif fmt.sniff(_head(source)) > 0:
            sources.append(source)
        else:
            skipped.append(source)
    if not sources:
        raise NoSourcesFound(f"no {fmt.name} log files found in {shown}")
    return SourceSelection(sources, skipped, fmt)


@dataclass
class _Counter:
    entries: int = 0
    parse_errors: int = 0


def _tracked(
    entries: Iterable[LogEntry],
    counter: _Counter,
    sources: Sequence[SourceFile],
    progress: Callable[[IngestProgress], None] | None,
    every: int,
) -> Iterator[LogEntry]:
    total = sum(s.size for s in sources)
    base: dict[str, int] = {}
    acc = 0
    for s in sources:
        base[str(s.path)] = acc
        acc += s.size
    for item in entries:
        counter.entries += 1
        if item.parse_error is not None:
            counter.parse_errors += 1
        if progress is not None and counter.entries % every == 0:
            src = item.source
            progress(
                IngestProgress(
                    entries=counter.entries,
                    bytes_read=min(total, base.get(src.path, 0) + src.offset),
                    bytes_total=total,
                    current_file=src.path,
                )
            )
        yield item


def _mine(logset: LogSet) -> list[dict[str, Any]]:
    """Propose merges with Drain3 and write ``merges.parquet``; ``ImportError`` without drain3."""
    mining = importlib.import_module("atlas_log_analyzer.core.mining")
    pairs = list(logset.groups().select("signature", "template").iter_rows())
    proposals = list(mining.propose_merges(pairs))
    write_merges(logset.merges_path, proposals)
    return [
        {
            "cluster_id": p.cluster_id,
            "signatures": list(p.signatures),
            "drain_template": p.drain_template,
        }
        for p in proposals
    ]


def ingest_sources(
    store: Store,
    sources: Sequence[SourceFile],
    fmt: LogFormat,
    *,
    name: str | None = None,
    spec: SignatureSpec | None = None,
    frames: int | None = None,
    overwrite: bool = False,
    mine: bool = False,
    progress: Callable[[IngestProgress], None] | None = None,
    progress_every: int = PROGRESS_EVERY,
) -> IngestResult:
    """Parse, sign and store already discovered ``sources`` with a known format.

    Args:
        store: Target store.
        sources: Files in reading order (see :func:`~atlas_log_analyzer.core.reader.
            discover_sources`).
        fmt: Log format.
        name: Name of the new set.
        spec: Signature spec; default ``fmt.signature_spec``.
        frames: Override ``spec.frames`` (top stack frames in signatures).
        overwrite: Rebuild an existing set with the same id.
        mine: Propose Drain3 merges and write ``merges.parquet`` (needs the ``mine`` extra).
        progress: Called every ``progress_every`` parsed entries.
        progress_every: Entries between progress callbacks.

    Raises:
        StoreError: See :meth:`Store.ingest`.
        ImportError: ``mine=True`` without drain3 (the set is stored nonetheless).
        ValueError: ``frames`` or ``progress_every`` is negative / not positive.

    """
    if frames is not None and frames < 0:
        raise ValueError("frames must be >= 0")
    if progress_every <= 0:
        raise ValueError("progress_every must be > 0")
    spec = spec or fmt.signature_spec
    if frames is not None:
        spec = dataclasses.replace(spec, frames=frames)
    recipe = Recipe(fmt, spec)
    grouper = recipe.grouper()
    counter = _Counter()
    started = time.perf_counter()
    parsed = _tracked(parse_sources(sources, fmt), counter, sources, progress, progress_every)
    # Deduplicate before grouping so group counts match the stored entries (no
    # recomputation from entries.parquet) and only one id set is held.
    signed = grouper.sign(unique_entries(parsed))
    logset = store.ingest(
        signed,
        grouper.sorted_groups,
        sources,
        fmt,
        name=name,
        spec=spec,
        overwrite=overwrite,
        dedupe=False,
        recipe=recipe,
    )
    elapsed = time.perf_counter() - started
    merges = _mine(logset) if mine else None
    return IngestResult(
        logset=logset,
        parsed_entries=counter.entries,
        parse_errors=counter.parse_errors,
        elapsed_s=elapsed,
        existing=counter.entries == 0 and logset.manifest.entry_count > 0,
        merges=merges,
        warnings=_name_warning(name, logset),
    )


def ingest_paths(
    store: Store,
    paths: Iterable[Path | str],
    *,
    fmt: str | LogFormat | None = None,
    name: str | None = None,
    spec: SignatureSpec | None = None,
    frames: int | None = None,
    overwrite: bool = False,
    mine: bool = False,
    force: bool = False,
    progress: Callable[[IngestProgress], None] | None = None,
    progress_every: int = PROGRESS_EVERY,
) -> IngestResult:
    """Discover sources in ``paths``, detect the format when not given, and ingest them.

    Files found in directories that the format does not recognise are skipped
    and listed in :attr:`IngestResult.skipped` (see :func:`select_sources`).

    Args:
        store: Target store.
        paths: Files and/or directories (non-recursive ``*.log*``).
        fmt: Format name or object; default: detected from the sources (named files
            first).
        name: Name of the new set.
        spec: Signature spec; default the format's.
        frames: Override ``spec.frames``.
        overwrite: Rebuild an existing set with the same id.
        mine: Propose Drain3 merges (see :func:`ingest_sources`).
        force: Read large named files that are not in the format (see :func:`select_sources`).
        progress: Progress callback (see :class:`IngestProgress`).
        progress_every: Entries between progress callbacks.

    Raises:
        NoSourcesFound: ``paths`` holds no log files.
        FormatNotDetected: ``fmt`` is ``None`` and detection failed.
        NotInFormat: A large named file is not in the format and ``force`` is false.
        FileNotFoundError: A path does not exist.
        LookupError: Unknown format name (``UnknownFormatError``).
        StoreError: See :meth:`Store.ingest`.

    """
    selection = select_sources(paths, fmt, force=force)
    result = ingest_sources(
        store,
        selection.sources,
        selection.fmt,
        name=name,
        spec=spec,
        frames=frames,
        overwrite=overwrite,
        mine=mine,
        progress=progress,
        progress_every=progress_every,
    )
    return dataclasses.replace(result, skipped=tuple(str(s.path) for s in selection.skipped))
