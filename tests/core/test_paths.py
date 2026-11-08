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
        ("COM¹.log", "_COM¹.log"),  # superscript digits name devices too
        ("lpt³", "_lpt³"),
        ("aux .txt", "_aux .txt"),  # spaces before the extension are ignored
    ],
)
def test_device_names_get_a_prefix(name: str, safe: str) -> None:
    assert windows_safe(name) == safe


def test_safe_name() -> None:
    assert safe_name("prod 2026/10", "x") == "prod_2026_10"
    assert safe_name("...", "fallback") == "fallback"
    assert safe_name("Con", "x") == "_Con"
    assert safe_name("COM¹.log", "x") == "COM_.log"  # ASCII only
    assert safe_name("název.log", "x") == "n_zev.log"
