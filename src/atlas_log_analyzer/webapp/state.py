"""Process-wide state of the web app: the store, caches and the job manager."""

from __future__ import annotations

import shutil
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import msgspec
from fastapi import Request

from atlas_log_analyzer.report import build_payload
from atlas_log_analyzer.service.views import set_summary
from atlas_log_analyzer.store.locks import FileLock
from atlas_log_analyzer.store.logset import LogSet, LogSetManifest
from atlas_log_analyzer.store.store import UNSET, Label, Store, StoreError
from atlas_log_analyzer.webapp.jobs import UPLOADS_DIR, JobManager
from atlas_log_analyzer.webapp.settings import Settings

if TYPE_CHECKING:
    from atlas_log_analyzer.service.capture_queries import CaptureWindow

__all__ = [
    "SERVER_LOCK_FILE",
    "SPOOL_MAX_AGE_S",
    "AppState",
    "StoreInUse",
    "clean_spool",
    "get_state",
    "lock_server",
]

SERVER_LOCK_FILE = ".server.lock"
"""Lock file in the store root held by the one server process that uses the store."""
SPOOL_MAX_AGE_S = 6 * 3600
"""Entries of the store's ZIP spool (``.spool``) older than this are left by a crashed ingest."""

_ENCODER = msgspec.json.Encoder()


class StoreInUse(StoreError):
    """Another server process holds the store's :data:`SERVER_LOCK_FILE`."""


def lock_server(root: Path) -> FileLock | None:
    """Take the one-server-per-store lock of ``root``.

    The operating system releases it when the process ends, so a crashed server
    never blocks the next one. Returns ``None`` (no lock) when ``root`` does not
    exist yet, the lock file cannot be created or the file system cannot lock files.

    Raises:
        StoreInUse: Another process serves ``root``.

    """
    if not root.is_dir():
        return None
    lock = FileLock(root / SERVER_LOCK_FILE)
    try:
        if lock.acquire():
            return lock
    except OSError:  # no lock file (read-only store) or no locking: run unlocked
        return None
    raise StoreInUse(
        f"another atlas-log server is already using the store {root}; stop it or serve "
        "another --store"
    )


def clean_spool(root: Path, *, max_age_s: float = SPOOL_MAX_AGE_S) -> int:
    """Delete what a crashed ingest left in ``<root>/.spool``: entries older than ``max_age_s``.

    A running ingest (a CLI next to the server) extracts into a fresh entry, so only old
    ones are removed. Returns the number of entries deleted.
    """
    from atlas_log_analyzer.store.split import SPOOL_DIR

    spool = root / SPOOL_DIR
    if not spool.is_dir():
        return 0
    cutoff = time.time() - max_age_s
    removed = 0
    for entry in spool.iterdir():
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
        removed += 1
    return removed


class AppState:
    """Everything the routes share.

    ``Store`` is safe to share between the request threads and the ingest
    worker: it locks only its manifest cache and, for a write, the final rename,
    so pages and ``/api`` keep answering during an ingest. Log sets are
    immutable once written, which makes cached report payloads and set
    summaries valid until the set's ``created_at`` changes (overwrite) or the
    set is removed.
    """

    def __init__(self, settings: Settings) -> None:
        """Create the state for ``settings``."""
        self.settings = settings
        self.store = Store(settings.store)
        self.jobs = JobManager(self.store, on_done=self.invalidate, sync=settings.sync_jobs)
        self._reports: OrderedDict[tuple[Any, ...], bytes] = OrderedDict()
        self._clusters: OrderedDict[tuple[Any, ...], bytes] = OrderedDict()
        self._summaries: dict[tuple[str, str], dict[str, int]] = {}
        self._cache_lock = threading.Lock()
        self._server_lock: FileLock | None = None

    def start(self) -> None:
        """Claim the store; delete upload spools of an earlier server and stale ZIP spools.

        Raises:
            StoreInUse: Another server process uses the store.

        """
        self._server_lock = lock_server(self.store.root)
        clean_spool(self.store.root)
        if self._server_lock is None:
            return  # no store yet, or no locking: another server may own the spools
        spool = self.store.root / UPLOADS_DIR
        if spool.is_dir():
            for orphan in spool.iterdir():
                if orphan.is_dir():
                    shutil.rmtree(orphan, ignore_errors=True)
                else:
                    orphan.unlink(missing_ok=True)

    def close(self) -> None:
        """Cancel the jobs, delete their spools and release the store."""
        try:
            self.jobs.shutdown()
        finally:
            if self._server_lock is not None:
                self._server_lock.release()
                self._server_lock = None

    def open(self, ref: str) -> LogSet:
        """Open a set by id, id prefix or name (raises the store's typed errors)."""
        return self.store.open(ref)

    def manifests(self) -> list[LogSetManifest]:
        """All manifests, newest first."""
        return self.store.list()

    def remove(self, ref: str) -> LogSet:
        """Delete a set and drop its cached data."""
        ls = self.store.open(ref)
        self.store.remove(ls.manifest.id)
        self.invalidate(ls.manifest.id)
        return ls

    def label(
        self, ref: str, *, node: Label = UNSET, cluster: Label = UNSET, capture: Label = UNSET
    ) -> LogSetManifest:
        """Label a set (:meth:`Store.label`) and drop its cached data.

        The cached payloads hold the manifest, and labelling keeps ``created_at``.
        """
        m = self.store.label(ref, node=node, cluster=cluster, capture=capture)
        self.invalidate(m.id)
        return m

    def invalidate(self, set_id: str) -> None:
        """Drop cached payloads and summaries that involve ``set_id``.

        Cluster payloads are all dropped: a new, removed or relabelled set can change a
        capture's members and every node's triage baseline.
        """
        with self._cache_lock:
            for key in [k for k in self._reports if set_id in (k[0], k[2])]:
                del self._reports[key]
            self._clusters.clear()
            for key in [k for k in self._summaries if k[0] == set_id]:
                del self._summaries[key]

    def summary(self, ls: LogSet) -> dict[str, int]:
        """:func:`~atlas_log_analyzer.service.views.set_summary`, cached per set version."""
        key = (ls.manifest.id, str(ls.manifest.created_at))
        with self._cache_lock:
            hit = self._summaries.get(key)
        if hit is not None:
            return hit
        row = set_summary(ls)
        with self._cache_lock:
            self._summaries[key] = row
        return row

    def report_bytes(
        self,
        ls: LogSet,
        *,
        compare: LogSet | None,
        min_level: str | None,
        examples: int,
        max_raw: int,
        max_groups: int | None,
        entries_url: str | None,
    ) -> bytes:
        """Return the encoded ``report.v1`` payload, built once per set version and options."""
        key = (
            ls.manifest.id,
            str(ls.manifest.created_at),
            compare.manifest.id if compare else None,
            str(compare.manifest.created_at) if compare else None,
            min_level,
            examples,
            max_raw,
            max_groups,
            entries_url,
        )
        with self._cache_lock:
            hit = self._reports.get(key)
            if hit is not None:
                self._reports.move_to_end(key)
                return hit
        payload = build_payload(
            ls,
            compare=compare,
            min_level=min_level,
            examples=examples,
            max_raw=max_raw,
            max_groups=max_groups,
            entries_url=entries_url,
        )
        data = _ENCODER.encode(payload)
        with self._cache_lock:
            self._reports[key] = data
            while len(self._reports) > max(1, self.settings.report_cache):
                self._reports.popitem(last=False)
        return data

    def cluster_report_bytes(
        self,
        ref: str,
        w: CaptureWindow,
        *,
        examples: int,
        max_raw: int,
        max_groups: int,
        triage: bool,
    ) -> bytes:
        """Return the encoded ``cluster-report.v1`` of a capture with the web app's ``urls``.

        Built once per member set versions and options; :meth:`invalidate` drops them all.
        """
        from atlas_log_analyzer.service import capture_queries
        from atlas_log_analyzer.service.documents import ClusterReportUrls

        capture = capture_queries.capture_from_ref(self, ref, front="api")
        key = (
            tuple((m.node, m.logset.id, str(m.logset.manifest.created_at)) for m in capture.nodes),
            capture.id,
            capture.cluster,
            w,
            examples,
            max_raw,
            max_groups,
            triage,
        )
        with self._cache_lock:
            hit = self._clusters.get(key)
            if hit is not None:
                self._clusters.move_to_end(key)
                return hit
        payload = capture_queries.report_payload(
            self,
            capture,
            w,
            examples=examples,
            max_raw=max_raw,
            max_groups=max_groups,
            triage=triage,
            front="api",
        )
        target = capture.ref if capture.id is not None else capture.nodes[0].logset.id
        payload.urls = ClusterReportUrls(
            entries=f"/api/captures/{target}/entries",
            context="/api/sets/{set}/entries/{id}/context?all_nodes=1&window=30",
            set_report="/sets/{set}",
        )
        data = _ENCODER.encode(payload)
        with self._cache_lock:
            self._clusters[key] = data
            while len(self._clusters) > max(1, self.settings.report_cache):
                self._clusters.popitem(last=False)
        return data


def get_state(request: Request) -> AppState:
    """FastAPI dependency: the app's :class:`AppState`."""
    state: AppState = request.app.state.atlas
    return state
