"""Background ingest jobs: one at a time, in a worker thread, with polled progress."""

from __future__ import annotations

import itertools
import shutil
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from ragnalog.core.paths import safe_name
from ragnalog.service.errors import OpError, from_exception
from ragnalog.service.ingest import ingest_paths
from ragnalog.service.params import IngestTarget
from ragnalog.store.pipeline import IngestProgress
from ragnalog.store.store import Store, StoreError

__all__ = [
    "KEEP_FINISHED",
    "MAX_ACTIVE_JOBS",
    "UPLOADS_DIR",
    "Job",
    "JobCancelled",
    "JobManager",
    "JobState",
    "QueueFull",
    "safe_filename",
]

JobState = Literal["queued", "running", "succeeded", "failed", "cancelled"]
UPLOADS_DIR = ".uploads"
"""Spool directory inside the store (ignored by store listings: it starts with a dot)."""
MAX_ACTIVE_JOBS = 8
"""Queued plus running jobs: one more upload is refused (503)."""


class QueueFull(Exception):
    """:meth:`JobManager.submit` refused a job: :data:`MAX_ACTIVE_JOBS` are queued or running."""


KEEP_FINISHED = 100
"""Finished jobs kept for ``GET /api/jobs``; older ones are forgotten."""


def _scrubbed(error: dict[str, Any]) -> dict[str, Any]:
    """Return an ``error.v1`` body without server paths in its message (spool, store)."""
    from ragnalog.webapp.errors import scrub_paths

    if isinstance(error.get("message"), str):
        error = {**error, "message": scrub_paths(error["message"])}
    return error


def safe_filename(name: str) -> str:
    """Basename of an upload reduced to ``[A-Za-z0-9_.-]`` (not empty, dot-only or ``nul.log``)."""
    return safe_name(Path(name.replace("\\", "/")).name, "upload.log")


class JobCancelled(Exception):
    """Raised inside the worker when a running job was cancelled."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(slots=True)
class Job:
    """One ingest job; the API reads the fields while the worker updates them."""

    id: str
    name: str | None
    files: list[str]
    bytes_total: int
    options: dict[str, Any]
    state: JobState = "queued"
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    entries: int = 0
    bytes_read: int = 0
    current_file: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    capture: dict[str, Any] | None = None
    """The ``ingest-capture.v1`` document of a split ingest."""
    cancel_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return the ``job.v1`` document."""
        if self.bytes_total:
            fraction = round(min(1.0, self.bytes_read / self.bytes_total), 4)
        else:
            fraction = 1.0 if self.state == "succeeded" else 0.0
        doc: dict[str, Any] = {
            "schema": "job.v1",
            "id": self.id,
            "kind": "ingest",
            "state": self.state,
            "name": self.name,
            "files": self.files,
            "options": self.options,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "progress": {
                "entries": self.entries,
                "bytes_read": self.bytes_read,
                "bytes_total": self.bytes_total,
                "fraction": fraction,
                "current_file": self.current_file,
            },
            "result": self.result,
            "error": self.error,
        }
        if self.capture is not None:
            doc["capture"] = self.capture
        return doc


class JobManager:
    """Run ingest jobs over a store, one at a time (parsing is CPU-bound).

    ``on_done`` is called with the set id after every successful ingest so caches
    can be invalidated. With ``sync=True`` jobs run in the submitting thread (tests).
    """

    def __init__(
        self,
        store: Store,
        *,
        on_done: Callable[[str], None] | None = None,
        sync: bool = False,
    ) -> None:
        """Create a manager for ``store`` (which locks itself only for the final rename)."""
        self.store = store
        self._on_done = on_done
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = None if sync else ThreadPoolExecutor(1, thread_name_prefix="ragnalog-ingest")
        self._seq = itertools.count(1)
        self._futures: dict[str, Future[None]] = {}

    @property
    def spool(self) -> Path:
        """Directory where uploads are written before ingest."""
        return self.store.root / UPLOADS_DIR

    def new_upload_dir(self) -> tuple[str, Path]:
        """Allocate a job id and create its upload directory."""
        job_id = f"{int(time.time() * 1000):x}{next(self._seq):02x}"
        path = self.spool / job_id
        path.mkdir(parents=True, exist_ok=True)
        return job_id, path

    def discard_upload(self, job_id: str) -> None:
        """Remove the upload directory of a job that was never submitted."""
        shutil.rmtree(self.spool / job_id, ignore_errors=True)

    def submit(self, job_id: str, paths: list[Path], *, name: str | None, **options: Any) -> Job:
        """Queue an ingest of ``paths`` (files in the job's upload directory).

        The cap is checked and the job added in one step under the lock, so uploads that
        finish at the same moment cannot push the queue past :data:`MAX_ACTIVE_JOBS` (the
        check from the request headers can only see the jobs submitted before them).

        Raises:
            QueueFull: :data:`MAX_ACTIVE_JOBS` jobs are already queued or running.

        """
        job = Job(
            id=job_id,
            name=name,
            files=[p.name for p in paths],
            bytes_total=sum(p.stat().st_size for p in paths),
            options=options,
        )
        with self._lock:
            if (
                sum(j.state in ("queued", "running") for j in self._jobs.values())
                >= MAX_ACTIVE_JOBS
            ):
                raise QueueFull(f"{MAX_ACTIVE_JOBS} ingest jobs are queued or running")
            self._jobs[job_id] = job
        if self._pool is None:
            self._run(job, paths)
        else:
            self._futures[job_id] = self._pool.submit(self._run, job, paths)
        return job

    def get(self, job_id: str) -> Job | None:
        """Return the job with this id, if known."""
        with self._lock:
            return self._jobs.get(job_id)

    def all(self) -> list[Job]:
        """Return all jobs of this process, newest first."""
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.id, reverse=True)

    def cancel(self, job_id: str) -> Job | None:
        """Request cancellation of a queued or running job.

        A queued job is cancelled at once; a running one at its next progress
        callback (the store discards the partially written set).
        """
        job = self.get(job_id)
        if job is None:
            return None
        future = self._futures.get(job_id)
        if job.state == "queued" and future is not None and future.cancel():
            self._finish(job, "cancelled")
            return job
        if job.state in ("queued", "running"):
            job.cancel_requested = True
        return job

    def shutdown(self) -> None:
        """Cancel everything, stop the worker and delete the spools (application shutdown).

        Jobs are not persisted, so a queued job's upload would never be read again.
        """
        for job in self.all():
            if job.state in ("queued", "running"):
                job.cancel_requested = True
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=True)
        for job in self.all():
            if job.state == "queued":  # its future was cancelled, so _run never ran
                self._finish(job, "cancelled")

    def active_count(self) -> int:
        """Return the number of queued and running jobs."""
        with self._lock:
            return sum(j.state in ("queued", "running") for j in self._jobs.values())

    def _finish(self, job: Job, state: JobState) -> None:
        job.state = state
        job.finished_at = _now()
        shutil.rmtree(self.spool / job.id, ignore_errors=True)
        self._prune()

    def _prune(self) -> None:
        """Forget the oldest finished jobs beyond :data:`KEEP_FINISHED`."""
        with self._lock:
            done = sorted(
                (j.id for j in self._jobs.values() if j.state not in ("queued", "running")),
                reverse=True,
            )
            for job_id in done[KEEP_FINISHED:]:
                del self._jobs[job_id]
                self._futures.pop(job_id, None)

    def _run(self, job: Job, paths: list[Path]) -> None:
        if job.cancel_requested:
            self._finish(job, "cancelled")
            return
        job.state = "running"
        job.started_at = _now()

        def progress(p: IngestProgress) -> None:
            if job.cancel_requested:
                raise JobCancelled
            job.entries = p.entries
            job.bytes_read = p.bytes_read
            if p.bytes_total:  # extracted size once a ZIP is unpacked, not the upload's
                job.bytes_total = p.bytes_total
            job.current_file = Path(p.current_file).name

        try:
            doc = self._ingest(job, paths, progress)
        except JobCancelled:
            self._finish(job, "cancelled")
            return
        except (OpError, StoreError, OSError, ValueError, LookupError) as exc:
            err = from_exception(exc, "api") or OpError("store_error", str(exc))
            job.error = _scrubbed(err.envelope()["error"])
            self._finish(job, "failed")
            return
        except Exception as exc:  # anything else must still end the job, not leave it running
            err = OpError("internal_error", f"{type(exc).__name__}: {exc}")
            job.error = _scrubbed(err.envelope()["error"])
            self._finish(job, "failed")
            return
        if doc["schema"] == "ingest-capture.v1":  # a split: result is its first set
            sets = doc["sets"]
            job.result, job.capture = sets[0], doc
        else:
            sets = [doc]
            job.result = doc
        job.entries = sum(int(s["entry_count"]) for s in sets)
        job.bytes_read = job.bytes_total
        job.current_file = None
        try:
            if self._on_done is not None:  # cache invalidation: must not leave the job running
                for item in sets:
                    self._on_done(str(item["id"]))
        finally:
            self._finish(job, "succeeded")

    def _ingest(
        self, job: Job, paths: list[Path], progress: Callable[[IngestProgress], None]
    ) -> dict[str, Any]:
        opts = job.options
        target = IngestTarget.parse(
            node=opts.get("node"),
            cluster=opts.get("cluster"),
            capture=opts.get("capture"),
            split=opts.get("split"),
            front="api",
        )
        return ingest_paths(
            self.store,
            paths,
            fmt=opts.get("format"),
            name=job.name,
            frames=opts.get("frames"),
            overwrite=bool(opts.get("overwrite")),
            mine=bool(opts.get("mine")),
            force=bool(opts.get("force")),
            target=target,
            progress=progress,
            progress_every=20_000,
            front="api",
        )
