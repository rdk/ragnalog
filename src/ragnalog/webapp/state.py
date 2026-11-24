"""Process-wide state of the web app: the store, caches and the job manager."""

from __future__ import annotations

import logging
import shutil
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import msgspec
from fastapi import Request

from ragnalog.report import build_payload
from ragnalog.service.views import set_activity, set_summary
from ragnalog.store.locks import FileLock
from ragnalog.store.logset import MANIFEST_FILE, LogSet, LogSetManifest
from ragnalog.store.store import (
    UNSET,
    AmbiguousLogSet,
    Label,
    LogSetNotFound,
    Store,
    StoreError,
)
from ragnalog.webapp.jobs import UPLOADS_DIR, JobManager
from ragnalog.webapp.settings import Settings

if TYPE_CHECKING:
    from ragnalog.service.capture_queries import CaptureWindow

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


_LOG = logging.getLogger(__name__)


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
        f"another ragnalog server is already using the data directory {root}; stop it or serve "
        "another --store"
    )


def clean_spool(root: Path, *, max_age_s: float = SPOOL_MAX_AGE_S) -> int:
    """Delete what a crashed ingest left in ``<root>/.spool``: entries older than ``max_age_s``.

    A running ingest (a CLI next to the server) extracts into a fresh entry, so only old
    ones are removed. Returns the number of entries deleted.
    """
    from ragnalog.store.split import SPOOL_DIR

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
        self._activity: dict[tuple[str, str], list[tuple[int, int, int]]] = {}
        self._cache_lock = threading.Lock()
        self._building: dict[tuple[Any, ...], threading.Lock] = {}
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

    def warm(self) -> threading.Thread:
        """Build the newest capture's cluster page payload in a background thread.

        The first open of a cluster page builds it (about half a second for two nodes of
        700k entries each); ``ragnalog serve`` does that at startup for the capture most
        likely opened next, with the options the page asks for. Failures are only logged.
        """

        def run() -> None:
            from ragnalog.service.capture_queries import CaptureWindow
            from ragnalog.service.captures import list_captures

            try:
                found = list_captures(self)
                if found:
                    self.cluster_report_bytes(
                        f"capture:{found[0].capture}",
                        CaptureWindow(),
                        examples=3,
                        max_raw=4000,
                        max_groups=500,
                        triage=True,
                    )
            except Exception:
                _LOG.debug("warming the newest capture failed", exc_info=True)

        thread = threading.Thread(target=run, name="ragnalog-warm", daemon=True)
        thread.start()
        return thread

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

    def _store_version(self) -> tuple[tuple[str, str, int], ...]:
        """Return the version of every set (:func:`_version`), for keys depending on all."""
        return tuple(
            (m.id, str(m.created_at), _manifest_mtime(self.store.set_dir(m.id)))
            for m in self.manifests()
        )

    def remove(self, ref: str) -> LogSet | None:
        """Delete a set and drop its cached data (``None``: a set with an unreadable manifest)."""
        try:
            ls = self.store.open(ref)
        except LogSetNotFound, AmbiguousLogSet:
            raise
        except StoreError:  # `Store.remove` still deletes such a set by its directory name
            self.store.remove(ref)
            self.invalidate(ref)
            return None
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
            for key in [k for k in self._activity if k[0] == set_id]:
                del self._activity[key]

    def summary(self, ls: LogSet) -> dict[str, int]:
        """:func:`~ragnalog.service.views.set_summary`, cached per set version."""
        key = (ls.manifest.id, _version(ls))
        with self._cache_lock:
            hit = self._summaries.get(key)
        if hit is not None:
            return hit
        row = set_summary(ls)
        with self._cache_lock:
            _replace_version(self._summaries, key, row)
        return row

    def activity(self, ls: LogSet) -> list[tuple[int, int, int]]:
        """:func:`~ragnalog.service.views.set_activity`, cached per set version."""
        key = (ls.manifest.id, _version(ls))
        with self._cache_lock:
            hit = self._activity.get(key)
        if hit is not None:
            return hit
        hours = set_activity(ls)
        with self._cache_lock:
            _replace_version(self._activity, key, hours)
        return hours

    def _cached(
        self,
        cache: OrderedDict[tuple[Any, ...], bytes],
        key: tuple[Any, ...],
        build: Callable[[], bytes],
    ) -> bytes:
        """Return ``cache[key]``, building it once: concurrent callers wait for one build."""
        with self._cache_lock:
            hit = cache.get(key)
            if hit is not None:
                cache.move_to_end(key)
                return hit
            gate = self._building.setdefault(key, threading.Lock())
        with gate:
            with self._cache_lock:
                hit = cache.get(key)
            if hit is not None:
                return hit  # built by the caller this one waited for
            try:
                data = build()
                with self._cache_lock:
                    cache[key] = data
                    while len(cache) > max(1, self.settings.report_cache):
                        cache.popitem(last=False)
            finally:
                with self._cache_lock:
                    self._building.pop(key, None)
        return data

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
        """Return the encoded ``report.v1`` payload, built once per set version and options.

        With ``entries_url`` (the web app) the example entries are not embedded: the page
        fetches them per group from that URL.
        """
        key = (
            ls.manifest.id,
            _version(ls),
            compare.manifest.id if compare else None,
            _version(compare) if compare else None,
            min_level,
            examples,
            max_raw,
            max_groups,
            entries_url,
        )

        def build() -> bytes:
            payload = build_payload(
                ls,
                compare=compare,
                min_level=min_level,
                examples=examples,
                max_raw=max_raw,
                max_groups=max_groups,
                entries_url=entries_url,
                embed_examples=entries_url is None,
            )
            return _ENCODER.encode(payload)

        return self._cached(self._reports, key, build)

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

        Built once per member set versions and options; with ``triage`` also per version
        of every other set (a node's baseline can be any of them, and the CLI can add,
        remove or relabel one next to the server). :meth:`invalidate` drops them all.
        """
        from ragnalog.service import capture_queries
        from ragnalog.service.documents import ClusterReportUrls

        capture = capture_queries.capture_from_ref(self, ref, front="api")
        window = w.resolve(capture, front="api")  # since=1d and an ISO time can be one window
        key = (
            tuple((m.node, m.logset.id, _version(m.logset)) for m in capture.nodes),
            capture.id,
            capture.cluster,
            window.mode,
            window.ms,
            examples,
            max_raw,
            max_groups,
            triage,
            self._store_version() if triage else None,
        )

        def build() -> bytes:
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
                examples="/api/sets/{set}/groups/{sig}/examples?max_raw=0",
                raw="/sets/{set}/entries/{id}/raw",
                timeline=f"/api/captures/{target}/timeline",
                users=f"/api/captures/{target}/users",
            )
            return _ENCODER.encode(payload)

        return self._cached(self._clusters, key, build)


def _replace_version[V](cache: dict[tuple[str, str], V], key: tuple[str, str], value: V) -> None:
    """Store ``value`` under ``(set id, version)``, dropping the set's older versions.

    A relabel by the CLI next to the server gives a set a new version without going
    through :meth:`AppState.invalidate`; the old entries would stay forever.
    """
    for old in [k for k in cache if k[0] == key[0]]:
        del cache[old]
    cache[key] = value


def _manifest_mtime(path: Path) -> int:
    try:
        return (path / MANIFEST_FILE).stat().st_mtime_ns
    except OSError:
        return 0


def _version(ls: LogSet) -> str:
    """Return a set's version for the caches.

    ``regroup``, ``upgrade`` and ``label`` (also when run from the CLI next to the server)
    keep ``created_at`` but rewrite the manifest, so its mtime is part of the version.
    """
    return f"{ls.manifest.created_at}|{_manifest_mtime(ls.path)}"


def get_state(request: Request) -> AppState:
    """FastAPI dependency: the app's :class:`AppState`."""
    state: AppState = request.app.state.ragnalog
    return state
