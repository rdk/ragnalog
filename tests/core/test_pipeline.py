"""``core.pipeline``: the recipe key, the stages it builds, and ``unique_entries``."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

import pytest

from ragnalog.core.formats import registry
from ragnalog.core.formats.base import HeaderMatch, LogFormat
from ragnalog.core.formats.jira import JIRA_RULES, JiraFormat
from ragnalog.core.grouping import Grouper
from ragnalog.core.masking import GENERIC_RULES, Masker, MaskRule
from ragnalog.core.models import LogEntry, SourceRef
from ragnalog.core.parser import parse_file
from ragnalog.core.pipeline import (
    Recipe,
    format_version,
    key_format_version,
    recipe_differences,
    recipe_key,
    unique_entries,
)
from ragnalog.core.signature import DEFAULT_SPEC, SignatureSpec, Signer

from .conftest import EDGE_CASES, SAMPLE

TICKET = MaskRule.make("ticket", r"\bTCK-\d+\b")


class Versionless:
    """A third-party format without a ``version`` attribute."""

    name = "versionless"
    description = "test format"
    mask_rules: Sequence[MaskRule] = ()
    signature_spec = DEFAULT_SPEC

    def is_header(self, line: str) -> bool:
        return False

    def parse_header(self, line: str) -> HeaderMatch | None:
        return None

    def build(
        self,
        header: HeaderMatch | None,
        header_line: str,
        continuation: Sequence[str],
        source: SourceRef,
    ) -> LogEntry:
        raise NotImplementedError

    def sniff(self, lines: Sequence[str]) -> float:
        return 0.0


def test_key_of_the_default_recipe() -> None:
    assert Recipe(JiraFormat(), DEFAULT_SPEC).key == "jira@4|L+G+E+F0|sig2|mask3"
    assert Recipe(JiraFormat(), SignatureSpec(frames=2)).key == "jira@4|L+G+E+F2|sig2|mask3"


def test_extra_rules_add_a_digest() -> None:
    plain = Recipe(JiraFormat(), DEFAULT_SPEC)
    ruled = Recipe(JiraFormat(), DEFAULT_SPEC, (TICKET,))
    assert ruled.key.startswith(plain.key + "|rules:") and len(ruled.key.split(":")[-1]) == 16
    other = Recipe(JiraFormat(), DEFAULT_SPEC, (MaskRule.make("ticket", r"\bTCK-\d{2,}\b"),))
    assert other.key != ruled.key
    assert ruled.rules == (TICKET, *JIRA_RULES, *GENERIC_RULES)
    assert ruled.masker().apply("closed TCK-12")[0] == "closed <TICKET>"


def test_format_version_is_optional() -> None:
    fmt = Versionless()
    assert isinstance(fmt, LogFormat)  # the registry's runtime check accepts it
    assert format_version(fmt) == 1 and Recipe(fmt, DEFAULT_SPEC).key.startswith("versionless@1|")
    fmt.version = 3  # type: ignore[attr-defined]
    assert Recipe(fmt, DEFAULT_SPEC).key.startswith("versionless@3|")
    assert format_version(JiraFormat()) == JiraFormat.version == 4
    registry.register(Versionless())
    try:
        assert registry.get("versionless").name == "versionless"
    finally:
        registry._formats.pop("versionless", None)


@pytest.mark.parametrize("spec", [DEFAULT_SPEC, SignatureSpec(frames=2)])
def test_stages_equal_the_hand_built_chain(spec: SignatureSpec) -> None:
    jira = JiraFormat()
    entries = [*parse_file(SAMPLE, jira), *parse_file(EDGE_CASES, jira)]
    old = Grouper(Signer(Masker([*jira.mask_rules, *GENERIC_RULES]), spec, titles_from_body=True))
    new = Recipe(jira, spec).grouper()
    assert [(s.signature, s.coarse) for s in old.sign(entries)] == [
        (s.signature, s.coarse) for s in new.sign(entries)
    ]
    assert old.groups == new.groups
    assert Recipe(jira, spec).signer().spec == spec


def test_recipe_key_parts_and_differences() -> None:
    key = recipe_key("jira", 1, "L+G+E+F0", mask_rules_version=1)
    assert key == "jira@1|L+G+E+F0|sig2|mask1"
    assert recipe_key("jira", 2, "L+G+E+F0", mask_rules_version=3, rules="ab") == (
        "jira@2|L+G+E+F0|sig2|mask3|rules:ab"
    )
    assert recipe_differences(key, key) == []
    diffs = recipe_differences(key, "jira@1|L+G+E+F2|sig2|mask2")
    assert diffs == ["signature_spec: L+G+E+F0 != L+G+E+F2", "mask_rules_version: mask1 != mask2"]
    assert recipe_differences(key, key + "|rules:ab") == ["rules: none != ab"]
    assert recipe_differences(key, "jira@2|L+G+E+F0|sig2|mask1") == ["format: jira@1 != jira@2"]


def test_key_format_version() -> None:
    assert key_format_version("jira@2|L+G+E+F0|sig2|mask1") == 2
    assert key_format_version("acme.v2@13|L+G+E+F0|sig2|mask1|rules:ab") == 13
    assert key_format_version("jira|L+G+E+F0") is None
    assert key_format_version("") is None


@pytest.mark.parametrize(
    "spec",
    [
        DEFAULT_SPEC,
        SignatureSpec(frames=3),
        SignatureSpec(include_exceptions=False),
        SignatureSpec(include_level=False, include_logger=False, frames=1),
    ],
)
def test_spec_from_key_round_trip(spec: SignatureSpec) -> None:
    assert SignatureSpec.from_key(spec.key) == spec


@pytest.mark.parametrize("key", ["", "L+G", "L+X+F0", "F", "F-1", "G+L+F0", "L+L+F0"])
def test_spec_from_key_rejects(key: str) -> None:
    with pytest.raises(ValueError, match="not a signature spec key"):
        SignatureSpec.from_key(key)


def test_unique_entries_first_wins() -> None:
    entries = list(parse_file(SAMPLE, JiraFormat()))
    twice = [*entries, *(dataclasses.replace(e, source=SourceRef("x", 1, 0)) for e in entries)]
    assert list(unique_entries(twice)) == entries
