"""Sweep: interrupted overwrites come back, interrupted removals do not, live writes stay."""

from __future__ import annotations

import os
import time
from pathlib import Path

from ragnalog.store import Store
from ragnalog.store.fresh import keep_fresh
from ragnalog.store.store import LEFTOVER_AGE_S

from .conftest import Built, build
from .synth import WEIGHTS_B


def _age(path: Path, seconds: float) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_interrupted_overwrite_is_restored_by_the_next_sweep(store: Store, built: Built) -> None:
    set_id = built.logset.manifest.id
    aside = store.root / f".replaced-{set_id}-deadbeef"
    os.replace(store.root / set_id, aside)  # killed between the two renames
    _age(aside, 2 * LEFTOVER_AGE_S)
    store._tmp_dir("other").rmdir()  # any write sweeps
    assert not aside.exists()
    assert [m.id for m in store.list()] == [set_id]
    assert store.open(set_id).manifest.entry_count == built.logset.manifest.entry_count


def test_newest_replaced_copy_wins_and_stale_ones_are_deleted(store: Store, built: Built) -> None:
    set_id = built.logset.manifest.id
    older, newer = (store.root / f".replaced-{set_id}-{h}" for h in ("00000001", "00000002"))
    os.replace(store.root / set_id, older)
    newer.mkdir()
    (newer / "marker").write_text("x")
    _age(older, 100)
    store._tmp_dir("other").rmdir()
    assert (store.root / set_id / "marker").is_file()
    assert older.exists() and not newer.exists()  # the other is young: kept until a day old


def test_replaced_copy_beside_an_installed_set_is_kept_until_old(
    store: Store, built: Built
) -> None:
    set_id = built.logset.manifest.id
    aside = store.root / f".replaced-{set_id}-cafe0000"
    aside.mkdir()
    store._tmp_dir("other").rmdir()
    assert aside.is_dir()
    _age(aside, 2 * LEFTOVER_AGE_S)
    store._tmp_dir("other").rmdir()
    assert not aside.exists()
    assert store.open(set_id).manifest.id == set_id


def test_interrupted_removal_is_not_restored(store: Store, built: Built) -> None:
    set_id = built.logset.manifest.id
    trash = store.root / f".trash-{set_id}-deadbeef"
    os.replace(store.root / set_id, trash)
    _age(trash, 2 * LEFTOVER_AGE_S)
    store._tmp_dir("other").rmdir()
    assert not trash.exists()
    assert not (store.root / set_id).exists()
    assert store.list() == []


def test_overwrite_leaves_no_replaced_copy(store: Store, built: Built) -> None:
    build(store, ["a.log", "b.log", "c.log"], WEIGHTS_B, name="prod-a", overwrite=True)
    assert [p.name for p in store.root.iterdir() if p.name.startswith(".replaced-")] == []
    assert len(store.list()) == 1


def test_a_long_running_write_keeps_its_tmp_dir_fresh(store: Store) -> None:
    tmp = store._tmp_dir("abc")
    _age(tmp, 2 * LEFTOVER_AGE_S)  # a write that outlived the sweep age
    with keep_fresh(tmp, interval=0.02):
        deadline = time.time() + 5
        while time.time() - tmp.stat().st_mtime > 60 and time.time() < deadline:
            time.sleep(0.02)
        store._tmp_dir("other").rmdir()
    assert tmp.is_dir()


def test_a_forgotten_tmp_dir_is_swept(store: Store) -> None:
    tmp = store._tmp_dir("abc")
    _age(tmp, 2 * LEFTOVER_AGE_S)
    store._tmp_dir("other").rmdir()
    assert not tmp.exists()
