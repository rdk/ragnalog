"""Repository-wide pytest options."""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add ``--update-golden``: rewrite ``tests/golden/*.json`` instead of comparing."""
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="rewrite the golden files under tests/golden/ from the current code",
    )


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    """Whether the run was started with ``--update-golden``."""
    return bool(request.config.getoption("--update-golden"))
