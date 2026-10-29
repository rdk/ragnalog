from __future__ import annotations

import re

import pytest

from atlas_log_analyzer.core.masking import GENERIC_RULES, MASK_RULES_VERSION, Masker, MaskRule


def test_docstring_example(masker: Masker) -> None:
    assert masker.apply("Script field failed on issue: ABC-69517, field: Finished On") == (
        "Script field failed on issue: <ISSUE>, field: Finished On",
        ("ABC-69517",),
    )


@pytest.mark.parametrize(
    ("text", "template", "variables"),
    [
        ("at 2026-10-29 02:19:41,415+0100 ok", "at <TS> ok", ("2026-10-29 02:19:41,415+0100",)),
        ("at 2026-10-29T02:19:41.415Z ok", "at <TS> ok", ("2026-10-29T02:19:41.415Z",)),
        (
            "id 550e8400-e29b-41d4-a716-446655440000",
            "id <UUID>",
            ("550e8400-e29b-41d4-a716-446655440000",),
        ),
        (
            "from 10.0.0.24:8080 and 10.0.0.1",
            "from <IP> and <IP>",
            ("10.0.0.24:8080", "10.0.0.1"),
        ),
        (
            "page <https://jira.example.com/jolokia/read?x=1> redirect",
            "page <<URL>> redirect",
            ("https://jira.example.com/jolokia/read?x=1",),
        ),
        ("see https://a.example.com/x.", "see <URL>.", ("https://a.example.com/x",)),
        ("mail a.b@example.com now", "mail <EMAIL> now", ("a.b@example.com",)),
        ("obj 0xdeadbeef cafebabe12", "obj <HEX> <HEX>", ("0xdeadbeef", "cafebabe12")),
        ("event X@36f1da1b fired", "event X@<OBJ> fired", ("36f1da1b",)),
        ("Dropped: [customfield_10001]", "Dropped: [<CUSTOMFIELD>]", ("customfield_10001",)),
        ("req 207x2193299x2 done", "req <REQID> done", ("207x2193299x2",)),
        ("finished after PT5.008157164S.", "finished after <DURATION>.", ("PT5.008157164S",)),
        (
            "thread/stack: RMI-d6tcld. Incorrect",
            "thread/stack: <THREAD>. Incorrect",
            ("RMI-d6tcld",),
        ),
        (
            "(repoId=83) took 923ms, 1/10",
            "(repoId=<NUM>) took <NUM>ms, <NUM>/<NUM>",
            ("83", "923", "1", "10"),
        ),
        ("Lucene3x log4j thread-8", "Lucene3x log4j thread-<NUM>", ("8",)),
        (
            "key KB001_0000687992_5_1. owner JIRAUSER12345",
            "key <ID>. owner <ID>",
            ("KB001_0000687992_5_1", "JIRAUSER12345"),
        ),
        (
            "file:/opt/x/Helper.groovy: 8: unable to resolve",
            "file:/opt/x/Helper.groovy: <NUM>: unable to resolve",
            ("8",),
        ),
        ("UTF-8 is not an issue key", "UTF-<NUM> is not an issue key", ("8",)),
        ("no variables here", "no variables here", ()),
        ("", "", ()),
    ],
)
def test_rules(masker: Masker, text: str, template: str, variables: tuple[str, ...]) -> None:
    assert masker.apply(text) == (template, variables)
    assert masker.template(text) == template


def test_variables_in_order_of_appearance(masker: Masker) -> None:
    _, variables = masker.apply("10.0.0.1 ABC-1 42 ABC-2")
    assert variables == ("10.0.0.1", "ABC-1", "42", "ABC-2")


def test_rule_order_wins() -> None:
    first = MaskRule.make("word", r"[a-z]+\d+")
    masker = Masker([first, *GENERIC_RULES])
    assert masker.apply("abc123 7") == ("<WORD> <NUM>", ("abc123", "7"))


def test_rule_flags_and_inner_groups() -> None:
    rule = MaskRule("key", re.compile(r"(?P<k>key)=(\w+)", re.IGNORECASE), "<KV>")
    masker = Masker([rule])
    assert masker.apply("KEY=a key=b") == ("<KV> <KV>", ("KEY=a", "key=b"))


def test_empty_rules_and_cache() -> None:
    masker = Masker([])
    assert masker.apply("x 1") == ("x 1", ())
    assert masker.template("x 1") == "x 1"
    small = Masker(GENERIC_RULES, cache_size=2)
    for i in range(10):
        assert small.apply(f"n {i}") == ("n <NUM>", (str(i),))
    assert len(small._cache) <= 2


def test_version() -> None:
    assert MASK_RULES_VERSION == 1
