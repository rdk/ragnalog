"""Store locking: the OS file lock, the install critical section and in-use retries."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import atlas_log_analyzer.store.store as store_mod
from atlas_log_analyzer.core.signature import SignatureSpec
from atlas_log_analyzer.store import Store, StoreError
from atlas_log_analyzer.store.locks import FileLock

from .conftest import Built, build
from .synth import WEIGHTS_A, WEIGHTS_B, FakeFormat, generate, source_files


def test_file_lock_excludes_until_released(tmp_path: Path) -> None:
    a, b = FileLock(tmp_path / "x.lock"), FileLock(tmp_path / "x.lock")
    assert a.acquire() and a.held
    started = time.monotonic()
    assert not b.acquire(timeout=0.2)
    assert time.monotonic() - started >= 0.2
    assert not b.held
    a.release()
    a.release()  # no-op
    assert b.acquire()
    with pytest.raises(RuntimeError, match="already locked"):
        b.acquire()
    b.release()


def test_name_taken_while_parsing_is_refused_at_install(store: Store) -> None:
    sources = source_files(["x.log"])

    def stream():  # type: ignore[no-untyped-def]
        yield from generate(sources, WEIGHTS_A, 10)
        # another writer installs a set under the same name before this one finishes
        build(store, ["y.log"], WEIGHTS_B, name="night", root="/other")

    with pytest.raises(StoreError, match="already used"):
        store.ingest(stream(), list, sources, FakeFormat(), name="night", spec=SignatureSpec())
    assert [m.name for m in store.list()] == ["night"]
    assert not [p for p in store.root.iterdir() if p.name.startswith(".tmp-")]


def test_same_set_installed_while_parsing_is_reused(store: Store) -> None:
    names = ["x.log"]
    sources = source_files(names)

    def stream():  # type: ignore[no-untyped-def]
        yield from generate(sources, WEIGHTS_A, 10)
        build(store, names, WEIGHTS_A, name="first")

    ls = store.ingest(stream(), list, sources, FakeFormat(), name="second", spec=SignatureSpec())
    assert ls.manifest.name == "first"
    assert [m.name for m in store.list()] == ["first"]
    assert not [p for p in store.root.iterdir() if p.name.startswith(".tmp-")]


def _flaky_replace(monkeypatch: pytest.MonkeyPatch, failures: int) -> list[int]:
    """Make the store's ``os.replace`` raise ``PermissionError`` ``failures`` times."""
    calls = [0]
    real = os.replace

    def replace(src: Path, dst: Path) -> None:
        calls[0] += 1
        if calls[0] <= failures:
            raise PermissionError(13, "in use (simulated)", str(src))
        real(src, dst)

    monkeypatch.setattr(store_mod, "RETRY_DELAYS_S", (0.0, 0.0, 0.0))
    monkeypatch.setattr(store_mod.os, "replace", replace)
    return calls


def test_remove_retries_while_in_use(built: Built, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _flaky_replace(monkeypatch, failures=2)
    built.store.remove("prod-a")
    assert calls[0] == 3
    assert built.store.list() == []
    assert [p.name for p in built.store.root.iterdir() if not p.name.startswith(".")] == []
    assert not list(built.store.root.glob(".trash-*"))


def test_remove_that_keeps_failing_leaves_the_set(
    built: Built, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flaky_replace(monkeypatch, failures=100)
    with pytest.raises(StoreError, match="cannot remove"):
        built.store.remove("prod-a")
    assert [m.name for m in built.store.list()] == ["prod-a"]


def test_overwrite_retries_while_in_use(built: Built, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _flaky_replace(monkeypatch, failures=1)
    again = build(built.store, ["a.log", "b.log", "c.log"], WEIGHTS_A, overwrite=True)
    assert calls[0] == 3  # old set → trash (retried once), tmp → set
    assert again.logset.manifest.id == built.logset.manifest.id
    assert len(built.store.list()) == 1


def test_install_waits_for_another_process(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    store.root.mkdir(parents=True)
    other = FileLock(store.root / store_mod.WRITE_LOCK_FILE)  # stands in for another process
    assert other.acquire()
    monkeypatch.setattr(store_mod, "WRITE_LOCK_TIMEOUT_S", 0.1)
    try:
        with pytest.raises(StoreError, match="locked by another writer"):
            build(store, ["x.log"], WEIGHTS_A)
    finally:
        other.release()
    assert store.list() == []
    assert not [p for p in store.root.iterdir() if p.name.startswith(".tmp-")]
    build(store, ["x.log"], WEIGHTS_A)
    assert len(store.list()) == 1


def test_writes_work_without_a_lock_file(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(self: FileLock, timeout: float = 0.0) -> bool:
        raise PermissionError(13, "read-only (simulated)", str(self.path))

    monkeypatch.setattr(FileLock, "acquire", refuse)
    built = build(store, ["x.log"], WEIGHTS_A, name="x")
    store.remove("x")
    assert store.list() == [] and built.logset.manifest.name == "x"


def test_overwrite_that_keeps_failing_is_a_store_error(
    built: Built, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flaky_replace(monkeypatch, failures=100)
    with pytest.raises(StoreError, match="in use"):
        build(built.store, ["a.log", "b.log", "c.log"], WEIGHTS_A, overwrite=True)
    monkeypatch.undo()
    assert [m.name for m in built.store.list()] == ["prod-a"]
