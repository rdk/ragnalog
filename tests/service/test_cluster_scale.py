"""Cluster builders over two synthetic nodes of 200k entries each (``pytest -m slow``)."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import msgspec
import pytest

from atlas_log_analyzer.core.signature import SignatureSpec
from atlas_log_analyzer.service import cluster as C
from atlas_log_analyzer.store import Store
from tests.store.synth import WEIGHTS_A, WEIGHTS_B, FakeFormat, MiniGrouper, generate, source_files

N = 200_000


@pytest.mark.slow
def test_cluster_builders_on_two_large_nodes(tmp_path: Path) -> None:
    store = Store(tmp_path / "store")
    members = []
    for k, weights in enumerate((WEIGHTS_A, WEIGHTS_B)):
        sources = source_files(["atlassian-jira.log.1", "atlassian-jira.log"], root=f"/node{k}")
        signed = list(generate(sources, weights, N // 2, offset=k * 3))[:N]
        grouper = MiniGrouper()
        for se in signed:
            grouper.add(se)
        ls = store.ingest(
            iter(signed),
            lambda g=grouper: g.groups.values(),
            sources,
            FakeFormat(),  # type: ignore[arg-type]
            spec=SignatureSpec(),
        )
        members.append((f"NODE{k + 1}", ls))
    capture = C.open_capture(members)
    window = C.resolve_window(capture)

    timings: dict[str, float] = {}

    def timed(name: str, call: Callable[[], object]) -> object:
        t0 = time.perf_counter()
        result = call()
        timings[name] = time.perf_counter() - t0
        return result

    timed("capture", lambda: C.capture_summary(capture, window=window))
    timed("timeline", lambda: C.cluster_timeline(capture, window=window, bucket="15m"))
    timed("matrix", lambda: C.matrix(capture, window=window))
    timed("events", lambda: C.events(capture, window=window))
    timed("interactions", lambda: C.interactions(capture, window=window))
    timed("merged", lambda: C.merged_entries(capture, window=window, offset=N // 2))
    payload = timed("payload", lambda: C.cluster_payload(capture, window=window))
    size = len(msgspec.json.encode(payload))
    print("\n" + ", ".join(f"{k} {v:.2f}s" for k, v in timings.items()) + f", {size / 1e6:.1f} MB")
    assert all(v < 10 for k, v in timings.items() if k != "payload"), timings
    assert timings["payload"] < 30 and size < 12_000_000
