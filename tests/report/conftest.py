"""Fixtures for report tests: the Jira fixtures ingested once per module into a tmp store.

Sets: ``sample`` (``sample.log``, with Drain3 merge proposals), ``edge``
(``edge_cases.log``, same signature spec, used as the comparison baseline) and
``edge-f2`` (``edge_cases.log`` signed with ``--frames 2``: not comparable).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from atlas_log_analyzer.cli.app import run
from atlas_log_analyzer.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "jira"
SAMPLE_LOG = FIXTURES / "sample.log"
EDGE_LOG = FIXTURES / "edge_cases.log"


def _ingest(store: Path, path: Path, name: str, *extra: str) -> None:
    code = run(["--output", "json", "--quiet", "--store", str(store), "ingest", str(path),
                "--format", "jira", "--name", name, *extra])  # fmt: skip
    assert code == 0, f"ingest {path} failed with {code}"


@pytest.fixture(scope="module")
def store_dir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("report-store")
    store = root / "store"
    _ingest(store, SAMPLE_LOG, "sample", "--mine")
    _ingest(store, EDGE_LOG, "edge")
    # The same file again under another id/spec: copy it so the digest-based id differs.
    other = root / "f2" / "edge_cases.log"
    other.parent.mkdir()
    shutil.copyfile(EDGE_LOG, other)
    with other.open("ab") as fh:
        fh.write(b"\n")
    _ingest(store, other, "edge-f2", "--frames", "2")
    yield store


@pytest.fixture(scope="module")
def store(store_dir: Path) -> Store:
    return Store(store_dir)


def report_data(html: str) -> object:
    """The embedded payload of a rendered page."""
    start = html.index('<script type="application/json" id="report-data">')
    start = html.index(">", start) + 1
    end = html.index("</script>", start)
    return json.loads(html[start:end])
