from __future__ import annotations

import time
from pathlib import Path

from atlas_log_analyzer.core.signature import SignatureSpec
from atlas_log_analyzer.store import Store

from .synth import WEIGHTS_A, FakeFormat, MiniGrouper, generate, source_files

N = 200_000


def test_ingest_200k(tmp_path: Path) -> None:
    sources = source_files(["big.log.2", "big.log.1", "big.log"])
    per_source = N // len(sources) + 1

    # Pre-generate so the timing covers the store only.
    signed = list(generate(sources, WEIGHTS_A, per_source))[:N]
    grouper = MiniGrouper()
    for se in signed:
        grouper.add(se)

    store = Store(tmp_path / "store")
    t0 = time.perf_counter()
    ls = store.ingest(
        iter(signed),
        lambda: grouper.groups.values(),
        sources,
        FakeFormat(),  # type: ignore[arg-type]
        spec=SignatureSpec(),
    )
    elapsed = time.perf_counter() - t0

    size = ls.entries_path.stat().st_size
    print(
        f"\ningest {N} entries: {elapsed:.2f}s, {N / elapsed:,.0f} rows/s, "
        f"entries.parquet {size / 1e6:.1f} MB ({size / N:.0f} B/row)"
    )
    assert ls.manifest.entry_count == N
    assert elapsed < 60

    t0 = time.perf_counter()
    assert ls.groups()["count"].sum() == N
    assert ls.entries(level="ERROR", limit=100).height == 100
    st = ls.stats()
    n = ls.sql("select count(*) n from entries where exception_type is not null")["n"][0]
    print(f"queries (groups, entries, stats, sql): {time.perf_counter() - t0:.2f}s")
    assert st["entry_count"] == N and n > 0
