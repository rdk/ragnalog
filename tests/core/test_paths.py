from __future__ import annotations

import pytest

from atlas_log_analyzer.core.paths import safe_name, windows_safe


@pytest.mark.parametrize(
    ("name", "safe"),
    [
        ("nul.log", "_nul.log"),
        ("AUX", "_AUX"),
        ("com1.log.1", "_com1.log.1"),
        ("lpt9", "_lpt9"),
        ("console.log", "console.log"),
        ("com10.log", "com10.log"),
        ("atlassian-jira.log", "atlassian-jira.log"),
    ],
)
def test_device_names_get_a_prefix(name: str, safe: str) -> None:
    assert windows_safe(name) == safe


def test_safe_name() -> None:
    assert safe_name("prod 2026/10", "x") == "prod_2026_10"
    assert safe_name("...", "fallback") == "fallback"
    assert safe_name("Con", "x") == "_Con"
