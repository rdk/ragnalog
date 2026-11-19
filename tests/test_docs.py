"""Generated documentation stays in sync with ``store/schema.py`` and ``service/documents.py``."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from ragnalog.service.documents import document_names, document_schema

ROOT = Path(__file__).resolve().parents[1]
FORMATS = ROOT / "docs" / "formats"


def _generator() -> ModuleType:
    path = FORMATS / "generate.py"
    spec = importlib.util.spec_from_file_location("docs_formats_generate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_md_generated_blocks_are_current() -> None:
    gen = _generator()
    text = (ROOT / "docs" / "cli.md").read_text(encoding="utf-8")
    assert {m["name"] for m in gen.BLOCK_RE.finditer(text)} == set(gen.cli_blocks())
    assert gen.render_cli_md(text) == text, "run `uv run python docs/formats/generate.py`"


def test_format_docs_are_current() -> None:
    gen = _generator()
    for name in ("entries", "groups", "merges", "logset"):
        path = FORMATS / f"{name}.v1.md"
        assert path.read_text(encoding="utf-8") == gen.render(name), path.name


def test_every_document_has_current_generated_files() -> None:
    gen = _generator()
    files = gen.outputs()
    for name in document_names():
        assert FORMATS / f"{name}.schema.json" in files
        assert (FORMATS / f"{name}.md").exists(), f"{name}.md"
    for path, text in files.items():
        assert path.exists(), f"run `uv run python docs/formats/generate.py` ({path.name})"
        assert path.read_text(encoding="utf-8") == text, path.name


def test_schema_files_are_the_document_schemas() -> None:
    triage = json.loads((FORMATS / "triage.v1.schema.json").read_text(encoding="utf-8"))
    assert triage == document_schema("triage.v1")
    assert triage["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert "DiffRow" in triage["$defs"] and "TriageBaseline" in triage["$defs"]


def test_table_docs_keep_their_columns_and_gain_the_document() -> None:
    text = (FORMATS / "entries.v1.md").read_text(encoding="utf-8")
    assert text.index("| `exception_chain` |") < text.index("## JSON document `entries.v1`")
    assert "| `next_offset` |" in text and "| `body_truncated` |" in text


def test_report_md_stays_hand_written() -> None:
    text = (FORMATS / "report.v1.md").read_text(encoding="utf-8")
    assert "Hand-written" in text and "report.v1.schema.json" in text
