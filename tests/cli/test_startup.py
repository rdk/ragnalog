"""Startup cost guard: commands load only the modules they need (docs/design.md, B-P2).

Each check runs in a fresh interpreter so ``sys.modules`` starts clean.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from .conftest import MINI_LOG, Invoke

HEAVY_FOR_READS = (
    "duckdb",
    "pyarrow.compute",
    "rich.table",
    "atlas_log_analyzer.store.pipeline",
    "atlas_log_analyzer.store.writer",
)
"""Not needed by a JSON read command: SQL engine, writer, ingest pipeline, tables."""


def _loaded_after(argv: list[str], modules: tuple[str, ...]) -> tuple[int, list[str]]:
    script = textwrap.dedent(
        """
        import contextlib, io, json, sys
        from atlas_log_analyzer.cli.app import run
        with contextlib.redirect_stdout(io.StringIO()):
            code = run(json.loads(sys.argv[1]))
        print(json.dumps([code, [m for m in json.loads(sys.argv[2]) if m in sys.modules]]))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, json.dumps(argv), json.dumps(modules)],
        capture_output=True,
        text=True,
        check=True,
    )
    code, loaded = json.loads(proc.stdout)
    return code, loaded


@pytest.fixture
def mini_store(jcli: Invoke, store_dir: Path) -> Path:
    assert jcli("ingest", MINI_LOG, "--name", "mini").code == 0
    return store_dir


def test_json_groups_loads_no_writer_sql_or_tables(mini_store: Path) -> None:
    argv = ["--output", "json", "--store", str(mini_store), "groups", "mini"]
    assert _loaded_after(argv, HEAVY_FOR_READS) == (0, [])


@pytest.mark.parametrize(
    "command", [["version"], ["formats"], ["schema", "triage.v1"], ["manifest"], []]
)
def test_metadata_commands_load_no_polars(command: list[str], tmp_path: Path) -> None:
    argv = ["--output", "json", "--store", str(tmp_path / "none"), *command]
    assert _loaded_after(argv, ("polars", *HEAVY_FOR_READS)) == (0, [])


def test_sets_reads_manifests_without_polars(mini_store: Path) -> None:
    """``sets`` lists manifests and summarises groups with pyarrow only (no polars)."""
    argv = ["--output", "json", "--store", str(mini_store), "sets"]
    assert _loaded_after(argv, ("polars", *HEAVY_FOR_READS)) == (0, [])
    brief = [*argv, "--brief"]
    assert _loaded_after(brief, ("polars", *HEAVY_FOR_READS)) == (0, [])
