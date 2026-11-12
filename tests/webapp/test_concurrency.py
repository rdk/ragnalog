"""The web app during and around ingest jobs: no store-wide lock, spools, one server per store."""

from __future__ import annotations

import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import atlas_log_analyzer.webapp.jobs as jobs_mod
from atlas_log_analyzer.store.store import Store
from atlas_log_analyzer.webapp import Settings, create_app
from atlas_log_analyzer.webapp.jobs import UPLOADS_DIR, JobManager
from atlas_log_analyzer.webapp.state import SERVER_LOCK_FILE, StoreInUse

from .conftest import MINI

TIMEOUT_S = 20


def _blocked_ingest(monkeypatch: pytest.MonkeyPatch) -> tuple[threading.Event, threading.Event]:
    """Make job ingests wait for ``release`` after signalling ``started``."""
    started, release = threading.Event(), threading.Event()
    real = jobs_mod.ingest_paths

    def ingest_paths(*args: Any, **kwargs: Any) -> Any:
        started.set()
        assert release.wait(TIMEOUT_S), "test never released the ingest"
        return real(*args, **kwargs)

    monkeypatch.setattr(jobs_mod, "ingest_paths", ingest_paths)
    return started, release


def test_reads_answer_while_an_ingest_runs(
    store_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, release = _blocked_ingest(monkeypatch)
    with TestClient(create_app(Settings(store=store_dir))) as client:
        try:
            upload = MINI.read_bytes() + b"\n"  # other content than the stored "mini" set
            res = client.post("/api/ingest", files={"files": ("mini2.log", upload)})
            assert res.status_code == 202, res.text
            job_id = res.json()["id"]
            assert started.wait(TIMEOUT_S)
            with ThreadPoolExecutor(1) as pool:
                health = pool.submit(client.get, "/api/health").result(timeout=TIMEOUT_S)
                sets = pool.submit(client.get, "/api/sets").result(timeout=TIMEOUT_S)
                page = pool.submit(client.get, "/sets/mini").result(timeout=TIMEOUT_S)
            assert health.status_code == 200 and health.json()["sets"] == 2
            assert sets.status_code == 200 and sets.json()["count"] == 2
            assert page.status_code == 200
            assert client.get(f"/api/jobs/{job_id}").json()["state"] == "running"
        finally:
            release.set()
        deadline = time.monotonic() + TIMEOUT_S
        while (job := client.get(f"/api/jobs/{job_id}").json())["state"] == "running":
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert job["state"] == "succeeded", job
        assert client.get("/api/sets").json()["count"] == 3


def test_second_server_on_the_same_store_fails(store_dir: Path) -> None:
    with TestClient(create_app(Settings(store=store_dir, sync_jobs=True))) as first:
        assert (store_dir / SERVER_LOCK_FILE).is_file()
        second = TestClient(create_app(Settings(store=store_dir, sync_jobs=True)))
        with pytest.raises(StoreInUse, match="already using the data directory"), second:
            pass
        assert first.get("/api/health").status_code == 200
    # released at shutdown
    with TestClient(create_app(Settings(store=store_dir, sync_jobs=True))) as again:
        assert again.get("/api/health").status_code == 200


def test_serve_refuses_a_store_that_is_already_served(store_dir: Path) -> None:
    from atlas_log_analyzer.cli.app import run

    with TestClient(create_app(Settings(store=store_dir, sync_jobs=True))):
        assert run(["--output", "json", "--store", str(store_dir), "serve", "--port", "1"]) == 3


def test_startup_deletes_orphaned_spools(store_dir: Path) -> None:
    orphan = store_dir / UPLOADS_DIR / "18f00000001"
    orphan.mkdir(parents=True)
    (orphan / "x.log").write_bytes(b"left by a crashed server")
    (store_dir / UPLOADS_DIR / "stray.tmp").write_bytes(b"")
    with TestClient(create_app(Settings(store=store_dir, sync_jobs=True))) as client:
        assert list((store_dir / UPLOADS_DIR).iterdir()) == []
        assert client.get("/api/sets").json()["count"] == 2


def test_shutdown_deletes_spools_of_queued_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, release = _blocked_ingest(monkeypatch)
    manager = JobManager(Store(tmp_path / "store"))
    jobs = []
    for name in ("a.log", "b.log"):
        job_id, folder = manager.new_upload_dir()
        shutil.copy(MINI, folder / name)
        jobs.append(manager.submit(job_id, [folder / name], name=None))
    running, queued = jobs
    assert started.wait(TIMEOUT_S)
    stopper = threading.Thread(target=manager.shutdown)
    stopper.start()
    try:
        deadline = time.monotonic() + TIMEOUT_S
        while not manager._futures[queued.id].cancelled():
            assert time.monotonic() < deadline
            time.sleep(0.01)
    finally:
        release.set()
        stopper.join(TIMEOUT_S)
    assert not stopper.is_alive()
    assert running.state == "succeeded" and queued.state == "cancelled"
    assert list(manager.spool.iterdir()) == []


def test_server_runs_unlocked_when_the_lock_file_cannot_be_created(
    store_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atlas_log_analyzer.store.locks import FileLock

    def refuse(self: FileLock, timeout: float = 0.0) -> bool:
        raise PermissionError(13, "read-only (simulated)", str(self.path))

    monkeypatch.setattr(FileLock, "acquire", refuse)
    orphan = store_dir / UPLOADS_DIR / "18f00000001"
    orphan.mkdir(parents=True)
    with TestClient(create_app(Settings(store=store_dir, sync_jobs=True))) as client:
        assert client.get("/api/health").json()["sets"] == 2
    assert orphan.is_dir()  # without the lock another server may own the spools
