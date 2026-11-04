"""Generated documentation stays in sync with ``store/schema.py``."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def _generator() -> ModuleType:
    path = ROOT / "docs" / "formats" / "generate.py"
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
        path = ROOT / "docs" / "formats" / f"{name}.v1.md"
        assert path.read_text(encoding="utf-8") == gen.render(name), path.name
