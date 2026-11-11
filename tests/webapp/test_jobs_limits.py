"""Upload admission (disk space, job queue), pruning of finished jobs, token redirect."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from fastapi.testclient import TestClient

from atlas_log_analyzer.webapp import Settings, create_app
from atlas_log_analyzer.webapp.jobs import KEEP_FINISHED, MAX_ACTIVE_JOBS, Job, JobManager
from atlas_log_analyzer.webapp.state import AppState

from .conftest import MINI


class _Usage(NamedTuple):
    total: int
    used: int
    free: int


def _upload(client: TestClient) -> int:
    with MINI.open("rb") as fh:
        return client.post("/api/ingest", files={"files": ("mini.log", fh)}).status_code


def _spool_empty(store: Path) -> bool:
    spool = store / ".uploads"
    return not spool.exists() or not any(spool.iterdir())


def test_upload_refused_for_disk_space(
    client: TestClient, store_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Too little free space: 507 with a clear message and nothing written."""
    monkeypatch.setattr(
        shutil, "disk_usage", lambda _p: _Usage(10**9, 10**9 - 5 * 2**20, 5 * 2**20)
    )
    with MINI.open("rb") as fh:
        resp = client.post("/api/ingest", files={"files": ("mini.log", fh)})
    assert resp.status_code == 507
    assert "not enough free disk space" in resp.json()["error"]["message"]
    assert "5 MiB free" in resp.json()["error"]["message"]
    assert _spool_empty(store_dir)


def test_upload_refused_for_full_queue(client: TestClient, store_dir: Path) -> None:
    """MAX_ACTIVE_JOBS queued or running: 503 with Retry-After and nothing written."""
    manager = client.app.state.atlas.jobs  # type: ignore[attr-defined]
    for i in range(MAX_ACTIVE_JOBS):
        manager._jobs[f"q{i}"] = Job(f"q{i}", None, [], 0, {}, state="queued")
    resp = None
    with MINI.open("rb") as fh:
        resp = client.post("/api/ingest", files={"files": ("mini.log", fh)})
    assert resp.status_code == 503
    assert int(resp.headers["Retry-After"]) > 0
    assert "retry later" in resp.json()["error"]["message"]
    assert _spool_empty(store_dir)
    manager._jobs.pop("q0")
    assert _upload(client) == 202


def test_queue_filled_while_the_body_was_read(
    client: TestClient, store_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The header check passed, then other uploads filled the queue: submit refuses (503)."""
    manager = client.app.state.atlas.jobs  # type: ignore[attr-defined]
    real_new = manager.new_upload_dir

    def fill_then_allocate() -> tuple[str, Path]:
        for i in range(MAX_ACTIVE_JOBS):  # simultaneous uploads submitted in the meantime
            manager._jobs[f"r{i}"] = Job(f"r{i}", None, [], 0, {}, state="queued")
        return real_new()

    monkeypatch.setattr(manager, "new_upload_dir", fill_then_allocate)
    with MINI.open("rb") as fh:
        resp = client.post("/api/ingest", files={"files": ("mini.log", fh)})
    assert resp.status_code == 503
    assert int(resp.headers["Retry-After"]) > 0
    assert manager.active_count() == MAX_ACTIVE_JOBS
    assert _spool_empty(store_dir)


@pytest.fixture
def manager(tmp_path: Path) -> Iterator[JobManager]:
    """A synchronous job manager over an empty store."""
    state = AppState(Settings(store=tmp_path / "s", sync_jobs=True))
    yield state.jobs


def test_pruning_keeps_recent_finished_and_all_active(manager: JobManager) -> None:
    """Only the newest KEEP_FINISHED finished jobs stay; running and queued jobs never go."""
    for i in range(3):
        manager._jobs[f"a{i:04d}"] = Job(
            f"a{i:04d}", None, [], 0, {}, state="running" if i else "queued"
        )
    total = KEEP_FINISHED + 20
    for i in range(total):
        job = Job(f"b{i:04d}", None, [], 0, {}, state="running")
        manager._jobs[job.id] = job
        manager._finish(job, "succeeded")
    assert {j for j in manager._jobs if j.startswith("a")} == {"a0000", "a0001", "a0002"}
    finished = sorted(j for j in manager._jobs if j.startswith("b"))
    assert len(finished) == KEEP_FINISHED
    assert finished[0] == f"b{total - KEEP_FINISHED:04d}"
    assert manager.get("b0000") is None


def test_pruned_job_is_not_found(client: TestClient) -> None:
    """A pruned job answers like an unknown one."""
    assert client.get("/api/jobs/b0000").status_code == 404


def test_token_redirect_is_relative(store_dir: Path) -> None:
    """The first-visit redirect has no scheme or host and keeps the other parameters."""
    with TestClient(create_app(Settings(store=store_dir, token="s3cret", sync_jobs=True))) as c:
        resp = c.get("/sets?a=1&token=s3cret&b=2", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/sets?a=1&b=2"
        resp = c.get("/?token=s3cret", follow_redirects=False)
        assert resp.headers["location"] == "/"
