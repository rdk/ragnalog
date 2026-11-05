"""Documents that write: ingest, regroup, upgrade, remove and the report file.

The front ends call these instead of the store's writers. Ingest is
:func:`ingest` (sources already selected, the CLI) or :func:`ingest_paths`
(discovery included, the web app's jobs); both return the ``logset.v1`` manifest
with ``ingest`` and, when mined, ``merges``.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from atlas_log_analyzer.service.errors import Front, OpError, flag
from atlas_log_analyzer.service.params import parse_level
from atlas_log_analyzer.service.sets import Sets, open_set

if TYPE_CHECKING:
    from atlas_log_analyzer.store.logset import LogSet
    from atlas_log_analyzer.store.pipeline import IngestProgress, IngestResult, SourceSelection
    from atlas_log_analyzer.store.store import Store

__all__ = [
    "Removable",
    "ingest",
    "ingest_paths",
    "regroup",
    "remove",
    "report_file",
    "report_filename",
    "select",
    "upgrade",
]

type Doc = dict[str, Any]
type Progress = Callable[[IngestProgress], None]


def _manifest(ls: LogSet) -> Doc:
    return dict(ls.manifest.to_dict())


def select(
    paths: Sequence[Path], fmt: str | None, *, force: bool = False, front: Front
) -> SourceSelection:
    """Discover the files of ``paths`` and resolve the format (foreign files are skipped).

    Raises:
        OpError: ``usage`` without paths; the pipeline's errors (``no_sources``,
            ``format_not_detected``, ``not_in_format``, ``unknown_format``,
            ``file_not_found``) are mapped by ``from_exception`` at the boundary.

    """
    from atlas_log_analyzer.store.pipeline import select_sources

    if not paths:
        raise OpError("usage", "at least one PATH is required")
    return select_sources(paths, fmt, force=force)


def _document(result: IngestResult) -> Doc:
    doc = _manifest(result.logset)
    doc["ingest"] = result.ingest_dict()
    if result.merges is not None:
        doc["merges"] = result.merges
    return doc


def _missing_mine(exc: ImportError, front: Front) -> OpError:
    return OpError(
        "missing_dependency",
        f"{flag('mine', front)} needs the optional 'mine' extra (drain3): {exc}. "
        "Install it with `uv sync --extra mine`.",
    )


def _check_frames(frames: int | None, front: Front) -> None:
    if frames is not None and frames < 0:
        raise OpError("invalid_value", f"{flag('frames', front)} must be >= 0")


def ingest(
    store: Store,
    selection: SourceSelection,
    *,
    name: str | None = None,
    frames: int | None = None,
    overwrite: bool = False,
    mine: bool = False,
    progress: Progress | None = None,
    front: Front,
) -> Doc:
    """Parse, sign and store the selected sources; ``logset.v1`` plus ``ingest``/``merges``.

    ``ingest.skipped`` lists the files of ``selection`` that are not in the format.
    With ``mine`` the Drain3 proposals are stored in ``merges.parquet`` and listed
    under ``merges``; without the ``mine`` extra the set is stored nonetheless and
    the error is ``missing_dependency``.
    """
    from atlas_log_analyzer.store.pipeline import ingest_sources

    _check_frames(frames, front)
    try:
        result = ingest_sources(
            store,
            selection.sources,
            selection.fmt,
            name=name,
            frames=frames,
            overwrite=overwrite,
            mine=mine,
            progress=progress,
        )
    except ImportError as exc:
        raise _missing_mine(exc, front) from exc
    skipped = tuple(str(s.path) for s in selection.skipped)
    return _document(dataclasses.replace(result, skipped=skipped))


def ingest_paths(
    store: Store,
    paths: Iterable[Path],
    *,
    fmt: str | None = None,
    name: str | None = None,
    frames: int | None = None,
    overwrite: bool = False,
    mine: bool = False,
    force: bool = False,
    progress: Progress | None = None,
    progress_every: int | None = None,
    front: Front,
) -> Doc:
    """Discover, detect, parse and store ``paths`` in one call (the web app's ingest jobs)."""
    from atlas_log_analyzer.store.pipeline import PROGRESS_EVERY
    from atlas_log_analyzer.store.pipeline import ingest_paths as pipeline_ingest

    _check_frames(frames, front)
    try:
        result = pipeline_ingest(
            store,
            paths,
            fmt=fmt,
            name=name,
            frames=frames,
            overwrite=overwrite,
            mine=mine,
            force=force,
            progress=progress,
            progress_every=progress_every or PROGRESS_EVERY,
        )
    except ImportError as exc:
        raise _missing_mine(exc, front) from exc
    return _document(result)


def regroup(
    store: Store, ref: str, *, frames: int = 0, no_exceptions: bool = False, front: Front
) -> Doc:
    """Recompute a set's groups with another signature spec; its updated ``logset.v1``."""
    from atlas_log_analyzer.core import formats
    from atlas_log_analyzer.core.formats.registry import UnknownFormatError
    from atlas_log_analyzer.core.signature import DEFAULT_SPEC, SignatureSpec

    _check_frames(frames, front)
    ls = open_set(store, ref, front=front)
    try:
        base: SignatureSpec = formats.get(ls.manifest.format).signature_spec
    except UnknownFormatError:
        base = DEFAULT_SPEC
    spec = dataclasses.replace(base, frames=frames, include_exceptions=not no_exceptions)
    return _manifest(store.regroup(ls.manifest.id, spec))


def upgrade(store: Store, refs: Sequence[str], *, all_sets: bool = False, front: Front) -> Doc:
    """``upgrade.v1``: rebuild stale sets at the current recipe, each with its own spec.

    ``refs`` names the sets (in that order), or ``all_sets`` takes every set (newest
    first). Items carry ``status`` (``upgraded``/``skipped``), ``reason`` and
    ``previous_recipe_key``.
    """
    every = flag("all", front)
    if all_sets and refs:
        raise OpError("invalid_value", f"give SET arguments or {every}, not both")
    if all_sets:
        ids = [m.id for m in store.list()]
    elif refs:
        ids = [open_set(store, ref, front=front).manifest.id for ref in refs]
    else:
        raise OpError("invalid_value", f"give one or more SETs, or {every}")
    items = []
    for set_id in dict.fromkeys(ids):
        result = store.upgrade(set_id)
        item = {k: v for k, v in result.manifest.to_dict().items() if k != "schema"}
        item["status"] = "upgraded" if result.upgraded else "skipped"
        item["reason"] = result.reason
        item["previous_recipe_key"] = result.previous_recipe_key
        items.append(item)
    return {
        "schema": "upgrade.v1",
        "count": len(items),
        "upgraded": sum(1 for i in items if i["status"] == "upgraded"),
        "items": items,
    }


class Removable(Sets, Protocol):
    """Sets that can also be removed (``Store``; the web app's ``AppState`` drops its caches)."""

    def remove(self, ref: str) -> object:
        """Delete the set ``ref``."""
        ...


def remove(sets: Removable, ref: str, *, front: Front) -> Doc:
    """``remove.v1``: delete a set (its directory and every file)."""
    ls = open_set(sets, ref, front=front)
    sets.remove(ls.manifest.id)
    return {"schema": "remove.v1", "removed": ls.manifest.id, "name": ls.manifest.name}


def report_filename(ls: LogSet) -> Path:
    """Default report file of a set: ``<name or id>.html`` in the current directory."""
    stem = re.sub(r"[^\w.-]+", "_", ls.manifest.name or ls.manifest.id).strip("._")
    return Path((stem or ls.manifest.id) + ".html")


def report_file(
    sets: Sets,
    ref: str,
    *,
    out: Path | None = None,
    compare: str | None = None,
    min_level: str | None = None,
    examples: int = 3,
    max_raw: int = 4000,
    max_groups: int = 0,
    front: Front,
) -> Doc:
    """Write the HTML report of a set (optionally compared with ``compare``); ``report-file.v1``.

    ``max_groups`` 0 = all. ``path`` in the document is absolute.
    """
    from atlas_log_analyzer.report import write_report
    from atlas_log_analyzer.store.query import IncompatibleSets

    lvl = parse_level(min_level, "min_level", front=front)
    for name, value in (("examples", examples), ("max_raw", max_raw), ("max_groups", max_groups)):
        if value < 0:
            raise OpError("invalid_value", f"{flag(name, front)} must be >= 0")
    ls = open_set(sets, ref, front=front)
    baseline = open_set(sets, compare, front=front) if compare is not None else None
    path = out if out is not None else report_filename(ls)
    try:
        info = write_report(
            ls,
            path,
            compare=baseline,
            min_level=lvl,
            examples=examples,
            max_raw=max_raw,
            max_groups=max_groups or None,
        )
    except IncompatibleSets as exc:
        raise OpError.because("incompatible_sets", front, str(exc)) from exc
    return {
        "schema": "report-file.v1",
        "path": info.path.resolve().as_posix(),
        "bytes": info.bytes,
        "groups": info.groups,
        "entries": info.entries,
        "compare": info.compare,
    }
