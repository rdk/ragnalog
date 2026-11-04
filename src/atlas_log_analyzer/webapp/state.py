"""Process-wide state of the web app: the store, its lock, caches and the job manager."""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any

import msgspec
from fastapi import Request

from atlas_log_analyzer.report import build_payload
from atlas_log_analyzer.store.logset import LogSet, LogSetManifest
from atlas_log_analyzer.store.store import Store
from atlas_log_analyzer.store.views import set_summary
from atlas_log_analyzer.webapp.jobs import JobManager
from atlas_log_analyzer.webapp.settings import Settings

__all__ = ["AppState", "get_state"]

_ENCODER = msgspec.json.Encoder()


class AppState:
    """Everything the routes share.

    ``Store`` keeps a manifest cache that is not thread-safe, so every store
    access goes through :attr:`lock`. Log sets are immutable once written, which
    makes cached report payloads and set summaries valid until the set's
    ``created_at`` changes (overwrite) or the set is removed.
    """

    def __init__(self, settings: Settings) -> None:
        """Create the state for ``settings``."""
        self.settings = settings
        self.store = Store(settings.store)
        self.lock = threading.RLock()
        self.jobs = JobManager(
            self.store, self.lock, on_done=self.invalidate, sync=settings.sync_jobs
        )
        self._reports: OrderedDict[tuple[Any, ...], bytes] = OrderedDict()
        self._summaries: dict[tuple[str, str], dict[str, int]] = {}
        self._cache_lock = threading.Lock()

    def open(self, ref: str) -> LogSet:
        """Open a set by id, id prefix or name (raises the store's typed errors)."""
        with self.lock:
            return self.store.open(ref)

    def manifests(self) -> list[LogSetManifest]:
        """All manifests, newest first."""
        with self.lock:
            return self.store.list()

    def remove(self, ref: str) -> LogSet:
        """Delete a set and drop its cached data."""
        with self.lock:
            ls = self.store.open(ref)
            self.store.remove(ls.manifest.id)
        self.invalidate(ls.manifest.id)
        return ls

    def invalidate(self, set_id: str) -> None:
        """Drop cached payloads and summaries that involve ``set_id``."""
        with self._cache_lock:
            for key in [k for k in self._reports if set_id in (k[0], k[2])]:
                del self._reports[key]
            for key in [k for k in self._summaries if k[0] == set_id]:
                del self._summaries[key]

    def summary(self, ls: LogSet) -> dict[str, int]:
        """:func:`~atlas_log_analyzer.store.views.set_summary`, cached per set version."""
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


def get_state(request: Request) -> AppState:
    """FastAPI dependency: the app's :class:`AppState`."""
    state: AppState = request.app.state.atlas
    return state
