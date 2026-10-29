from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from atlas_log_analyzer.core.reader import SourceFile
from atlas_log_analyzer.core.signature import SignatureSpec
from atlas_log_analyzer.store import LogSet, Store

from .synth import WEIGHTS_A, WEIGHTS_B, FakeFormat, MiniGrouper, generate, source_files

N_PER_SOURCE = 100
DUPLICATES = 10
PARSE_ERRORS = 3


@dataclass
class Built:
    store: Store
    logset: LogSet
    sources: list[SourceFile]
    grouper: MiniGrouper
    emitted: int


def build(
    store: Store,
    names: list[str],
    weights: dict[str, int],
    *,
    name: str | None = None,
    root: str = "/logs",
    duplicates: int = DUPLICATES,
    parse_errors: int = PARSE_ERRORS,
    n_per_source: int = N_PER_SOURCE,
    spec: SignatureSpec | None = None,
    overwrite: bool = False,
    grouper_dedups: bool = True,
) -> Built:
    sources = source_files(names, root)
    grouper = MiniGrouper(dedup=grouper_dedups)
    emitted = 0

    def counted():  # type: ignore[no-untyped-def]
        nonlocal emitted
        for se in grouper.track(
            generate(
                sources,
                weights,
                n_per_source,
                duplicates=duplicates,
                parse_errors=parse_errors,
            )
        ):
            emitted += 1
            yield se

    ls = store.ingest(
        counted(),
        lambda: grouper.groups.values(),
        sources,
        FakeFormat(),  # type: ignore[arg-type]
        name=name,
        spec=spec or SignatureSpec(),
        overwrite=overwrite,
    )
    return Built(store, ls, sources, grouper, emitted)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "store")


@pytest.fixture
def built(store: Store) -> Built:
    return build(store, ["a.log", "b.log", "c.log"], WEIGHTS_A, name="prod-a")


@pytest.fixture
def built_b(store: Store, built: Built) -> Built:
    return build(store, ["d.log", "e.log"], WEIGHTS_B, name="prod-b", root="/other")
