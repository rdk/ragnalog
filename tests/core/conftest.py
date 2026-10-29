from __future__ import annotations

from pathlib import Path

import pytest

from atlas_log_analyzer.core.formats.jira import JIRA_RULES, JiraFormat
from atlas_log_analyzer.core.masking import GENERIC_RULES, Masker

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "jira"
SAMPLE = FIXTURES / "sample.log"
EDGE_CASES = FIXTURES / "edge_cases.log"


@pytest.fixture
def jira() -> JiraFormat:
    return JiraFormat()


@pytest.fixture
def masker() -> Masker:
    return Masker([*JIRA_RULES, *GENERIC_RULES])
