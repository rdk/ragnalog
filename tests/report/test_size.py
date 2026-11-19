"""Size budget of the ``report.v1`` payload (regression guard for payload bloat)."""

from __future__ import annotations

from pathlib import Path

from ragnalog.cli.app import run
from ragnalog.report import build_payload
from ragnalog.report.render import embed_json
from ragnalog.store import Store

MINI_LOG = Path(__file__).parents[1] / "cli" / "data" / "mini.log"
BYTES_PER_ENTRY_CEILING = 2000
"""Generous ceiling; the compact contract measured ~1,000 bytes per example entry."""


def test_payload_bytes_per_example_entry(tmp_path: Path) -> None:
    store = tmp_path / "store"
    code = run(["--output", "json", "--quiet", "--store", str(store), "ingest", str(MINI_LOG),
                "--format", "jira", "--name", "mini"])  # fmt: skip
    assert code == 0
    p = build_payload(Store(store).open("mini"))
    assert p["entries"]
    # Everything but the set-level metadata scales with groups/entries.
    body = {k: p[k] for k in ("groups", "entries", "merges", "compare")}
    size = len(embed_json(body).encode("utf-8"))
    assert size / len(p["entries"]) <= BYTES_PER_ENTRY_CEILING, size / len(p["entries"])
