"""The :class:`Store`: a directory holding log sets."""

from __future__ import annotations

import builtins
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

import xxhash

from atlas_log_analyzer import __version__
from atlas_log_analyzer.core import formats
from atlas_log_analyzer.core.formats.base import LogFormat, banners_since
from atlas_log_analyzer.core.grouping import Group, SignedEntry
from atlas_log_analyzer.core.masking import MASK_RULES_VERSION
from atlas_log_analyzer.core.pipeline import Recipe, format_version, key_format_version
from atlas_log_analyzer.core.reader import SourceFile
from atlas_log_analyzer.core.signature import SIGNATURE_VERSION, SignatureSpec
from atlas_log_analyzer.store.locks import FileLock
from atlas_log_analyzer.store.manifest import (
    ENTRIES_FILE,
    GROUPS_FILE,
    MANIFEST_FILE,
    LogSetManifest,
    SourceInfo,
    current_recipe_key,
)
from atlas_log_analyzer.store.schema import SCHEMA_VERSION

if TYPE_CHECKING:
    from atlas_log_analyzer.store.logset import LogSet
    from atlas_log_analyzer.store.writer import SourceStats

__all__ = [
    "MIN_PREFIX",
    "UNSET",
    "AmbiguousLogSet",
    "Label",
    "LogSetNotFound",
    "Store",
    "StoreError",
    "Unset",
    "UpgradeResult",
    "logset_id",
]

MIN_PREFIX = 4
"""Minimum length of an id prefix accepted by :meth:`Store.open`."""
WRITE_LOCK_FILE = ".write.lock"
"""Lock file in the store root, held while a set is renamed into place or removed."""
WRITE_LOCK_TIMEOUT_S = 30.0
"""How long a write waits for another process's install or removal to finish."""
RETRY_DELAYS_S = (0.05, 0.1, 0.2, 0.4, 0.8)
"""Back-off between attempts to rename or delete a set directory that is in use.

On Windows a directory cannot be renamed or deleted while a reader has one of its
files open; such readers are short-lived (one query), so a few retries suffice.
"""


class Unset(Enum):
    """Type of :data:`UNSET`: a keyword of :meth:`Store.label` that was not given."""

    UNSET = "UNSET"


UNSET: Final = Unset.UNSET
"""Leave a field of :meth:`Store.label` as it is (``None`` clears it)."""

type Label = str | Literal[Unset.UNSET] | None
"""A value of :meth:`Store.label`: the new text, ``None`` (clear) or :data:`UNSET` (keep)."""


class StoreError(Exception):
    """Any store-level failure (missing set, corrupt manifest, version mismatch)."""


class LogSetNotFound(StoreError):
    """:meth:`Store.open` found no log set for the reference."""


class AmbiguousLogSet(StoreError):
    """:meth:`Store.open` found several log sets for a name or id prefix."""


def logset_id(digests: Iterable[str]) -> str:
    """Log-set id: xxh3-64 hex of the LF-joined sorted source digests."""
    return xxhash.xxh3_64_hexdigest("\n".join(sorted(digests)).encode())


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _source_infos(
    sources: list[SourceFile], stats: dict[str, SourceStats]
) -> tuple[SourceInfo, ...]:
    """Match writer statistics (keyed by ``entry.source.path``) to the input files."""
    from atlas_log_analyzer.store.split import shown_path
    from atlas_log_analyzer.store.writer import SourceStats

    normalised = {_norm(k): v for k, v in stats.items()}
    out = []
    for sf in sources:
        shown = shown_path(str(sf.path))  # what the entries record (zip/member)
        st = stats.get(shown) or normalised.get(_norm(shown)) or SourceStats()
        out.append(
            SourceInfo(
                path=shown,
                size=sf.size,
                xxh3=sf.xxh3,
                first_ts=st.first_ts,
                last_ts=st.last_ts,
                entries=st.entries,
            )
        )
    return tuple(out)


def _restat(
    sources: tuple[SourceInfo, ...], stats: dict[str, SourceStats]
) -> tuple[SourceInfo, ...]:
    """Return the manifest's sources with entry counts and time ranges from ``stats``."""
    from atlas_log_analyzer.store.writer import SourceStats

    normalised = {_norm(k): v for k, v in stats.items()}
    out = []
    for info in sources:
        st = stats.get(info.path) or normalised.get(_norm(info.path)) or SourceStats()
        out.append(replace(info, entries=st.entries, first_ts=st.first_ts, last_ts=st.last_ts))
    return tuple(out)


def _spool_renames(ls: LogSet) -> dict[str, str]:
    """Map the set's source paths in a removed ZIP spool to their ``<zip>/<member>`` names."""
    import polars as pl

    from atlas_log_analyzer.store.split import unspooled

    listed = [s.path for s in ls.manifest.sources]
    stored = (
        pl.scan_parquet(ls.entries_path)
        .select(pl.col("source_path").cast(pl.String).unique())
        .collect()["source_path"]
        .to_list()
    )
    known = [p for p in listed if unspooled(p) is None]
    out: dict[str, str] = {}
    for path in {*listed, *(p for p in stored if p is not None)}:
        name = unspooled(path, known)
        if name is not None:
            out[path] = name
    return out


LEFTOVER_AGE_S = 24 * 3600
"""Age after which a ``.tmp-*``/``.trash-*`` directory counts as left over (see ``_sweep``)."""


def _retry[T](action: Callable[[], T]) -> T:
    """Run ``action``, retrying after :data:`RETRY_DELAYS_S` while it raises ``PermissionError``."""
    for delay in RETRY_DELAYS_S:
        try:
            return action()
        except PermissionError:
            time.sleep(delay)
    return action()


def _remove_tree(path: Path) -> None:
    """Delete ``path`` with retries; a leftover ``.trash-*`` directory is ignored by readers."""
    try:
        _retry(lambda: shutil.rmtree(path))
    except OSError:
        shutil.rmtree(path, ignore_errors=True)


def _logset(path: Path, manifest: LogSetManifest) -> LogSet:
    from atlas_log_analyzer.store.logset import LogSet  # loads polars

    return LogSet(path, manifest)


def _time_range(stats: Iterable[SourceStats]) -> tuple[datetime | None, datetime | None]:
    firsts = [s.first_ts for s in stats if s.first_ts is not None]
    lasts = [s.last_ts for s in stats if s.last_ts is not None]
    return (min(firsts) if firsts else None, max(lasts) if lasts else None)


@dataclass(frozen=True, slots=True)
class UpgradeResult:
    """Outcome of :meth:`Store.upgrade` for one set."""

    manifest: LogSetManifest
    """The set's manifest after the call (unchanged when skipped)."""
    upgraded: bool
    reason: str | None
    """Why the set was skipped: ``"current"`` or an unknown format/spec; ``None`` if upgraded."""
    previous_recipe_key: str
    renamed_sources: int = 0
    """Source paths renamed from a removed ZIP spool to ``<zip>/<member>``."""


class Store:
    """Directory of log sets; creates it on first write.

    Every log set lives in ``<root>/<id>/``. Directories whose names start with
    ``.`` are temporary (in-progress writes) and ignored by readers.

    One instance may be shared by threads. Reads take no lock beyond a short one
    around the manifest cache; a write locks the store (against other processes
    too, see :data:`WRITE_LOCK_FILE`) only for its final name check and rename.
    Listing reads only manifests (no polars); :meth:`open` builds the
    :class:`~atlas_log_analyzer.store.logset.LogSet`.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._index: dict[Path, tuple[int, int, LogSetManifest | None]] = {}
        """Manifest cache: set dir → (manifest mtime_ns, size, manifest or None if invalid)."""
        self._index_lock = threading.Lock()
        """Guards :attr:`_index`; manifests are read outside it."""
        self._write_lock = threading.Lock()
        """Serialises the installs and removals of this instance (see :meth:`_writing`)."""

    def _cached(self, d: Path) -> LogSetManifest | None:
        """Read the manifest of ``d`` through the cache; ``None`` when unreadable or invalid."""
        try:
            st = (d / MANIFEST_FILE).stat()
        except OSError:
            with self._index_lock:
                self._index.pop(d, None)
            return None
        with self._index_lock:
            hit = self._index.get(d)
        if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2]
        try:
            m: LogSetManifest | None = LogSetManifest.read(d)
        except OSError, ValueError:
            m = None
        with self._index_lock:
            self._index[d] = (st.st_mtime_ns, st.st_size, m)
        return m

    def _set_dirs(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return [
            p
            for p in self.root.iterdir()
            if p.is_dir() and not p.name.startswith(".") and (p / MANIFEST_FILE).is_file()
        ]

    def _load_all(self) -> list[tuple[Path, LogSetManifest]]:
        dirs = self._set_dirs()
        live = set(dirs)
        with self._index_lock:
            for stale in [d for d in self._index if d not in live]:
                del self._index[stale]
        # corrupt or newer-schema sets are skipped by listings
        return [(d, m) for d in dirs if (m := self._cached(d)) is not None]

    def list(self) -> list[LogSetManifest]:
        """Manifests of all log sets, newest first.

        Sets whose manifest is unreadable or uses a newer schema are skipped. Only
        the (cached) ``manifest.json`` files are read, so listing does not load polars.
        """
        manifests = [m for _, m in self._load_all()]
        return sorted(manifests, key=lambda m: m.created_at, reverse=True)

    def set_dir(self, set_id: str) -> Path:
        """Directory of the set with id ``set_id`` (whether or not it exists)."""
        return self.root / set_id

    def manifests(self) -> builtins.list[LogSetManifest]:
        """Return :meth:`list` (the ``Sets`` protocol of :mod:`atlas_log_analyzer.service`)."""
        return self.list()

    def open(self, ref: str) -> LogSet:
        """Open a log set by id, unique id prefix, or name.

        Resolution order: exact id, then exact name, then id prefix of at least
        :data:`MIN_PREFIX` characters. Several matches at the same step are an error.
        Manifests are cached per :class:`Store` instance and re-read when their
        file changes, so repeated calls do not reparse every manifest.

        Raises:
            LogSetNotFound: Nothing matches ``ref``.
            AmbiguousLogSet: Several sets share the name or id prefix.
            StoreError: The set with id ``ref`` exists but cannot be opened.

        """
        return _logset(*self._resolve(ref))

    def _resolve(self, ref: str) -> tuple[Path, LogSetManifest]:
        """Directory and manifest of the set ``ref`` (see :meth:`open`), without polars."""
        # one plain path part only: no "..", no separators, no drive ("C:x" is relative to
        # drive C's current directory on Windows, outside the store)
        plain = bool(ref) and not ref.startswith(".") and not any(c in ref for c in "/\\:")
        exact = self.root / ref if plain else self.root
        if plain and (exact / MANIFEST_FILE).is_file():
            m = self._cached(exact)
            return exact, (m if m is not None else self._read(exact))
        sets = self._load_all()
        by_name = [(d, m) for d, m in sets if m.name == ref]
        if len(by_name) == 1:
            return by_name[0]
        if len(by_name) > 1:
            ids = ", ".join(m.id for _, m in by_name)
            raise AmbiguousLogSet(f"name {ref!r} is ambiguous: {ids}")
        if len(ref) >= MIN_PREFIX:
            by_prefix = [(d, m) for d, m in sets if m.id.startswith(ref.lower())]
            if len(by_prefix) == 1:
                return by_prefix[0]
            if len(by_prefix) > 1:
                ids = ", ".join(m.id for _, m in by_prefix)
                raise AmbiguousLogSet(f"id prefix {ref!r} is ambiguous: {ids}")
        raise LogSetNotFound(f"no log set {ref!r} in {self.root}")

    def manifest(self, ref: str) -> LogSetManifest:
        """Manifest of the set ``ref`` (resolved as by :meth:`open`), without loading polars.

        Raises:
            LogSetNotFound: Nothing matches ``ref``.
            AmbiguousLogSet: Several sets share the name or id prefix.
            StoreError: The set with id ``ref`` exists but cannot be read.

        """
        return self._resolve(ref)[1]

    def label(
        self,
        ref: str,
        *,
        node: Label = UNSET,
        cluster: Label = UNSET,
        capture: Label = UNSET,
    ) -> LogSetManifest:
        """Set or clear the identity fields of a set; its id, name and data stay as they are.

        A field left at :data:`UNSET` keeps its value, ``None`` clears it. The manifest
        is rewritten in place (temporary file, then rename) under the store's write lock.

        Raises:
            LogSetNotFound: Nothing matches ``ref``.
            AmbiguousLogSet: Several sets share the name or id prefix.
            StoreError: The manifest cannot be read or rewritten.

        """
        path, _ = self._resolve(ref)
        with self._writing():
            m = self._read(path)  # not the cache: another process may have labelled it
            updated = replace(
                m,
                node=m.node if node is UNSET else node,
                cluster=m.cluster if cluster is UNSET else cluster,
                capture=m.capture if capture is UNSET else capture,
            )
            if updated != m:
                try:
                    _retry(lambda: updated.replace_in(path))
                except OSError as exc:
                    raise StoreError(f"cannot label log set {m.id}: {exc}") from exc
        with self._index_lock:
            self._index.pop(path, None)
        return updated

    @staticmethod
    def _read(path: Path) -> LogSetManifest:
        try:
            return LogSetManifest.read(path)
        except (OSError, ValueError) as exc:
            raise StoreError(f"cannot open log set {path.name}: {exc}") from exc

    @staticmethod
    def _load(path: Path) -> LogSet:
        return _logset(path, Store._read(path))

    def _tmp_dir(self, set_id: str) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        self._sweep()
        tmp = self.root / f".tmp-{set_id}-{uuid.uuid4().hex[:8]}"
        tmp.mkdir()
        return tmp

    def _sweep(self) -> None:
        """Delete what a killed write or ingest left a day ago or more.

        That is ``.tmp-*``/``.trash-*`` directories and the entries of the ZIP spool
        (``.spool/<id>``, extracted when the ingest started). A running write touches
        its own directory far more often than that; readers already skip these
        directories, this only gives the disk space back.
        """
        from atlas_log_analyzer.store.split import SPOOL_DIR

        cutoff = time.time() - LEFTOVER_AGE_S
        spool = self.root / SPOOL_DIR
        for d in [*self.root.iterdir(), *(spool.iterdir() if spool.is_dir() else ())]:
            if d.parent == self.root and not d.name.startswith((".tmp-", ".trash-")):
                continue
            try:
                if d.is_dir() and d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                continue
        with suppress(OSError):
            spool.rmdir()  # only when empty: another ingest may use it

    @contextmanager
    def _writing(self) -> Iterator[None]:
        """Hold the store's write lock: this instance's, then :data:`WRITE_LOCK_FILE`.

        The file lock keeps another process (a CLI ingest next to ``serve``) from
        installing a set under the same name at the same moment. When the lock
        file cannot be created or locked (read-only store, a file system without
        locks) only the in-process lock is held; the rename then reports the error.
        """
        with self._write_lock:
            lock = FileLock(self.root / WRITE_LOCK_FILE)
            try:
                locked = lock.acquire(timeout=WRITE_LOCK_TIMEOUT_S)
            except OSError:  # includes LockUnavailable
                locked = None
            if locked is False:
                raise StoreError(
                    f"store {self.root} is locked by another writer "
                    f"(waited {WRITE_LOCK_TIMEOUT_S:g} s)"
                )
            try:
                yield
            finally:
                lock.release()

    def _check_name(self, name: str | None, set_id: str) -> None:
        if name is None:
            return
        for m in self.list():
            if m.name == name and m.id != set_id:
                raise StoreError(f"name {name!r} is already used by log set {m.id}")

    def _trash(self, set_id: str) -> Path:
        return self.root / f".trash-{set_id}-{uuid.uuid4().hex[:8]}"

    def _install(self, tmp: Path, set_id: str) -> Path:
        """Move ``tmp`` to ``<root>/<set_id>``, replacing an existing set; hold :meth:`_writing`."""
        final = self.root / set_id
        trash: Path | None = None
        if final.exists():
            trash = self._trash(set_id)
            _retry(lambda: os.replace(final, trash))
        try:
            _retry(lambda: os.replace(tmp, final))
        except OSError:
            if trash is not None:
                _retry(lambda: os.replace(trash, final))
            raise
        if trash is not None:
            _remove_tree(trash)
        return final

    def ingest(
        self,
        signed: Iterable[SignedEntry],
        groups_after: Callable[[], Iterable[Group]],
        sources: Iterable[SourceFile],
        fmt: LogFormat,
        *,
        name: str | None = None,
        spec: SignatureSpec,
        overwrite: bool = False,
        dedupe: bool = True,
        recipe: Recipe | None = None,
        node: str | None = None,
        cluster: str | None = None,
        capture: str | None = None,
    ) -> LogSet:
        """Write a new log set.

        ``signed`` is consumed once, streamed into ``entries.parquet`` in
        batches with deduplication on ``entry.id`` (pass ``dedupe=False`` when the
        ids are unique already, e.g. :func:`~atlas_log_analyzer.store.writer.
        unique_entries` ran before the grouper). ``groups_after`` is called
        after the stream is exhausted (the grouper has seen everything by then)
        and its result written to ``groups.parquet``; if the group counts do not
        add up to the number of stored (deduplicated) entries, ``groups.parquet``
        is recomputed from ``entries.parquet`` instead. The log-set id is derived
        from the source digests; an existing set with the same id is left
        untouched unless ``overwrite`` is true.

        When the set already exists and ``overwrite`` is false, the existing
        :class:`LogSet` is returned immediately: ``signed`` is not iterated and
        ``groups_after`` is not called (pass a lazy iterator so no parsing happens),
        and ``name`` is ignored.

        Everything is written to a temporary directory inside the store and
        renamed into place at the end, so a failed ingest leaves no partial set.
        The manifest records ``recipe.key`` (default ``Recipe(fmt, spec)``): the
        recipe the caller signed ``signed`` with, and the identity ``node``, ``cluster``
        and ``capture`` (ignored, like ``name``, when the set exists; see :meth:`label`).

        Raises:
            StoreError: No sources were given, or ``name`` is already used by
                another log set.

        """
        # the writer (pyarrow.compute) is loaded only by commands that write
        from atlas_log_analyzer.store.writer import EntriesWriter, groups_from_entries, write_groups

        src_list = list(sources)
        if not src_list:
            raise StoreError("no sources to ingest")
        set_id = logset_id(s.xxh3 for s in src_list)
        existing = self.root / set_id
        if (existing / MANIFEST_FILE).is_file() and not overwrite:
            return self._load(existing)
        self._check_name(name, set_id)  # fail before parsing; checked again before the rename
        tmp = self._tmp_dir(set_id)
        try:
            with EntriesWriter(tmp / ENTRIES_FILE, dedupe=dedupe) as writer:
                writer.write(signed)
            groups = list(groups_after())
            if sum(g.count for g in groups) == writer.count:
                group_count = write_groups(tmp / GROUPS_FILE, groups)
            else:
                # The grouper saw entries the writer dropped as duplicates (or missed some):
                # derive groups from what was actually stored.
                group_count = groups_from_entries(tmp / ENTRIES_FILE, tmp / GROUPS_FILE)
            first_ts, last_ts = _time_range(writer.sources.values())
            manifest = LogSetManifest(
                id=set_id,
                name=name,
                format=fmt.name,
                sources=_source_infos(src_list, writer.sources),
                first_ts=first_ts,
                last_ts=last_ts,
                entry_count=writer.count,
                group_count=group_count,
                created_at=datetime.now(UTC),
                tool_version=__version__,
                schema_version=SCHEMA_VERSION,
                signature_version=SIGNATURE_VERSION,
                mask_rules_version=MASK_RULES_VERSION,
                signature_spec=spec.key,
                recipe_key=(recipe or Recipe(fmt, spec)).key,
                node=node,
                cluster=cluster,
                capture=capture,
            )
            manifest.write(tmp)
            with self._writing():
                # another writer may have taken the name or built the same set meanwhile
                self._check_name(name, set_id)
                if not overwrite and (existing / MANIFEST_FILE).is_file():
                    shutil.rmtree(tmp, ignore_errors=True)
                    return self._load(existing)
                final = self._install(tmp, set_id)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return _logset(final, manifest)

    def regroup(self, ref: str, spec: SignatureSpec) -> LogSet:
        """Re-sign the stored entries with another spec and recompute ``groups.parquet``.

        Entries are re-signed from their stored columns with the current recipe of
        the set's format (:class:`~atlas_log_analyzer.core.pipeline.Recipe`), each
        distinct (level, logger, message, exception chain, body lead line of a blank
        message) once, so ``entries.parquet`` is rewritten too (``signature``,
        ``coarse_signature``, ``template``, ``variables`` change; every other column is
        kept) and the manifest records the new spec, recipe key and current
        signature/mask-rule versions. Any ``merges.parquet`` is dropped because it
        refers to the old signatures.

        A set built by an older version of its format is also re-assembled from the
        stored entries (the log files are not read): banners are attached to the entry
        they announce (``resign_entries(reassemble=True)``), and the entry count, the
        per-source counts and time ranges follow.

        Raises:
            StoreError: The set does not exist or its format is unknown.

        """
        from atlas_log_analyzer.store.regroup import resign_entries
        from atlas_log_analyzer.store.writer import groups_from_entries, source_stats

        ls = self.open(ref)
        m = ls.manifest
        try:
            fmt = formats.get(m.format)
        except LookupError as exc:
            raise StoreError(f"unknown format {m.format!r} of log set {m.id}") from exc
        recipe = Recipe(fmt, spec)
        built_with = key_format_version(m.recipe_key) or 1
        tmp = self._tmp_dir(m.id)
        try:
            count = resign_entries(
                ls.entries_path,
                tmp / ENTRIES_FILE,
                recipe,
                m.format,
                reassemble=built_with < format_version(fmt),
                attach=built_with < banners_since(fmt),
            )
            group_count = groups_from_entries(tmp / ENTRIES_FILE, tmp / GROUPS_FILE)
            sources, first_ts, last_ts = m.sources, m.first_ts, m.last_ts
            if count != m.entry_count:
                stats = source_stats(tmp / ENTRIES_FILE)
                sources = _restat(m.sources, stats)
                first_ts, last_ts = _time_range(stats.values())
            manifest = replace(
                m,
                sources=sources,
                first_ts=first_ts,
                last_ts=last_ts,
                entry_count=count,
                group_count=group_count,
                tool_version=__version__,
                schema_version=SCHEMA_VERSION,
                signature_version=SIGNATURE_VERSION,
                mask_rules_version=MASK_RULES_VERSION,
                signature_spec=spec.key,
                recipe_key=recipe.key,
            )
            with self._writing():
                manifest = self._still_current(ls.path, m, manifest)
                manifest.write(tmp)
                final = self._install(tmp, m.id)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        with self._index_lock:
            self._index.pop(final, None)
        return _logset(final, manifest)

    def _still_current(
        self, path: Path, read: LogSetManifest, rebuilt: LogSetManifest
    ) -> LogSetManifest:
        """``rebuilt`` with the labels the set has now; hold :meth:`_writing`.

        A rebuild reads the set, works without the lock, then installs: meanwhile
        ``label`` may have changed the identity fields (kept), and ``remove``, an
        ingest with ``--overwrite`` or another rebuild may have replaced the data
        (refused, so nothing is lost or brought back).

        Raises:
            LogSetNotFound: The set was removed meanwhile.
            StoreError: Its data changed meanwhile.

        """
        if not (path / MANIFEST_FILE).is_file():
            raise LogSetNotFound(f"log set {read.id} was removed while it was rebuilt")
        now = self._read(path)
        if replace(now, node=read.node, cluster=read.cluster, capture=read.capture) != read:
            raise StoreError(f"log set {read.id} changed while it was rebuilt; run it again")
        return replace(rebuilt, node=now.node, cluster=now.cluster, capture=now.capture)

    def upgrade(self, ref: str) -> UpgradeResult:
        """Rebuild a set at the current recipe of its format, keeping its signature spec.

        A set is stale when its ``recipe_key`` differs from the key this tool would
        sign it with today (a format, signature or masking-rule version changed);
        stale sets are not comparable with new ones. The set is re-signed as by
        :meth:`regroup` with its own spec; a set that is current, or whose format
        is not registered, is left untouched and reported as skipped.

        A set ingested from a ZIP by an older version names its files by where they
        were extracted (a spool directory removed since): those paths become
        ``<zip>/<member>`` in the entries, groups and manifest, whether or not the
        set is stale.

        Raises:
            StoreError: The set does not exist.

        """
        ls = self.open(ref)
        m = ls.manifest
        names = _spool_renames(ls)
        current = current_recipe_key(m.format, m.signature_spec)
        if current is not None and current != m.recipe_key:
            ls = self.regroup(m.id, SignatureSpec.from_key(m.signature_spec))
        elif not names:
            reason = (
                "current"
                if current is not None
                else f"format {m.format!r} or signature spec {m.signature_spec!r} is unknown"
            )
            return UpgradeResult(m, upgraded=False, reason=reason, previous_recipe_key=m.recipe_key)
        if names:
            ls = self._rename_sources(ls, names)
        return UpgradeResult(
            ls.manifest,
            upgraded=True,
            reason=None,
            previous_recipe_key=m.recipe_key,
            renamed_sources=len(names),
        )

    def _rename_sources(self, ls: LogSet, names: dict[str, str]) -> LogSet:
        """Rewrite the set with its source paths renamed by ``names`` (signatures kept)."""
        from atlas_log_analyzer.store.regroup import rename_sources
        from atlas_log_analyzer.store.writer import groups_from_entries, source_stats

        m = ls.manifest
        tmp = self._tmp_dir(m.id)
        try:
            for f in ls.path.iterdir():  # merges and anything else: same signatures
                if f.is_file() and f.name not in (ENTRIES_FILE, GROUPS_FILE, MANIFEST_FILE):
                    shutil.copy2(f, tmp / f.name)
            rename_sources(ls.entries_path, tmp / ENTRIES_FILE, names)
            group_count = groups_from_entries(tmp / ENTRIES_FILE, tmp / GROUPS_FILE)
            sources = tuple(replace(s, path=names.get(s.path, s.path)) for s in m.sources)
            manifest = replace(
                m,
                sources=_restat(sources, source_stats(tmp / ENTRIES_FILE)),
                group_count=group_count,
                tool_version=__version__,
            )
            with self._writing():
                manifest = self._still_current(ls.path, m, manifest)
                manifest.write(tmp)
                final = self._install(tmp, m.id)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        with self._index_lock:
            self._index.pop(final, None)
        return _logset(final, manifest)

    def remove(self, ref: str) -> None:
        """Delete a log set directory.

        The directory is first renamed out of the way, so the set disappears at
        once and a failure leaves it intact, then deleted. Both steps are retried
        while a reader holds one of its files open (Windows).

        Raises:
            StoreError: The set does not exist or cannot be deleted.

        """
        ls = self.open(ref)
        set_id = ls.manifest.id
        trash = self._trash(set_id)
        with self._writing():
            try:
                _retry(lambda: os.replace(ls.path, trash))
            except OSError as exc:
                raise StoreError(f"cannot remove log set {set_id}: {exc}") from exc
        with self._index_lock:
            self._index.pop(ls.path, None)
        _remove_tree(trash)
