"""Web app fixtures: an app over a temporary store with the masked fixture logs."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from atlas_log_analyzer.store.pipeline import ingest_paths
from atlas_log_analyzer.store.store import Store
from atlas_log_analyzer.webapp import Settings, create_app

ROOT = Path(__file__).resolve().parents[1]
MINI = ROOT / "cli" / "data" / "mini.log"
SAMPLE = ROOT / "fixtures" / "jira" / "sample.log"


@pytest.fixture
def store_dir(tmp_path: Path) -> Path:
    """A store with two sets: ``sample`` (the older baseline) and ``mini``."""
    root = tmp_path / "store"
    store = Store(root)
    ingest_paths(store, [SAMPLE], name="sample")
    ingest_paths(store, [MINI], name="mini")
    return root


def _client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def client(store_dir: Path) -> Iterator[TestClient]:
    """Client of an app over :func:`store_dir`; ingest jobs run synchronously."""
    yield from _client(Settings(store=store_dir, sync_jobs=True))


@pytest.fixture
def empty_client(tmp_path: Path) -> Iterator[TestClient]:
    """Client of an app over an empty store."""
    yield from _client(Settings(store=tmp_path / "empty", sync_jobs=True))
