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
from atlas_log_analyzer.core.grouping import Grouper, SignedEntry
from atlas_log_analyzer.core.masking import GENERIC_RULES, Masker
from atlas_log_analyzer.core.parser import parse_sources
from atlas_log_analyzer.core.reader import SourceFile, discover_sources, iter_lines
from atlas_log_analyzer.core.signature import SignatureSpec, Signer
from atlas_log_analyzer.store.logset import LogSet
from atlas_log_analyzer.store.store import Store, StoreError
from atlas_log_analyzer.store.writer import write_merges

__all__ = [
    "PROGRESS_EVERY",
    "SNIFF_LINES",
    "FormatNotDetected",
    "IngestProgress",
    "IngestResult",
    "NoSourcesFound",
    "detect_format",
    "ingest_paths",
    "ingest_sources",
]

PROGRESS_EVERY = 50_000
"""Default number of entries between two progress callbacks."""
SNIFF_LINES = 200
"""Lines of the first source used to detect the format."""


class NoSourcesFound(StoreError):
    """The given paths contain no log files."""


class FormatNotDetected(StoreError):
    """No registered format recognises the first source."""


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

    def ingest_dict(self) -> dict[str, Any]:
        """Return the ``"ingest"`` object of the CLI's logset.v1 output."""
        return {
            "parsed_entries": self.parsed_entries,
            "parse_errors": self.parse_errors,
            "elapsed_s": round(self.elapsed_s, 3),
            "existing": self.existing,
        }


def detect_format(sources: Sequence[SourceFile]) -> LogFormat:
    """Detect the format from the first :data:`SNIFF_LINES` lines of the first source.

    Raises:
        FormatNotDetected: No format recognises the lines.

    """
    first = [line.text for line in islice(iter_lines(sources[0].path), SNIFF_LINES)]
    fmt = formats.detect(first)
    if fmt is None:
        raise FormatNotDetected(
            f"could not detect the log format of {sources[0].path}; pass --format "
            "(see `atlas-log formats`)"
        )
    return fmt


@dataclass
class _Counter:
    entries: int = 0
    parse_errors: int = 0


def _tracked(
    signed: Iterable[SignedEntry],
    counter: _Counter,
    sources: Sequence[SourceFile],
    progress: Callable[[IngestProgress], None] | None,
    every: int,
) -> Iterator[SignedEntry]:
    total = sum(s.size for s in sources)
    base: dict[str, int] = {}
    acc = 0
    for s in sources:
        base[str(s.path)] = acc
        acc += s.size
    for item in signed:
        counter.entries += 1
        if item.entry.parse_error is not None:
            counter.parse_errors += 1
        if progress is not None and counter.entries % every == 0:
            src = item.entry.source
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
    grouper = Grouper(Signer(Masker([*fmt.mask_rules, *GENERIC_RULES]), spec))
    counter = _Counter()
    started = time.perf_counter()
    signed = _tracked(
        grouper.sign(parse_sources(sources, fmt)), counter, sources, progress, progress_every
    )
    logset = store.ingest(
        signed, grouper.sorted_groups, sources, fmt, name=name, spec=spec, overwrite=overwrite
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
    progress: Callable[[IngestProgress], None] | None = None,
    progress_every: int = PROGRESS_EVERY,
) -> IngestResult:
    """Discover sources in ``paths``, detect the format when not given, and ingest them.

    Args:
        store: Target store.
        paths: Files and/or directories (non-recursive ``*.log*``).
        fmt: Format name or object; default: detected from the first source.
        name: Name of the new set.
        spec: Signature spec; default the format's.
        frames: Override ``spec.frames``.
        overwrite: Rebuild an existing set with the same id.
        mine: Propose Drain3 merges (see :func:`ingest_sources`).
        progress: Progress callback (see :class:`IngestProgress`).
        progress_every: Entries between progress callbacks.

    Raises:
        NoSourcesFound: ``paths`` holds no log files.
        FormatNotDetected: ``fmt`` is ``None`` and detection failed.
        FileNotFoundError: A path does not exist.
        LookupError: Unknown format name (``UnknownFormatError``).
        StoreError: See :meth:`Store.ingest`.

    """
    path_list = list(paths)
    sources = discover_sources(path_list)
    if not sources:
        shown = ", ".join(str(p) for p in path_list)
        raise NoSourcesFound(f"no log files found in {shown}")
    if isinstance(fmt, str):
        fmt = formats.get(fmt)
    elif fmt is None:
        fmt = detect_format(sources)
    return ingest_sources(
        store,
        sources,
        fmt,
        name=name,
        spec=spec,
        frames=frames,
        overwrite=overwrite,
        mine=mine,
        progress=progress,
        progress_every=progress_every,
    )
