"""Documents that write: ingest, label, regroup, upgrade, remove and the report file.

The front ends call these instead of the store's writers. Ingest is
:func:`ingest_paths` (ZIP inputs, the split into one set per node and discovery
included: the CLI and the web app's jobs) or :func:`ingest` (sources already
selected, one set). One set gives the ``logset.v1`` manifest with ``ingest`` and,
when mined, ``merges``; a split gives ``ingest-capture.v1`` with one such manifest
per node (docs/cluster.md, §3).
"""

from __future__ import annotations

import contextlib
import dataclasses
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol

from atlas_log_analyzer.core.paths import safe_name
from atlas_log_analyzer.service.errors import Front, OpError, flag
from atlas_log_analyzer.service.params import (
    _LABEL,
    IngestTarget,
    check_count,
    check_label,
    check_out,
    parse_level,
)
from atlas_log_analyzer.service.sets import Sets, open_set, resolving

if TYPE_CHECKING:
    from atlas_log_analyzer.core.formats.base import LogFormat
    from atlas_log_analyzer.core.reader import SourceFile
    from atlas_log_analyzer.store.logset import LogSet
    from atlas_log_analyzer.store.manifest import LogSetManifest
    from atlas_log_analyzer.store.pipeline import IngestProgress, IngestResult, SourceSelection
    from atlas_log_analyzer.store.split import Split
    from atlas_log_analyzer.store.store import Label, Store

__all__ = [
    "HINT_LINES",
    "Labeller",
    "Removable",
    "ingest",
    "ingest_paths",
    "label",
    "regroup",
    "remove",
    "report_file",
    "report_filename",
    "select",
    "upgrade",
]

type Doc = dict[str, Any]
type Progress = Callable[[IngestProgress], None]
type Announce = Callable[[str], None]

HINT_LINES: Final = 65_536
"""Lines of a node's newest file read for :func:`~atlas_log_analyzer.core.formats.base.
node_hints`."""


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


def _document(result: IngestResult, manifest: LogSetManifest | None = None) -> Doc:
    doc = dict((manifest or result.logset.manifest).to_dict())
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


@dataclass(frozen=True, slots=True)
class _Options:
    """What one :func:`ingest_paths` call applies to every set it writes."""

    fmt: str | None
    name: str | None
    frames: int | None
    overwrite: bool
    mine: bool
    force: bool
    progress: Progress | None
    progress_every: int
    announce: Announce | None
    front: Front

    def say(self, text: str) -> None:
        if self.announce is not None:
            self.announce(text)


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
    target: IngestTarget | None = None,
    progress: Progress | None = None,
    progress_every: int | None = None,
    announce: Announce | None = None,
    front: Front,
) -> Doc:
    """Extract, split, discover, parse and store ``paths`` (``ingest``, the web app's jobs).

    ``.zip`` inputs are extracted into the store's spool (``job.json`` and log files
    only) and removed afterwards. ``target.split`` decides whether the inputs are one
    set or one set per node (:func:`~atlas_log_analyzer.store.split.plan_split`).

    One set: ``logset.v1`` with ``ingest`` (and ``merges``), labelled with
    ``target.node``, ``target.cluster`` (default: the cluster of the newest set of
    that node) and ``target.capture``. A split: ``ingest-capture.v1``
    ``{capture, sets, warnings}``, one set per node named ``<prefix>_<node>`` (prefix
    ``name``, else the cluster, else the first 8 characters of the capture id) and
    labelled with the node, the cluster and the capture id (``target.capture``, else
    the export's ``jobId``, else a hash of the set ids). Existing sets are reused
    (``ingest.existing``) and get the labels they lack. A split that fails or is
    cancelled removes the sets it added, so no capture is left with some nodes only.
    ``announce`` receives one-line notes for people (the CLI prints them on stderr).

    Raises:
        OpError: ``usage`` without paths; ``invalid_value`` for a bad option, a bad ZIP
            or a forced split that does not fit, or ``node`` with a split;
            ``missing_dependency`` for ``mine`` without drain3; the pipeline's errors
            are mapped by ``from_exception`` at the boundary.

    """
    from atlas_log_analyzer.store.pipeline import PROGRESS_EVERY
    from atlas_log_analyzer.store.split import SPOOL_DIR, expand_archives, plan_split

    path_list = list(paths)
    if not path_list:
        raise OpError("usage", "at least one PATH is required")
    _check_frames(frames, front)
    target = (target or IngestTarget()).checked(front=front)
    opts = _Options(
        fmt=fmt,
        name=name,
        frames=frames,
        overwrite=overwrite,
        mine=mine,
        force=force,
        progress=progress,
        progress_every=progress_every or PROGRESS_EVERY,
        announce=announce,
        front=front,
    )
    try:
        with expand_archives(path_list, store.root / SPOOL_DIR) as inputs:
            split = plan_split(inputs, target.split)
            if split.kind is None:
                return _single(store, split, target, opts)
            if target.node is not None:
                nodes = ", ".join(n.node for n in split.nodes)
                raise OpError(
                    "invalid_value",
                    f"{flag('node', front)} labels one set, but the input splits into "
                    f"nodes {nodes} ({split.kind}); pass {flag('split', front)} none to "
                    "ingest it as one set",
                )
            return _split(store, split, target, opts)
    except ImportError as exc:
        raise _missing_mine(exc, front) from exc


def _select(split: Split, o: _Options) -> SourceSelection:
    from atlas_log_analyzer.store.pipeline import select_sources

    selection = select_sources(split.paths, o.fmt, force=o.force)
    for note in selection.skipped_notes():
        o.say(note)
    return selection


def _size(sources: Sequence[SourceFile]) -> str:
    return f"{len(sources)} file(s), {sum(s.size for s in sources) / 1e6:.1f} MB"


def _single(store: Store, split: Split, target: IngestTarget, o: _Options) -> Doc:
    from atlas_log_analyzer.store.pipeline import ingest_sources
    from atlas_log_analyzer.store.store import logset_id

    selection = _select(split, o)
    o.say(f"ingesting {_size(selection.sources)}, format {selection.fmt.name}")
    manifests = store.list()
    old = _by_id(manifests, logset_id(s.xxh3 for s in selection.sources))
    keep = old if o.overwrite else None  # a rebuild keeps the labels it is not given
    node = target.node or (keep.node if keep else None)
    cluster = target.cluster or (keep.cluster if keep else None) or _node_cluster(manifests, node)
    capture = target.capture or (keep.capture if keep else None)
    result = ingest_sources(
        store,
        selection.sources,
        selection.fmt,
        name=o.name or (keep.name if keep else None),  # a rebuild keeps its name too
        frames=o.frames,
        overwrite=o.overwrite,
        mine=o.mine,
        progress=o.progress,
        progress_every=o.progress_every,
        node=node,
        cluster=cluster,
        capture=capture,
    )
    m, warnings = _apply_identity(store, result.logset.manifest, node, cluster, capture, o.front)
    warnings += _hint_warnings(selection.fmt, selection.sources, m, o.front, split=False)
    result = dataclasses.replace(
        result,
        skipped=tuple(str(s.path) for s in selection.skipped),
        warnings=(*result.warnings, *warnings),
    )
    return _document(result, m)


def _split(store: Store, split: Split, target: IngestTarget, o: _Options) -> Doc:
    import msgspec

    from atlas_log_analyzer.service.captures import capture_id, get_capture
    from atlas_log_analyzer.store.pipeline import NoSourcesFound, ingest_sources
    from atlas_log_analyzer.store.store import logset_id

    selection = _select(split, o)
    fmt = selection.fmt
    node_of = split.node_of()
    sources: dict[str, list[SourceFile]] = {n.node: [] for n in split.nodes}
    skipped: dict[str, list[str]] = {n.node: [] for n in split.nodes}
    for src in selection.sources:
        if (node := node_of.get(src.path)) is not None:
            sources[node].append(src)
    for src in selection.skipped:
        if (node := node_of.get(src.path)) is not None:
            skipped[node].append(str(src.path))
    warnings = list(split.warnings)
    for node, found in sources.items():
        if not found:
            warnings.append(f"node {node} has no {fmt.name} log files: no set for it")
    members = sorted((node, found) for node, found in sources.items() if found)
    if not members:
        raise NoSourcesFound(f"no {fmt.name} log files of any node in the input")
    ids = {node: logset_id(s.xxh3 for s in found) for node, found in members}
    job_id = split.job.id if split.job is not None else None
    if job_id is not None and _LABEL.match(job_id) is None:
        # the export's own id becomes a URL path segment: only label characters are kept
        warnings.append(f"the export's job id {job_id!r} is not a valid capture id: not used")
        job_id = None
    capture = target.capture or job_id or capture_id(ids.values())
    manifests = store.list()
    cluster = target.cluster or _capture_cluster([n for n, _ in members], manifests, warnings)
    prefix = o.name or cluster or capture[:8]
    o.say(
        f"capture {capture}: {len(members)} node(s) ({split.kind} split), "
        f"format {fmt.name}, cluster {cluster or '-'}"
    )
    total = sum(s.size for _, found in members for s in found)
    done_bytes = done_entries = 0
    docs: list[Doc] = []
    created: list[str] = []  # sets this run added: removed again if it does not finish
    try:
        for node, found in members:
            old = _by_id(manifests, ids[node])
            set_name = None
            if old is None or o.overwrite:
                set_name = _free_name(f"{prefix}_{node.lower()}", ids[node], manifests, warnings)
            o.say(f"  {node}: {_size(found)}" + (f" -> {set_name}" if set_name else ""))
            result = ingest_sources(
                store,
                found,
                fmt,
                name=set_name,
                frames=o.frames,
                overwrite=o.overwrite,
                mine=o.mine,
                progress=_offset(o.progress, done_bytes, done_entries, total),
                progress_every=o.progress_every,
                node=node,
                cluster=cluster or (old.cluster if old is not None and o.overwrite else None),
                capture=capture,
            )
            if old is None:
                created.append(result.logset.manifest.id)
            m, notes = _apply_identity(
                store, result.logset.manifest, node, cluster, capture, o.front
            )
            notes += _hint_warnings(fmt, found, m, o.front, split=True)
            result = dataclasses.replace(
                result, skipped=tuple(skipped[node]), warnings=(*result.warnings, *notes)
            )
            warnings.extend(f"{node}: {w}" for w in result.warnings)
            docs.append(_document(result, m))
            done_bytes += sum(s.size for s in found)
            done_entries += result.parsed_entries
    except BaseException:
        _roll_back(store, created)
        raise
    info = get_capture(store, capture)
    return {
        "schema": "ingest-capture.v1",
        "capture": msgspec.to_builtins(info),
        "sets": docs,
        "warnings": warnings,
    }


def _roll_back(store: Store, created: Sequence[str]) -> None:
    """Remove the sets a split that failed or was cancelled had added (best effort).

    A split stores one set per node; stopped halfway it would leave a capture with
    some nodes only, which nothing reports. Sets that existed before the run (same
    files, so the same data) stay.
    """
    from atlas_log_analyzer.store.store import StoreError

    for set_id in created:
        with contextlib.suppress(StoreError):
            store.remove(set_id)


def _by_id(manifests: Sequence[LogSetManifest], set_id: str) -> LogSetManifest | None:
    return next((m for m in manifests if m.id == set_id), None)


def _node_cluster(manifests: Sequence[LogSetManifest], node: str | None) -> str | None:
    """Cluster of the newest set (``manifests`` are newest first) of ``node`` that has one."""
    if node is None:
        return None
    return next((m.cluster for m in manifests if m.node == node and m.cluster), None)


def _capture_cluster(
    nodes: Sequence[str], manifests: Sequence[LogSetManifest], warnings: list[str]
) -> str | None:
    """Return the one cluster of the earlier sets of these nodes (``None``: none or several)."""
    known = {c for node in nodes if (c := _node_cluster(manifests, node)) is not None}
    if len(known) > 1:
        warnings.append(
            f"earlier sets of these nodes belong to clusters {', '.join(sorted(known))}; "
            "the capture gets no cluster (give one with cluster)"
        )
        return None
    return next(iter(known), None)


def _free_name(
    base: str, set_id: str, manifests: Sequence[LogSetManifest], warnings: list[str]
) -> str:
    """Return ``base``, or ``base_2``, ``base_3``, … when another set has that name already."""
    taken = {m.name for m in manifests if m.name is not None and m.id != set_id}
    name, k = base, 2
    while name in taken:
        name, k = f"{base}_{k}", k + 1
    if name != base:
        warnings.append(f"name {base} is used by another set: this set is named {name}")
    return name


def _offset(progress: Progress | None, done_bytes: int, done: int, total: int) -> Progress | None:
    """Report the progress of one node's ingest as part of the whole capture."""
    if progress is None:
        return None
    from atlas_log_analyzer.store.pipeline import IngestProgress

    def report(p: IngestProgress) -> None:
        progress(
            IngestProgress(
                entries=done + p.entries,
                bytes_read=done_bytes + p.bytes_read,
                bytes_total=total,
                current_file=p.current_file,
            )
        )

    return report


def _relabel_how(front: Front) -> str:
    return "`atlas-log label`" if front == "cli" else "POST /api/sets/{ref}/label"


def _apply_identity(
    store: Store,
    m: LogSetManifest,
    node: str | None,
    cluster: str | None,
    capture: str | None,
    front: Front,
) -> tuple[LogSetManifest, list[str]]:
    """Give an existing set the labels it lacks; warn about those it has otherwise."""
    changes: dict[str, str] = {}
    warnings: list[str] = []
    for key, want in (("node", node), ("cluster", cluster), ("capture", capture)):
        have = getattr(m, key)
        if want is None or have == want:
            continue
        if have is None:
            changes[key] = want
        else:
            warnings.append(
                f"{flag(key, front)} {want} ignored: set {m.name or m.id} has {key} {have} "
                f"(change it with {_relabel_how(front)})"
            )
    if changes:
        m = store.label(m.id, **changes)
    return m, warnings


def _hint_lines(source: SourceFile) -> list[str]:
    from atlas_log_analyzer.core.reader import iter_raw_lines

    lines = iter_raw_lines(source.path)
    try:
        return [text for _, _, text in islice(lines, HINT_LINES)]
    finally:
        lines.close()


def _hint_warnings(
    fmt: LogFormat, sources: Sequence[SourceFile], m: LogSetManifest, front: Front, *, split: bool
) -> list[str]:
    """Compare the node a set is labelled with and the node its newest file names.

    Without a node (one set, no ``node`` given or recorded) a unanimous hint is only
    suggested: labels stay explicit.
    """
    from atlas_log_analyzer.core.formats.base import node_hints

    if not sources or (m.node is None and split):
        return []
    hints = node_hints(fmt, _hint_lines(sources[-1]))
    if not hints:
        return []
    top, count = max(hints.items(), key=lambda kv: (kv[1], kv[0]))
    if m.node is not None:
        if top == m.node:
            return []
        return [
            f"the log names node {top} ({count} line(s) of {sources[-1].path.name}), "
            f"but the set is labelled node {m.node}"
        ]
    if len(hints) > 1:
        return []
    how = (
        f"pass --node {top} (or `atlas-log label {m.name or m.id} --node {top}`)"
        if front == "cli"
        else f"send node={top}"
    )
    return [f"the log names node {top} ({count} line(s)); {how} to label the set"]


class Labeller(Sets, Protocol):
    """Sets that can be labelled (``Store``; the web app's ``AppState`` drops its caches)."""

    def label(
        self, ref: str, *, node: Label = ..., cluster: Label = ..., capture: Label = ...
    ) -> LogSetManifest:
        """Set (text) or clear (``None``) the identity fields of the set ``ref``."""
        ...


def label(
    sets: Labeller,
    ref: str,
    *,
    node: str | None = None,
    cluster: str | None = None,
    capture: str | None = None,
    front: Front,
) -> Doc:
    """``logset.v1`` of a set after setting its ``node``, ``cluster`` and ``capture``.

    A value of ``None`` keeps the field, an empty one clears it. The set id, its name
    and its data do not change.

    Raises:
        OpError: ``usage`` when no field is given; ``invalid_value`` for a bad value;
            reasons ``set_not_found`` / ``ambiguous_ref``.

    """
    given = {k: v for k, v in (("node", node), ("cluster", cluster), ("capture", capture))}
    values = {k: check_label(v, k, front=front) for k, v in given.items() if v is not None}
    if not values:
        names = ", ".join(flag(k, front) for k in given)
        raise OpError("usage", f"give at least one of {names} (an empty value clears it)")
    m = resolving(sets, ref, lambda r: sets.label(r, **values), front=front)
    return dict(m.to_dict())


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
    return Path(safe_name(ls.manifest.name or ls.manifest.id, ls.manifest.id) + ".html")


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
        check_count(value, name, front=front)
    check_out(out, front=front)
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
