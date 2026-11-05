"""Golden signatures: the fixtures' signatures change only on purpose.

``signatures.json`` holds, per fixture file of every registered format, an xxh3
digest of its sorted ``(signature, coarse_signature)`` pairs and the entry count,
under the recipe key of the format's default spec (format version, spec,
``SIGNATURE_VERSION``, ``MASK_RULES_VERSION``). A change of the masking rules, of
the signature algorithm or of the entries a format builds that moves any signature
fails here; stored sets made by older versions would no longer diff cleanly against
new ones, which is what the versions record.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import xxhash

from atlas_log_analyzer.core import formats
from atlas_log_analyzer.core.parser import parse_file
from atlas_log_analyzer.core.pipeline import Recipe

GOLDEN = Path(__file__).with_name("signatures.json")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MESSAGE = (
    "signatures changed: bump SIGNATURE_VERSION, MASK_RULES_VERSION or the format's version "
    "if intended, then run `uv run pytest --update-golden`"
)


def _fixture_files() -> list[tuple[str, Path]]:
    return [
        (name, path) for name in formats.names() for path in sorted((FIXTURES / name).glob("*.log"))
    ]


def _recipe(format_name: str) -> Recipe:
    fmt = formats.get(format_name)
    return Recipe(fmt, fmt.signature_spec)


def _key(format_name: str) -> str:
    return _recipe(format_name).key


def _digest(format_name: str, path: Path) -> dict[str, Any]:
    fmt = formats.get(format_name)
    grouper = _recipe(format_name).grouper()
    pairs = sorted((s.signature.hash, s.coarse.hash) for s in grouper.sign(parse_file(path, fmt)))
    text = "\n".join(f"{sig} {coarse}" for sig, coarse in pairs) + f"\ncount {len(pairs)}"
    return {"count": len(pairs), "xxh3": xxhash.xxh3_64_hexdigest(text.encode())}


def _current() -> dict[str, dict[str, Any]]:
    doc: dict[str, dict[str, Any]] = {}
    for name, path in _fixture_files():
        doc.setdefault(_key(name), {})[f"{name}/{path.name}"] = _digest(name, path)
    return doc


def test_fixtures_exist() -> None:
    assert _fixture_files(), "no fixtures under tests/fixtures/<format>/"


def test_signatures_match_golden(update_golden: bool) -> None:
    current = _current()
    if update_golden:
        GOLDEN.write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        pytest.skip(f"rewrote {GOLDEN.name}")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for key, files in current.items():
        if key not in golden:
            pytest.fail(f"no golden signatures for {key}: run `uv run pytest --update-golden`")
        assert files == golden[key], MESSAGE
