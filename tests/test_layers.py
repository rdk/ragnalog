"""Layering (AGENTS.md, "Dependency direction"): service builds documents, front ends render."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "atlas_log_analyzer"

FRONT_END_FORBIDDEN = (
    "atlas_log_analyzer.store.query",
    "atlas_log_analyzer.store.triage",
    "atlas_log_analyzer.store.regroup",
    "atlas_log_analyzer.store.writer",
)
"""Store modules that compare or write sets: the front ends reach them through service."""
PIPELINE_TYPES = {"IngestProgress", "SourceSelection"}
"""The only names of ``store.pipeline`` a front end may import (progress callbacks)."""


def _imports(path: Path) -> list[tuple[str, list[str]]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(alias.name, []) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append((node.module, [alias.name for alias in node.names]))
    return out


def _files(*packages: str) -> list[Path]:
    return [p for pkg in packages for p in sorted((PACKAGE / pkg).rglob("*.py"))]


@pytest.mark.parametrize("path", _files("service"), ids=lambda p: p.name)
def test_service_imports_no_front_end(path: Path) -> None:
    for module, _ in _imports(path):
        top = module.split(".")[0]
        assert top not in {"cyclopts", "fastapi", "starlette", "rich"}, (path.name, module)
        assert not module.startswith(("atlas_log_analyzer.cli", "atlas_log_analyzer.webapp"))


@pytest.mark.parametrize("path", _files("cli", "webapp"), ids=lambda p: p.name)
def test_front_ends_do_not_compare_or_write_sets_themselves(path: Path) -> None:
    for module, names in _imports(path):
        assert module not in FRONT_END_FORBIDDEN, (path.name, module)
        if module == "atlas_log_analyzer.store.pipeline":
            assert set(names) <= PIPELINE_TYPES, (path.name, names)


@pytest.mark.parametrize("path", _files("core", "store"), ids=lambda p: p.name)
def test_core_and_store_import_no_upper_layer(path: Path) -> None:
    upper = ("atlas_log_analyzer.cli", "atlas_log_analyzer.webapp", "atlas_log_analyzer.service")
    for module, _ in _imports(path):
        if path.name == "views.py" and path.parent.name == "store":
            continue  # the re-export shim of service.views
        assert not module.startswith(upper), (path.name, module)


@pytest.mark.parametrize("path", _files("webapp"), ids=lambda p: p.name)
def test_webapp_imports_no_cli(path: Path) -> None:
    for module, _ in _imports(path):
        assert not module.startswith("atlas_log_analyzer.cli"), (path.name, module)
