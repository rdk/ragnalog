from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from atlas_log_analyzer.core.formats.jira import JiraFormat
from atlas_log_analyzer.core.parser import parse_file

from .conftest import SAMPLE

BENCH_FILE = Path("logs_01") / "atlassian-jira.log.3"
MIN_MB_PER_S = 30.0


def _run(path: Path) -> tuple[int, int, float]:
    fmt = JiraFormat()
    start = time.perf_counter()
    entries = errors = 0
    for entry in parse_file(path, fmt):
        entries += 1
        if entry.parse_error:
            errors += 1
    return entries, errors, time.perf_counter() - start


def test_throughput_smoke() -> None:
    entries, errors, elapsed = _run(SAMPLE)
    mb = SAMPLE.stat().st_size / 1e6
    print(f"\nsample: {entries} entries, {errors} parse errors, {mb / max(elapsed, 1e-9):.1f} MB/s")
    assert entries > 0


@pytest.mark.skipif(not os.environ.get("ATLAS_LOG_DATA"), reason="ATLAS_LOG_DATA not set")
def test_benchmark_real_file() -> None:
    path = Path(os.environ["ATLAS_LOG_DATA"]) / BENCH_FILE
    if not path.is_file():
        pytest.skip(f"{path} not found")
    _run(path)  # warm-up (regex compilation, OS file cache)
    entries, errors, elapsed = _run(path)
    mb_per_s = path.stat().st_size / 1e6 / elapsed
    print(f"\n{path.name}: {entries} entries, {errors} parse errors, {mb_per_s:.1f} MB/s")
    assert mb_per_s >= MIN_MB_PER_S
