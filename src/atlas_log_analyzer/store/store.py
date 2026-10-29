"""The :class:`Store`: a directory holding log sets."""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import xxhash

from atlas_log_analyzer import __version__
from atlas_log_analyzer.core import formats
from atlas_log_analyzer.core.formats.base import LogFormat
from atlas_log_analyzer.core.grouping import Group, Grouper, SignedEntry
from atlas_log_analyzer.core.masking import GENERIC_RULES, MASK_RULES_VERSION, Masker
from atlas_log_analyzer.core.reader import SourceFile
from atlas_log_analyzer.core.signature import SIGNATURE_VERSION, SignatureSpec, Signer
from atlas_log_analyzer.store.logset import (
    ENTRIES_FILE,
    GROUPS_FILE,
    MANIFEST_FILE,
    LogSet,
    LogSetManifest,
    SourceInfo,
)
from atlas_log_analyzer.store.schema import SCHEMA_VERSION
from atlas_log_analyzer.store.writer import (
    EntriesWriter,
    SourceStats,
    groups_from_entries,
    iter_entries,
    write_groups,
)

__all__ = ["MIN_PREFIX", "Store", "StoreError", "logset_id"]

MIN_PREFIX = 4
"""Minimum length of an id prefix accepted by :meth:`Store.open`."""


class StoreError(Exception):
    """Any store-level failure (missing set, corrupt manifest, version mismatch)."""


def logset_id(digests: Iterable[str]) -> str:
    """Log-set id: xxh3-64 hex of the LF-joined sorted source digests."""
    return xxhash.xxh3_64_hexdigest("\n".join(sorted(digests)).encode())


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _source_infos(
    sources: list[SourceFile], stats: dict[str, SourceStats]
) -> tuple[SourceInfo, ...]:
    """Match writer statistics (keyed by ``entry.source.path``) to the input files."""
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


def _time_range(stats: Iterable[SourceStats]) -> tuple[datetime | None, datetime | None]:
    firsts = [s.first_ts for s in stats if s.first_ts is not None]
    lasts = [s.last_ts for s in stats if s.last_ts is not None]
    return (min(firsts) if firsts else None, max(lasts) if lasts else None)


class Store:
    """Directory of log sets; creates it on first write.

    Every log set lives in ``<root>/<id>/``. Directories whose names start with
    ``.`` are temporary (in-progress writes) and ignored by readers.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _set_dirs(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return [
            p
            for p in self.root.iterdir()
            if p.is_dir() and not p.name.startswith(".") and (p / MANIFEST_FILE).is_file()
        ]

    def _load_all(self) -> list[LogSet]:
        out = []
        for d in self._set_dirs():
            try:
                out.append(LogSet.load(d))
            except OSError, ValueError:
                continue  # corrupt or newer-schema sets are skipped by listings
        return out

    def list(self) -> list[LogSetManifest]:
        """Manifests of all log sets, newest first.

        Sets whose manifest is unreadable or uses a newer schema are skipped.
        """
        manifests = [ls.manifest for ls in self._load_all()]
        return sorted(manifests, key=lambda m: m.created_at, reverse=True)

    def open(self, ref: str) -> LogSet:
        """Open a log set by id, unique id prefix, or name; raise :class:`StoreError`.

        Resolution order: exact id, then exact name, then id prefix of at least
        :data:`MIN_PREFIX` characters. Several matches at the same step are an error.
        """
        exact = self.root / ref
        if ref and not ref.startswith(".") and (exact / MANIFEST_FILE).is_file():
            return self._load(exact)
        sets = self._load_all()
        by_name = [ls for ls in sets if ls.manifest.name == ref]
        if len(by_name) == 1:
            return by_name[0]
        if len(by_name) > 1:
            ids = ", ".join(ls.manifest.id for ls in by_name)
            raise StoreError(f"name {ref!r} is ambiguous: {ids}")
        if len(ref) >= MIN_PREFIX:
            by_prefix = [ls for ls in sets if ls.manifest.id.startswith(ref.lower())]
            if len(by_prefix) == 1:
                return by_prefix[0]
            if len(by_prefix) > 1:
                ids = ", ".join(ls.manifest.id for ls in by_prefix)
                raise StoreError(f"id prefix {ref!r} is ambiguous: {ids}")
        raise StoreError(f"no log set {ref!r} in {self.root}")

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

    def _install(self, tmp: Path, set_id: str) -> Path:
        """Move ``tmp`` to ``<root>/<set_id>``, replacing an existing set."""
        final = self.root / set_id
        trash: Path | None = None
        if final.exists():
            trash = self.root / f".trash-{set_id}-{uuid.uuid4().hex[:8]}"
            os.replace(final, trash)
        try:
            os.replace(tmp, final)
        except OSError:
            if trash is not None:
                os.replace(trash, final)
            raise
        if trash is not None:
            shutil.rmtree(trash, ignore_errors=True)
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
    ) -> LogSet:
        """Write a new log set.

        ``signed`` is consumed once, streamed into ``entries.parquet`` in
        batches with deduplication on ``entry.id``. ``groups_after`` is called
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
        src_list = list(sources)
        if not src_list:
            raise StoreError("no sources to ingest")
        set_id = logset_id(s.xxh3 for s in src_list)
        existing = self.root / set_id
        if (existing / MANIFEST_FILE).is_file() and not overwrite:
            return self._load(existing)
        if name is not None:
            for m in self.list():
                if m.name == name and m.id != set_id:
                    raise StoreError(f"name {name!r} is already used by log set {m.id}")
        tmp = self._tmp_dir(set_id)
        try:
            with EntriesWriter(tmp / ENTRIES_FILE) as writer:
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
        ls = self.open(ref)
        m = ls.manifest
        try:
            fmt = formats.get(m.format)
        except LookupError as exc:
            raise StoreError(f"unknown format {m.format!r} of log set {m.id}") from exc
        grouper = Grouper(Signer(Masker([*fmt.mask_rules, *GENERIC_RULES]), spec))
        tmp = self._tmp_dir(m.id)
        try:
            with EntriesWriter(tmp / ENTRIES_FILE) as writer:
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
            final = self._install(tmp, m.id)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return LogSet(final, manifest)

    def remove(self, ref: str) -> None:
        """Delete a log set directory.

        Raises:
            StoreError: The set does not exist or cannot be deleted.

        """
        ls = self.open(ref)
        try:
            shutil.rmtree(ls.path)
        except OSError as exc:
            raise StoreError(f"cannot remove log set {ls.manifest.id}: {exc}") from exc
