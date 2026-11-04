import tomllib
from pathlib import Path

from atlas_log_analyzer import __version__
from atlas_log_analyzer._version import __version__ as literal

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_version_literal_matches_pyproject() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert literal == project["version"]
    assert __version__ == literal
