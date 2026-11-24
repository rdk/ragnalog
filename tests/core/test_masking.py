from __future__ import annotations

import re

import pytest

from ragnalog.core.masking import GENERIC_RULES, MASK_RULES_VERSION, Masker, MaskRule


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
        (
            "add 1 ldap userName = sAMAccountName: E_MPEKA2",
            "add <NUM> ldap userName = sAMAccountName: <USER>",
            ("1", "E_MPEKA2"),
        ),
        (
            "ERROR WITH USER LDAP UID: AP_DMT_SQ : NO LDAP ELEMENTS",
            "ERROR WITH USER LDAP UID: <USER> : NO LDAP ELEMENTS",
            ("AP_DMT_SQ",),
        ),
        (
            "login failed for user=jdoe (user: jdoe)",
            "login failed for user=<USER> (user: <USER>)",
            ("jdoe", "jdoe"),
        ),
        ("user: 'quoted' stays", "user: 'quoted' stays", ()),
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
    assert MASK_RULES_VERSION == 4


@pytest.mark.parametrize(
    ("text", "variable"),
    [
        ("Tue Nov 24 00:00:00 CET 2026", "Tue Nov 24 00:00:00 CET 2026"),
        ("Wed Jul 01 09:30:00 CEST 2026", "Wed Jul 01 09:30:00 CEST 2026"),
        ("Thu Jan 01 00:00:00 UTC 1970", "Thu Jan 01 00:00:00 UTC 1970"),
        ("Fri Dec 04 23:59:59 GMT+01:00 2026", "Fri Dec 04 23:59:59 GMT+01:00 2026"),
        ("Sat Mar 07 08:00:00 ChST 2026", "Sat Mar 07 08:00:00 ChST 2026"),
        ("Sun Feb  1 10:20:30 2026", "Sun Feb  1 10:20:30 2026"),  # C's ctime: no zone
    ],
)
def test_java_date_to_string_is_one_timestamp(masker: Masker, text: str, variable: str) -> None:
    assert masker.apply(f"args=[jdoe, {text}])] done.") == (
        "args=[jdoe, <TS>])] done.",
        (variable,),
    )
    assert masker.apply(f"expires {text}.") == ("expires <TS>.", (variable,))


def test_java_dates_of_other_days_share_a_template(masker: Masker) -> None:
    days = ("Sat Nov 21 13:05:09", "Sun Nov 22 00:00:00", "Mon Nov 23 07:45:12")
    templates = {masker.template(f"Duplicate entities [X] found args=[{d} CET 2026]") for d in days}
    assert templates == {"Duplicate entities [X] found args=[<TS>]"}


@pytest.mark.parametrize(
    ("text", "template"),
    [
        ("on Tue Nov 24 only", "on Tue Nov <NUM> only"),
        ("Mon Nov 23 12:00 CET 2026", "Mon Nov <NUM> <NUM>:<NUM> CET <NUM>"),
        ("Tue Nov 24 00:00:00 CET 20261", "Tue Nov <NUM> <NUM>:<NUM>:<NUM> CET <NUM>"),
    ],
)
def test_partial_java_dates_keep_their_words(masker: Masker, text: str, template: str) -> None:
    assert masker.template(text) == template


@pytest.mark.parametrize(
    ("text", "template"),
    [
        ("connected from 10.0.0.1.", "connected from <IP>."),
        ("proxy 10.0.0.1:8080. Retrying", "proxy <IP>. Retrying"),
        ("hosts 10.0.0.1, 10.0.0.2", "hosts <IP>, <IP>"),
    ],
)
def test_ip_before_punctuation_is_masked_whole(text: str, template: str) -> None:
    assert Masker(GENERIC_RULES).template(text) == template


def test_dotted_runs_longer_than_an_ip_are_no_ip() -> None:
    assert "<IP>" not in Masker(GENERIC_RULES).template("version 10.0.0.1.4 and 10.0.0.1.x")


def test_a_long_run_of_digits_is_masked_in_linear_time(masker: Masker) -> None:
    import time

    run = "1" * 200_000
    start = time.perf_counter()
    template, _ = masker.apply(f"Invalid JQL: key in (v.{run}x)")
    assert time.perf_counter() - start < 2.0  # was quadratic: hours at the 1 MiB line cap
    assert template == f"Invalid JQL: key in (v.{run}x)"  # glued to a letter: no number
