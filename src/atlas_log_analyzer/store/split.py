"""The inputs of one ingest: ZIP archives and the split of a cluster's logs into one set per node.

A Jira Data Center cluster writes one ``atlassian-jira.log*`` family per node. The
cluster log export (one ZIP with ``job.json`` and ``<NODE>_atlassian-jira.log*``
files), a folder of ``<NODE>_`` prefixed files or one sub-directory per node become
one log set per node (docs/cluster.md, §3)::

    with expand_archives(paths, store.root / SPOOL_DIR) as inputs:
        split = plan_split(inputs, "auto")
        split.kind        # "job", "prefix", "dir" or None (one set, as before)
        split.nodes       # (NodeFiles(node="NODE1", files=(...)), ...)

Only file names, directory listings and ``job.json`` are read here; the files are
digested and parsed by the ingest pipeline.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import stat
import time
import uuid
import zlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any, Final, Literal

from atlas_log_analyzer.core.paths import windows_safe
from atlas_log_analyzer.core.reader import LOG_GLOB, iter_raw_lines

__all__ = [
    "JOB_FILE",
    "MAX_ZIP_BYTES",
    "SPLIT_MODES",
    "SPOOL_DIR",
    "ArchiveError",
    "ExportJob",
    "NodeFiles",
    "Split",
    "SplitError",
    "SplitKind",
    "SplitMode",
    "expand_archives",
    "extract_zip",
    "plan_split",
    "read_job",
    "safe_node",
]

type SplitMode = Literal["auto", "prefix", "dir", "none"]
"""``--split``: detect (``auto``), force a kind, or keep one set (``none``)."""
type SplitKind = Literal["job", "prefix", "dir"]
"""How the nodes were told apart: ``job.json`` names, file prefixes or directories."""

SPLIT_MODES: Final[tuple[SplitMode, ...]] = ("auto", "prefix", "dir", "none")
SPOOL_DIR: Final = ".spool"
"""Directory in the store root where ZIP inputs are extracted (ignored by listings)."""
JOB_FILE: Final = "job.json"
"""Metadata of a cluster log export: ``{"jobId", "nodes": {"results": [{"node", ...}]}}``."""
MAX_ZIP_BYTES: Final = 32 << 30
"""Most bytes all ZIP inputs of one ingest may extract to (32 GiB)."""

_CHUNK = 1 << 20
_NODE_RE = re.compile(r"[^\w.-]+")
_PREFIX_RE = re.compile(r"^(?P<node>.*[^\W\d_].*?)_(?P<base>[^_]*\.log[^_]*)$")
"""``<NODE>_<log name>``: the node id holds a letter (no dates) and ends at the last ``_``."""
_ROTATED_RE = re.compile(r"\.\d+$")
_SNIFF_LINES = 200
"""Lines of a file a format must recognise one of (as ``store.pipeline.SNIFF_LINES``)."""
_DEPTH = 2
"""Levels below an input directory without log files searched for log directories."""


class ArchiveError(ValueError):
    """A ZIP input that cannot be extracted safely (unsafe path, too large, unreadable)."""


class SplitError(ValueError):
    """A forced ``--split`` mode that does not fit the input."""


def safe_node(text: str) -> str:
    """Turn a file prefix or directory name into a node id (other characters become ``_``)."""
    return _NODE_RE.sub("_", text).strip("_.") or "node"


# --- ZIP inputs


def _is_zip(path: Path) -> bool:
    return path.suffix.lower() == ".zip" and path.is_file()


def _member_parts(name: str) -> list[str]:
    """Path parts of a ZIP member; ``ArchiveError`` for an absolute or escaping path."""
    text = name.replace("\\", "/")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if text.startswith("/") or any(p == ".." or ":" in p for p in parts):
        raise ArchiveError(f"unsafe path in ZIP: {name!r} (absolute, drive or '..')")
    return [windows_safe(p) for p in parts]


def _wanted(name: str) -> bool:
    return name == JOB_FILE or fnmatch.fnmatch(name, LOG_GLOB)


def _regular(info: Any) -> bool:
    if info.is_dir():
        return False
    kind = stat.S_IFMT(info.external_attr >> 16)  # 0: no Unix mode (written on Windows)
    return kind in (0, stat.S_IFREG)


def extract_zip(archive: Path, target: Path, *, budget: list[int]) -> int:
    """Extract ``job.json`` and the log files (:data:`LOG_GLOB`) of ``archive`` into ``target``.

    Directories inside the archive are kept; other members, directories, links and
    duplicates are skipped. ``budget[0]`` is the number of bytes still allowed and is
    reduced by what is written.

    Returns:
        The number of files extracted.

    Raises:
        ArchiveError: A member path is absolute, has a drive or ``..``; a member is
            encrypted; the archive is unreadable; or the members exceed the budget.

    """
    import zipfile

    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"{archive.name} is not a readable ZIP file: {exc}") from exc
    with zf:
        members: list[tuple[Any, list[str]]] = []
        seen: set[tuple[str, ...]] = set()
        for info in zf.infolist():
            parts = _member_parts(info.filename)
            if not parts or not _regular(info) or not _wanted(parts[-1]):
                continue
            key = tuple(p.lower() for p in parts)
            if key in seen:
                continue
            seen.add(key)
            if info.flag_bits & 0x1:
                raise ArchiveError(f"{archive.name}: {info.filename} is encrypted")
            members.append((info, parts))
        declared = sum(info.file_size for info, _ in members)
        if declared > budget[0]:
            raise ArchiveError(_too_large(archive, declared))
        for info, parts in members:
            dest = target.joinpath(*parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with zf.open(info) as src, dest.open("wb") as out:
                while True:
                    try:
                        chunk = src.read(_CHUNK)
                    except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
                        raise ArchiveError(
                            f"{archive.name}: cannot extract {info.filename}: {exc}"
                        ) from exc
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > budget[0]:
                        raise ArchiveError(_too_large(archive, written))
                    out.write(chunk)
            budget[0] -= written
            with suppress(OverflowError, ValueError, OSError):
                mtime = time.mktime((*info.date_time, 0, 0, -1))
                os.utime(dest, (mtime, mtime))
    return len(members)


def _too_large(archive: Path, size: int) -> str:
    return (
        f"{archive.name}: the ZIP inputs extract to more than {MAX_ZIP_BYTES >> 30} GiB "
        f"(at least {size / (1 << 30):.1f} GiB); extract them yourself and ingest the folder"
    )


def _descend(root: Path) -> Path:
    """Follow single sub-directories down to where the files are (``export/…`` in a ZIP)."""
    current = root
    while True:
        children = list(current.iterdir())
        dirs = [c for c in children if c.is_dir()]
        if len(children) == 1 and len(dirs) == 1:
            current = dirs[0]
        else:
            return current


@contextmanager
def expand_archives(
    paths: Sequence[Path], spool: Path, *, max_bytes: int = MAX_ZIP_BYTES
) -> Iterator[list[Path]]:
    """Yield ``paths`` with every ``.zip`` file replaced by the directory it was extracted to.

    Each archive goes to ``<spool>/<random>/<archive stem>/`` (so the stem names the
    directory, as a node directory with ``--split dir``); all of it is removed when the
    block ends, also after an error. Paths that are no ZIP file pass unchanged.

    Raises:
        ArchiveError: See :func:`extract_zip`; ``max_bytes`` caps all archives together.

    """
    if not any(_is_zip(p) for p in paths):
        yield list(paths)
        return
    root = spool / uuid.uuid4().hex[:12]
    budget = [max_bytes]
    archives: dict[str, str] = {}  # extracted directory -> the ZIP, for error messages
    try:
        out: list[Path] = []
        used: set[str] = set()
        for path in paths:
            if not _is_zip(path):
                out.append(path)
                continue
            stem = safe_node(path.stem)
            while stem.lower() in used:
                stem = f"{stem}_"
            used.add(stem.lower())
            target = root / stem
            target.mkdir(parents=True)
            archives[str(target)] = archives[str(target.resolve())] = str(path)
            extract_zip(path, target, budget=budget)
            out.append(_descend(target))
        yield out
    except Exception as exc:
        _name_archives(exc, archives)
        raise
    finally:
        shutil.rmtree(root, ignore_errors=True)
        with suppress(OSError):
            spool.rmdir()  # only when empty: another ingest may use it


def _name_archives(exc: Exception, archives: dict[str, str]) -> None:
    """Replace extracted paths in the message of ``exc`` by their ZIP (the spool is gone)."""
    if len(exc.args) != 1 or not isinstance(exc.args[0], str) or str(exc) != exc.args[0]:
        return
    text = exc.args[0]
    for extracted in sorted(archives, key=len, reverse=True):
        text = text.replace(extracted, archives[extracted])
    if text != exc.args[0]:
        if getattr(exc, "message", None) == exc.args[0]:
            exc.message = text  # type: ignore[attr-defined]
        exc.args = (text,)


# --- job.json


@dataclass(frozen=True, slots=True)
class ExportJob:
    """The ``job.json`` of a cluster log export."""

    id: str | None
    """``jobId``: the capture id of the export."""
    nodes: tuple[tuple[str, str | None, str | None], ...]
    """``(node, status, errorMessage)`` per ``nodes.results`` item, in file order."""


def read_job(path: Path) -> ExportJob | None:
    """Read ``job.json``; ``None`` when it is not JSON or has neither a job id nor nodes."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError, ValueError:
        return None
    if not isinstance(data, dict):
        return None
    job_id = data.get("jobId")
    job_id = job_id.strip() if isinstance(job_id, str) and job_id.strip() else None
    nodes = data.get("nodes")
    results = nodes.get("results") if isinstance(nodes, dict) else None
    found: list[tuple[str, str | None, str | None]] = []
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict):
            continue
        node = item.get("node")
        if isinstance(node, str) and node.strip():
            status, error = item.get("status"), item.get("errorMessage")
            found.append(
                (
                    node.strip(),
                    str(status) if status is not None else None,
                    str(error) if error not in (None, "") else None,
                )
            )
    if job_id is None and not found:
        return None
    return ExportJob(job_id, tuple(found))


# --- the split


@dataclass(frozen=True, slots=True)
class NodeFiles:
    """The log files of one node."""

    node: str
    files: tuple[Path, ...]
    """Resolved paths, as :func:`~atlas_log_analyzer.core.reader.discover_sources` gives them."""


@dataclass(frozen=True, slots=True)
class Split:
    """How the inputs of one ingest map to nodes (:func:`plan_split`)."""

    kind: SplitKind | None
    """``None``: no split, the inputs are one set."""
    nodes: tuple[NodeFiles, ...]
    """Nodes in input order; empty without a split."""
    paths: tuple[Path, ...]
    """What to discover the sources in: the inputs (or the node directories), never
    ``job.json`` itself."""
    job: ExportJob | None = None
    """The export's ``job.json``, when one was found."""
    warnings: tuple[str, ...] = ()

    def node_of(self) -> dict[Path, str]:
        """Node of each file of :attr:`nodes`."""
        return {path: n.node for n in self.nodes for path in n.files}


def _logs(directory: Path) -> list[Path]:
    return sorted(c.resolve() for c in directory.glob(LOG_GLOB) if c.is_file())


def _subdirs(directory: Path) -> list[Path]:
    try:
        children = list(directory.iterdir())
    except OSError:
        return []
    return sorted(c for c in children if c.is_dir() and not c.name.startswith("."))


def _recognised(path: Path) -> bool:
    """Whether a registered format recognises one of the first lines of ``path``."""
    from atlas_log_analyzer.core import formats

    lines = iter_raw_lines(path)
    try:
        head = [text for _, _, text in islice(lines, _SNIFF_LINES)]
    except OSError:
        return False
    finally:
        lines.close()
    return formats.detect(head) is not None


def _holds_logs(directory: Path) -> bool:
    """Whether ``directory`` holds a log file (:data:`LOG_GLOB`) that a format recognises."""
    return any(_recognised(f) for f in _logs(directory))


def _log_dirs(directory: Path, depth: int) -> list[Path]:
    """``directory`` when it holds log files, else the log directories ``depth`` levels below."""
    if _holds_logs(directory):
        return [directory]
    if depth <= 0:
        return []
    return [d for c in _subdirs(directory) for d in _log_dirs(c, depth - 1)]


def _groups(directory: Path) -> dict[Path, list[Path]]:
    """Log directories below ``directory``, by the sub-directory (a node candidate) they are in.

    A sub-directory counts when it, or one of its own sub-directories, holds log files
    a format recognises: ``node1/`` or ``node1/application-logs/``, not ``tomcat-logs/``.
    """
    groups: dict[Path, list[Path]] = {}
    for child in _subdirs(directory):
        if found := _log_dirs(child, _DEPTH - 1):
            groups[child] = found
    return groups


@dataclass(frozen=True, slots=True)
class _Inputs:
    files: list[Path]
    """Log files given, or found in :attr:`search`."""
    dirs: list[Path]
    """The directories given."""
    jobs: list[Path]
    """``job.json`` files given, in a directory given or next to its log directories."""
    search: list[Path]
    """What to discover the sources in: files given, and the log directories."""
    found: dict[Path, list[Path]]
    """Log directories of each directory given (itself when it holds log files or none)."""
    groups: dict[Path, dict[Path, list[Path]]]
    """Of a directory given that holds no log files: its log directories by sub-directory."""


def _inputs(paths: Sequence[Path]) -> _Inputs:
    """Sort the inputs; a directory without log files is searched :data:`_DEPTH` levels down.

    A support ZIP holds ``application-logs/`` beside ``tomcat-logs/``, a ZIP of several
    nodes ``node1/application-logs/``, an export ``job.json`` beside ``logs/``.
    """
    files: dict[Path, None] = {}
    dirs: list[Path] = []
    jobs: list[Path] = []
    search: list[Path] = []
    found: dict[Path, list[Path]] = {}
    groups: dict[Path, dict[Path, list[Path]]] = {}
    for path in paths:
        if path.is_dir():
            dirs.append(path)
            below = {} if _holds_logs(path) else _groups(path)
            log_dirs = [d for ds in below.values() for d in ds] or [path]
            if below:
                groups[path] = below
            found[path] = log_dirs
            for d in log_dirs:
                search.append(d)
                files.update(dict.fromkeys(_logs(d)))
            for d in (path, *log_dirs, *(d.parent for d in log_dirs)):
                if (job := d / JOB_FILE).is_file() and job not in jobs:
                    jobs.append(job)
        elif path.is_file() and path.name.lower() == JOB_FILE:
            jobs.append(path)
        else:
            if path.is_file():
                files[path.resolve()] = None
            search.append(path)  # a missing path fails in discover_sources
    return _Inputs(list(files), dirs, jobs, search, found, groups)


def _names(paths: Sequence[Path], limit: int = 5) -> str:
    shown = ", ".join(p.name for p in paths[:limit])
    return shown + (f" and {len(paths) - limit} more" if len(paths) > limit else "")


def _by_job(files: list[Path], job: ExportJob) -> tuple[list[NodeFiles], list[Path], list[str]]:
    names = sorted({n for n, _, _ in job.nodes}, key=len, reverse=True)
    assigned: dict[str, list[Path]] = {}
    rest: list[Path] = []
    for f in files:
        node = next((n for n in names if f.name.startswith(n + "_")), None)
        if node is None:
            rest.append(f)
        else:
            assigned.setdefault(node, []).append(f)
    warnings = []
    for node, status, error in job.nodes:
        if status is not None and status.upper() != "OK":
            why = f": {error}" if error else ""
            warnings.append(f"{JOB_FILE}: node {node} reported status {status}{why}")
        elif node not in assigned:
            warnings.append(f"{JOB_FILE}: node {node} has no log files in the export")
    order = [n for n, _, _ in job.nodes if n in assigned]
    nodes = [NodeFiles(n, tuple(assigned[n])) for n in dict.fromkeys(order)]
    return nodes, rest, warnings


def _by_prefix(files: list[Path]) -> tuple[list[NodeFiles], list[Path], set[str]]:
    """Group ``<NODE>_<log>`` files by node; also return the log bases several nodes share."""
    assigned: dict[str, list[Path]] = {}
    bases: dict[str, set[str]] = {}
    rest: list[Path] = []
    for f in files:
        m = _PREFIX_RE.match(f.name)
        if m is None:
            rest.append(f)
            continue
        node = safe_node(m["node"])
        assigned.setdefault(node, []).append(f)
        bases.setdefault(_ROTATED_RE.sub("", m["base"]).lower(), set()).add(node)
    shared = {base for base, nodes in bases.items() if len(nodes) > 1}
    return [NodeFiles(n, tuple(fs)) for n, fs in assigned.items()], rest, shared


def _dir_node(directory: Path, files: Sequence[Path]) -> str:
    """Node id of a node directory: the ``<NODE>_`` prefix all its files share, else its name."""
    prefixes = {m["node"] if (m := _PREFIX_RE.match(f.name)) else None for f in files}
    if len(prefixes) == 1 and (prefix := prefixes.pop()) is not None:
        return safe_node(prefix)
    return safe_node(directory.resolve().name)


def _by_dirs(groups: dict[Path, list[Path]]) -> tuple[list[NodeFiles], tuple[Path, ...]]:
    """One node per node directory whose log directories hold log files; also return those."""
    nodes: dict[str, NodeFiles] = {}
    used: list[Path] = []
    for directory, log_dirs in groups.items():
        files = sorted({f for d in log_dirs for f in _logs(d)})
        if not files:
            continue
        node = _dir_node(directory, files)
        if node in nodes:
            raise SplitError(f"two node directories are named {node}: give each node its own name")
        nodes[node] = NodeFiles(node, tuple(files))
        used.extend(log_dirs)
    return list(nodes.values()), tuple(used)


def _unassigned(rest: list[Path]) -> tuple[str, ...]:
    if not rest:
        return ()
    return (f"{len(rest)} file(s) belong to no node and are not read: {_names(rest)}",)


def plan_split(paths: Sequence[Path], mode: SplitMode = "auto") -> Split:
    """Decide whether the inputs hold several nodes and which files belong to which.

    A directory given that holds no log file a registered format recognises is
    searched two levels down: its sub-directories that hold such files, or whose
    own sub-directories do, are its log directories (a support ZIP's
    ``application-logs/`` but not ``tomcat-logs/``; ``node1/application-logs/``; an
    export's ``logs/`` beside ``job.json``). ``job.json`` is also looked for next to
    the log directories.

    ``auto`` tries, in order:

    1. ``job``: a ``job.json`` (in an input directory, or given) names the nodes, and
       files are named ``<NODE>_<log name>`` after them (the cluster log export);
    2. ``prefix``: files named ``<NODE>_<log name>`` with at least two node ids that
       share a log name (``NODE1_atlassian-jira.log``, ``NODE2_atlassian-jira.log.1``);
       a node id holds a letter and ends at the name's last ``_``;
    3. ``dir``: one input directory without log files of its own whose (at least two)
       sub-directories hold log files (themselves or one level down), one node per
       sub-directory (named after the ``<NODE>_`` prefix all its files share, else
       after the sub-directory).

    Otherwise the inputs stay one set (a single sub-directory with log files is that
    set). ``prefix`` and ``dir`` force that kind (``dir`` also takes several input
    directories, one per node); ``none`` never splits. Files that belong to no node
    are left out with a warning. A ``job.json`` is never read as a log file, and its
    ``jobId`` is kept unless the mode is ``none``.

    Raises:
        SplitError: A forced mode finds no node, or two node directories share a name.

    """
    inputs = _inputs(paths)
    files, dirs = inputs.files, inputs.dirs
    search = tuple(inputs.search)
    if mode == "none":
        return Split(None, (), search)
    job = next((j for j in map(read_job, inputs.jobs) if j is not None), None)
    warnings: list[str] = []
    if mode == "auto" and job is not None and job.nodes:
        nodes, rest, notes = _by_job(files, job)
        if nodes:
            return Split("job", tuple(nodes), search, job, (*notes, *_unassigned(rest)))
        names = ", ".join(n for n, _, _ in job.nodes)
        warnings.append(f"{JOB_FILE} names nodes {names}, but no log file is named after them")
    # node directories (two or more sub-directories with log files) win over prefixes
    nested = len(paths) == 1 and len(dirs) == 1 and len(inputs.groups.get(dirs[0], {})) > 1
    if mode == "prefix" or (mode == "auto" and not nested):
        nodes, rest, shared = _by_prefix(files)
        if (mode == "prefix" and nodes) or (len(nodes) > 1 and shared):
            return Split("prefix", tuple(nodes), search, job, (*warnings, *_unassigned(rest)))
        if mode == "prefix":
            raise SplitError(
                "--split prefix: no log file is named <NODE>_<log name> (e.g. "
                "NODE1_atlassian-jira.log)"
            )
    if mode == "dir":
        if len(dirs) > 1:
            groups = {d: inputs.found[d] for d in dirs}
        else:
            groups = (inputs.groups.get(dirs[0]) or _groups(dirs[0])) if dirs else {}
        nodes, node_dirs = _by_dirs(groups)
        if not nodes:
            raise SplitError(
                "--split dir: give one directory per node, or one directory whose "
                "sub-directories (one per node) hold the log files"
            )
        taken = {f for n in nodes for f in n.files}
        rest = [f for f in files if f not in taken]
        return Split("dir", tuple(nodes), node_dirs, job, (*warnings, *_unassigned(rest)))
    if mode == "auto" and nested:
        nodes, node_dirs = _by_dirs(inputs.groups[dirs[0]])
        return Split("dir", tuple(nodes), node_dirs, job, tuple(warnings))
    return Split(None, (), search, job, tuple(warnings))
