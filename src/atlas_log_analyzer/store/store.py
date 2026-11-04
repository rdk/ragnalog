"""The :class:`Store`: a directory holding log sets."""

from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import xxhash

from atlas_log_analyzer import __version__
from atlas_log_analyzer.core import formats
from atlas_log_analyzer.core.formats.base import LogFormat
from atlas_log_analyzer.core.grouping import Group, Grouper, SignedEntry
from atlas_log_analyzer.core.masking import GENERIC_RULES, MASK_RULES_VERSION, Masker
from atlas_log_analyzer.core.reader import SourceFile
from atlas_log_analyzer.core.signature import SIGNATURE_VERSION, SignatureSpec, Signer
from atlas_log_analyzer.store.locks import FileLock, LockUnavailable
from atlas_log_analyzer.store.logset import (
    ENTRIES_FILE,
    GROUPS_FILE,
    MANIFEST_FILE,
    LogSet,
    LogSetManifest,
    SourceInfo,
)
from atlas_log_analyzer.store.schema import SCHEMA_VERSION

if TYPE_CHECKING:
    from atlas_log_analyzer.store.writer import SourceStats

__all__ = ["MIN_PREFIX", "AmbiguousLogSet", "LogSetNotFound", "Store", "StoreError", "logset_id"]

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
    from atlas_log_analyzer.store.writer import SourceStats

    normalised = {_norm(k): v for k, v in stats.items()}
    out = []
    for sf in sources:
        st = stats.get(str(sf.path)) or normalised.get(_norm(str(sf.path))) or SourceStats()
        out.append(
            SourceInfo(
                path=str(sf.path),
                size=sf.size,
                xxh3=sf.xxh3,
                first_ts=st.first_ts,
                last_ts=st.last_ts,
                entries=st.entries,
            )
        )
    return tuple(out)


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


def _time_range(stats: Iterable[SourceStats]) -> tuple[datetime | None, datetime | None]:
    firsts = [s.first_ts for s in stats if s.first_ts is not None]
    lasts = [s.last_ts for s in stats if s.last_ts is not None]
    return (min(firsts) if firsts else None, max(lasts) if lasts else None)


class Store:
    """Directory of log sets; creates it on first write.

    Every log set lives in ``<root>/<id>/``. Directories whose names start with
    ``.`` are temporary (in-progress writes) and ignored by readers.

    One instance may be shared by threads. Reads take no lock beyond a short one
    around the manifest cache; a write locks the store (against other processes
    too, see :data:`WRITE_LOCK_FILE`) only for its final name check and rename.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._index: dict[Path, tuple[int, int, LogSet | None]] = {}
        """Manifest cache: set dir → (manifest mtime_ns, size, loaded set or None if invalid)."""
        self._index_lock = threading.Lock()
        """Guards :attr:`_index`; manifests are loaded outside it."""
        self._write_lock = threading.Lock()
        """Serialises the installs and removals of this instance (see :meth:`_writing`)."""

    def _cached(self, d: Path) -> LogSet | None:
        """Load ``d`` through the manifest cache; ``None`` when unreadable or invalid."""
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
            ls: LogSet | None = LogSet.load(d)
        except OSError, ValueError:
            ls = None
        with self._index_lock:
            self._index[d] = (st.st_mtime_ns, st.st_size, ls)
        return ls

    def _set_dirs(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return [
            p
            for p in self.root.iterdir()
            if p.is_dir() and not p.name.startswith(".") and (p / MANIFEST_FILE).is_file()
        ]

    def _load_all(self) -> list[LogSet]:
        dirs = self._set_dirs()
        live = set(dirs)
        with self._index_lock:
            for stale in [d for d in self._index if d not in live]:
                del self._index[stale]
        # corrupt or newer-schema sets are skipped by listings
        return [ls for d in dirs if (ls := self._cached(d)) is not None]

    def list(self) -> list[LogSetManifest]:
        """Manifests of all log sets, newest first.

        Sets whose manifest is unreadable or uses a newer schema are skipped.
        """
        manifests = [ls.manifest for ls in self._load_all()]
        return sorted(manifests, key=lambda m: m.created_at, reverse=True)

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
        exact = self.root / ref
        plain = ref and not ref.startswith(".") and "/" not in ref and "\\" not in ref
        if plain and (exact / MANIFEST_FILE).is_file():
            ls = self._cached(exact)
            return ls if ls is not None else self._load(exact)
        sets = self._load_all()
        by_name = [ls for ls in sets if ls.manifest.name == ref]
        if len(by_name) == 1:
            return by_name[0]
        if len(by_name) > 1:
            ids = ", ".join(ls.manifest.id for ls in by_name)
            raise AmbiguousLogSet(f"name {ref!r} is ambiguous: {ids}")
        if len(ref) >= MIN_PREFIX:
            by_prefix = [ls for ls in sets if ls.manifest.id.startswith(ref.lower())]
            if len(by_prefix) == 1:
                return by_prefix[0]
            if len(by_prefix) > 1:
                ids = ", ".join(ls.manifest.id for ls in by_prefix)
                raise AmbiguousLogSet(f"id prefix {ref!r} is ambiguous: {ids}")
        raise LogSetNotFound(f"no log set {ref!r} in {self.root}")

    @staticmethod
    def _load(path: Path) -> LogSet:
        try:
            return LogSet.load(path)
        except (OSError, ValueError) as exc:
            raise StoreError(f"cannot open log set {path.name}: {exc}") from exc

    def _tmp_dir(self, set_id: str) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f".tmp-{set_id}-{uuid.uuid4().hex[:8]}"
        tmp.mkdir()
        return tmp

    @contextmanager
    def _writing(self) -> Iterator[None]:
        """Hold the store's write lock: this instance's, then :data:`WRITE_LOCK_FILE`.

        The file lock keeps another process (a CLI ingest next to ``serve``) from
        installing a set under the same name at the same moment. On file systems
        that cannot lock files only the in-process lock is held.
        """
        with self._write_lock:
            self.root.mkdir(parents=True, exist_ok=True)
            lock = FileLock(self.root / WRITE_LOCK_FILE)
            try:
                locked = lock.acquire(timeout=WRITE_LOCK_TIMEOUT_S)
            except LockUnavailable:
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
        return LogSet(final, manifest)

    def regroup(self, ref: str, spec: SignatureSpec) -> LogSet:
        """Recompute ``groups.parquet`` from the stored entries with another spec.

        Entries are re-signed from their stored columns with the set's format
        rules, so ``entries.parquet`` is rewritten too (``signature``,
        ``coarse_signature``, ``template``, ``variables`` change) and the manifest
        records the new spec and current signature/mask-rule versions. Any
        ``merges.parquet`` is dropped because it refers to the old signatures.

        Raises:
            StoreError: The set does not exist or its format is unknown.

        """
        from atlas_log_analyzer.store.writer import EntriesWriter, iter_entries, write_groups

        ls = self.open(ref)
        m = ls.manifest
        try:
            fmt = formats.get(m.format)
        except LookupError as exc:
            raise StoreError(f"unknown format {m.format!r} of log set {m.id}") from exc
        grouper = Grouper(Signer(Masker([*fmt.mask_rules, *GENERIC_RULES]), spec))
        tmp = self._tmp_dir(m.id)
        try:
            # stored ids are unique: no deduplication set needed
            with EntriesWriter(tmp / ENTRIES_FILE, dedupe=False) as writer:
                writer.write(grouper.sign(iter_entries(ls.entries_path, m.format)))
            group_count = write_groups(tmp / GROUPS_FILE, grouper.groups.values())
            manifest = replace(
                m,
                entry_count=writer.count,
                group_count=group_count,
                tool_version=__version__,
                schema_version=SCHEMA_VERSION,
                signature_version=SIGNATURE_VERSION,
                mask_rules_version=MASK_RULES_VERSION,
                signature_spec=spec.key,
            )
            manifest.write(tmp)
            with self._writing():
                final = self._install(tmp, m.id)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return LogSet(final, manifest)

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
