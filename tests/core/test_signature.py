from __future__ import annotations

from atlas_log_analyzer.core.formats.jira import JiraFormat
from atlas_log_analyzer.core.masking import Masker
from atlas_log_analyzer.core.models import LogEntry, SourceRef
from atlas_log_analyzer.core.signature import (
    COARSE_SPEC,
    DEFAULT_SPEC,
    SignatureSpec,
    Signer,
    exception_message_key,
)

DISPATCH = (
    "{ts} JIRA-EventThread-{n} ERROR {user}     "
    "[c.a.event.internal.AsynchronousAbleEventDispatcher] "
    "There was an exception thrown trying to dispatch event [com.X@{obj}] for {issue}"
)


def _entry(jira: JiraFormat, header: str, *body: str) -> LogEntry:
    return jira.build(jira.parse_header(header), header, list(body), SourceRef("x.log", 1, 0))


def _dispatch(
    jira: JiraFormat, *, n: int, user: str, issue: str, obj: str, root: str, ts: str
) -> LogEntry:
    header = DISPATCH.format(ts=ts, n=n, user=user, issue=issue, obj=obj)
    return _entry(
        jira,
        header,
        "java.lang.RuntimeException: Listener failed for " + issue,
        "\tat com.a.B.c(B.java:1)",
        f"Caused by: {root}: value {n} is null",
        f"\tat com.a.D.e(D.java:{n})",
        f"\t... {n} more",
    )


def test_spec_key() -> None:
    assert DEFAULT_SPEC.key == "L+G+E+F0"
    assert COARSE_SPEC.key == "L+G+F0"
    assert SignatureSpec(include_level=False, frames=3).key == "G+E+F3"


def test_stable_across_variables(jira: JiraFormat, masker: Masker) -> None:
    signer = Signer(masker)
    a = _dispatch(
        jira,
        n=607,
        user="anonymous",
        issue="ABC-1",
        obj="36f1da1b",
        root="java.lang.NullPointerException",
        ts="2026-10-29 03:05:57,395+0100",
    )
    b = _dispatch(
        jira,
        n=12,
        user="jdoe",
        issue="XYZ-99",
        obj="0a0b0c0d",
        root="java.lang.NullPointerException",
        ts="2026-10-28 11:00:00,000+0100",
    )
    sa, sb = signer.sign(a), signer.sign(b)
    assert a.id != b.id
    assert sa.hash == sb.hash
    assert len(sa.hash) == 16
    assert sa.template == (
        "There was an exception thrown trying to dispatch event [com.X@<OBJ>] for <ISSUE>"
    )
    assert sa.variables == ("36f1da1b", "ABC-1")
    assert sa.parts == (
        "jira",
        "ERROR",
        "c.a.event.internal.AsynchronousAbleEventDispatcher",
        sa.template,
        "java.lang.RuntimeException",
        "Listener failed for <ISSUE>",
        "java.lang.NullPointerException",
        "value <NUM> is null",
    )


def test_different_root_cause(jira: JiraFormat, masker: Masker) -> None:
    signer = Signer(masker)
    common = {"n": 1, "user": "anonymous", "issue": "A-1", "obj": "abcdef12"}
    ts = "2026-10-29 03:05:57,395+0100"
    a = _dispatch(jira, root="java.lang.NullPointerException", ts=ts, **common)  # type: ignore[arg-type]
    b = _dispatch(jira, root="java.lang.IllegalStateException", ts=ts, **common)  # type: ignore[arg-type]
    full_a, coarse_a = signer.sign_both(a)
    full_b, coarse_b = signer.sign_both(b)
    assert full_a.hash != full_b.hash
    assert coarse_a.hash == coarse_b.hash
    assert full_a == signer.sign(a)
    assert coarse_a == Signer(masker, COARSE_SPEC).sign(a)


def test_frames_spec(jira: JiraFormat, masker: Masker) -> None:
    header = "2026-10-29 03:05:57,395+0100 t ERROR      [l] failed"
    a = _entry(jira, header, "java.lang.Error: x", "\tat a.B.c(B.java:1)", "\tat a.B.d(B.java:2)")
    b = _entry(jira, header, "java.lang.Error: x", "\tat a.B.c(B.java:7)", "\tat a.B.z(B.java:2)")
    assert Signer(masker).sign(a).hash == Signer(masker).sign(b).hash
    one = Signer(masker, SignatureSpec(frames=1))
    two = Signer(masker, SignatureSpec(frames=2))
    assert one.sign(a).hash == one.sign(b).hash
    assert two.sign(a).hash != two.sign(b).hash
    assert two.sign(a).parts[-2:] == ("a.B.c", "a.B.d")


def test_template_falls_back_to_exception(jira: JiraFormat, masker: Masker) -> None:
    header = "2026-10-29 03:05:57,395+0100 t ERROR      [l] "
    entry = _entry(jira, header, "java.lang.IllegalStateException: bad id 42")
    sig = Signer(masker).sign(entry)
    assert sig.template == "java.lang.IllegalStateException: bad id <NUM>"
    no_exc = Signer(masker).sign(_entry(jira, header))
    assert no_exc.template == ""


def test_groovy_message_key(jira: JiraFormat, masker: Masker) -> None:
    header = "2026-10-29 03:05:57,395+0100 t ERROR      [l] Script field failed on issue: A-1"

    def groovy(script: str, line: int) -> LogEntry:
        return _entry(
            jira,
            header,
            "org.codehaus.groovy.control.MultipleCompilationErrorsException: startup failed:",
            f"file:/opt/{script}.groovy: {line}: unable to resolve class X",
            " @ line 8, column 1.",
            "1 error",
        )

    signer = Signer(masker)
    assert signer.sign(groovy("A", 8)).hash == signer.sign(groovy("A", 9)).hash
    assert signer.sign(groovy("A", 8)).hash != signer.sign(groovy("B", 8)).hash
    exc = groovy("A", 8).exceptions[0]
    assert exception_message_key(exc) == (
        "startup failed: file:/opt/A.groovy: 8: unable to resolve class X"
    )
