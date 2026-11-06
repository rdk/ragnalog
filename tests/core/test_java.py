from __future__ import annotations

import pytest

from atlas_log_analyzer.core.java import parse_exceptions, parse_frame
from atlas_log_analyzer.core.models import Frame

CHAIN = """\
java.lang.RuntimeException: Invalid property 'stackTrace': Getter threw exception
\tat com.atlassian.diagnostics.EventSystemMonitor.invokeMonitored(EventSystemMonitor.java:110)
\tat java.base/java.lang.Thread.run(Thread.java:840)
Caused by: org.springframework.beans.InvalidPropertyException: Invalid property 'stackTrace'
\tat org.springframework.beans.Accessor.getPropertyValue(Accessor.java:721)
\t... 9 more
Caused by: java.lang.reflect.InvocationTargetException
\tat org.springframework.beans.BeanWrapperImpl$Handler.getValue(BeanWrapperImpl.java:275)
\t... 20 more
Caused by: java.lang.NullPointerException: Cannot invoke "x.readLine()" because "y" is null
\tat org.codehaus.groovy.control.io.AbstractReaderSource.getLine(AbstractReaderSource.java:78)
\t... 23 more"""

GROOVY = """\
org.codehaus.groovy.control.MultipleCompilationErrorsException: startup failed:
file:/opt/scripts/Helper.groovy: 8: unable to resolve class com.example.TrustedRequest
 @ line 8, column 1.
   import com.example.TrustedRequest
   ^
file:/opt/scripts/Helper.groovy: 9: unable to resolve class com.example.Factory
 @ line 9, column 1.
   import com.example.Factory
   ^
2 errors"""


def test_caused_by_chain() -> None:
    chain = parse_exceptions(CHAIN)
    assert [e.type for e in chain] == [
        "java.lang.RuntimeException",
        "org.springframework.beans.InvalidPropertyException",
        "java.lang.reflect.InvocationTargetException",
        "java.lang.NullPointerException",
    ]
    assert chain[0].message == "Invalid property 'stackTrace': Getter threw exception"
    assert chain[2].message is None
    assert chain[3].message == 'Cannot invoke "x.readLine()" because "y" is null'
    assert [e.omitted_frames for e in chain] == [0, 9, 20, 23]
    assert [len(e.frames) for e in chain] == [2, 1, 1, 1]
    assert chain[0].frames[1] == Frame("java.lang.Thread.run", "Thread.java", 840, "java.base")
    assert chain[2].frames[0].location == (
        "org.springframework.beans.BeanWrapperImpl$Handler.getValue"
    )


def test_lines_input_equals_text_input() -> None:
    assert parse_exceptions(CHAIN.split("\n")) == parse_exceptions(CHAIN)


def test_groovy_form() -> None:
    (exc,) = parse_exceptions(GROOVY)
    assert exc.type == "org.codehaus.groovy.control.MultipleCompilationErrorsException"
    assert exc.message is not None
    assert exc.message.startswith("startup failed:\nfile:/opt/scripts/Helper.groovy: 8:")
    assert exc.message.endswith("2 errors")
    assert exc.frames == ()


def test_multiline_message_before_frames() -> None:
    text = "com.example.FooException: line one\nline two\n\tat a.B.c(B.java:1)"
    (exc,) = parse_exceptions(text)
    assert exc.message == "line one\nline two"
    assert len(exc.frames) == 1


def test_text_before_exception_and_stop_after_chain() -> None:
    text = "\n".join(
        [
            "    ",
            "some free text",
            "java.lang.IllegalStateException: boom",
            "\tat a.B.c(B.java:1)",
            "trailing text that is not part of the chain",
            "java.lang.Error: second",
        ]
    )
    (exc,) = parse_exceptions(text)
    assert exc.type == "java.lang.IllegalStateException"
    assert len(exc.frames) == 1


def test_mangled_frames_are_skipped() -> None:
    """Characters lost when a log is copied out of a terminal must not end the chain."""
    text = "\n".join(
        [
            "com.example.FooException: boom",
            "    at com.example.web.A.b(A.java:1)",
            "    ample.web.Gadget.lambda$load$4(Gadget.java:64)",  # start lost
            "    at org.glassfish.jersey.servlet.ServletContainer.doFiervletContainer.java:430)",
            "m.atlassian.plugin.servlet.filter.DelegatingPluginFilter.doFilter(Filter.java:59)",
            "    at com.example.web.A.c(A.java:2)",
            "    at java.base/java.lang.Thread.run(Native Method)",
            "Caused by: java.lang.IllegalStateException: cause",
            "    at com.example.web.A.d(A.java:3)",
            "trailing text",
            "    at com.example.web.A.e(A.java:4)",
        ]
    )
    chain = parse_exceptions(text)
    assert [e.type for e in chain] == [
        "com.example.FooException",
        "java.lang.IllegalStateException",
    ]
    locations = [f.location for f in chain[0].frames]
    assert locations == ["com.example.web.A.b", "com.example.web.A.c", "java.lang.Thread.run"]
    assert [f.location for f in chain[1].frames] == ["com.example.web.A.d"]


def test_lost_caused_by_prefix_still_ends_the_chain() -> None:
    text = "\n".join(
        [
            "com.example.FooException: boom",
            "    at com.example.web.A.b(A.java:1)",
            "com.example.BarException: another one",
            "    at com.example.web.A.c(A.java:2)",
        ]
    )
    (exc,) = parse_exceptions(text)
    assert exc.type == "com.example.FooException" and len(exc.frames) == 1


def test_suppressed_is_skipped() -> None:
    text = "\n".join(
        [
            "java.io.IOException: outer",
            "\tat a.B.c(B.java:1)",
            "\tSuppressed: java.io.IOException: close failed",
            "\t\tat a.B.close(B.java:9)",
            "\t\tCaused by: java.io.EOFException: eof",
            "\t\t\tat a.B.read(B.java:3)",
            "\tat a.B.d(B.java:2)",
            "Caused by: java.net.SocketException: reset",
            "\t... 4 more",
        ]
    )
    chain = parse_exceptions(text)
    assert [e.type for e in chain] == ["java.io.IOException", "java.net.SocketException"]
    assert [f.location for f in chain[0].frames] == ["a.B.c", "a.B.d"]
    assert chain[1].omitted_frames == 4


def test_unusual_type_name_needs_frames() -> None:
    collector = "com.atlassian.jira.cache.RequestCacheRecorderImpl$StackCollector"
    assert parse_exceptions(f"{collector}: Stack trace\n\tat a.B.c(B.java:1)")[0].type == collector
    assert parse_exceptions("com.example.Something: not an exception") == ()


@pytest.mark.parametrize(
    "text",
    [
        "",
        "\n\n",
        "    \n    ********\n    ROLLOVER",
        "file:/opt/x.groovy: 8: something",
        "at a.B.c(B.java:1)",
        "Caused by: ",
        "\x00\xff garbage �" * 50,
        "java.lang.Error: x\n\tat (broken\n\t... many more\nCaused by: \n",
    ],
)
def test_garbage_never_raises(text: str) -> None:
    result = parse_exceptions(text)
    assert isinstance(result, tuple)


@pytest.mark.parametrize(
    ("line", "frame"),
    [
        ("\tat a.B.c(B.java:12)", Frame("a.B.c", "B.java", 12, None)),
        ("\tat java.base/a.B.c(B.java:12)", Frame("a.B.c", "B.java", 12, "java.base")),
        ("\tat app//a.B.c(B.java:12)", Frame("a.B.c", "B.java", 12, "app")),
        ("\tat a.B.c(Native Method)", Frame("a.B.c", None, None, None)),
        ("\tat a.B.c(Unknown Source)", Frame("a.B.c", None, None, None)),
        ("\tat a.B$$Lambda$123/0x0000000801.run(Unknown Source)", None),
        ("    at a.B.c(B.groovy)", Frame("a.B.c", "B.groovy", None, None)),
        ("not a frame", None),
    ],
)
def test_parse_frame(line: str, frame: Frame | None) -> None:
    result = parse_frame(line)
    if line.startswith("\tat a.B$$Lambda"):
        assert result is not None
        assert result.location == "a.B$$Lambda$123/0x0000000801.run"
        assert result.module is None
        return
    assert result == frame


def test_library_packages_and_app_frames() -> None:
    from atlas_log_analyzer.core.java import LIBRARY_PACKAGES, is_app_frame

    for prefix in (
        "com.atlassian.",
        "io.atlassian.",
        "org.ofbiz.",
        "webwork.",
        "net.java.ao.",
        "org.hibernate.",
        "com.google.",
        "org.osgi.",
        "org.springframework.",
        "org.apache.",
        "org.eclipse.",
        "io.netty.",
        "com.zaxxer.",
        "org.postgresql.",
        "oracle.",
        "com.mysql.",
        "com.microsoft.sqlserver.",
        "java.",
        "javax.",
        "jdk.",
        "sun.",
        "groovy.",
        "org.codehaus.groovy.",
    ):
        assert prefix in LIBRARY_PACKAGES
    assert not is_app_frame("com.atlassian.jira.issue.IssueManager.getIssue")
    assert not is_app_frame("java.lang.Thread.run")
    assert is_app_frame("com.qoti.rf.stats.type.StatTypeHelper.resolve")
    assert is_app_frame("Script42.run")  # Groovy script classes have no package
    assert is_app_frame("com.atlassian.jira.Foo.bar", ["com.atlassian.jira."])
    assert not is_app_frame("com.qoti.rf.Foo.bar", ["org.acme."])
    assert not is_app_frame("com.qoti.rf.Foo.bar", ("org.acme.", "com.google."))


def test_suppressed_exceptions_cause_stays_out_of_the_chain() -> None:
    text = (
        "java.lang.IllegalStateException: top\n"
        "\tat a.B.c(B.java:1)\n"
        "\tSuppressed: java.io.IOException: close\n"
        "\t\tat a.C.d(C.java:2)\n"
        "\tCaused by: java.net.SocketException: reset\n"
        "\t\tat a.D.e(D.java:3)\n"
        "\t\t... 3 more\n"
        "Caused by: java.sql.SQLException: db\n"
        "\tat a.E.f(E.java:4)\n"
    )
    chain = parse_exceptions(text)
    assert [e.type for e in chain] == ["java.lang.IllegalStateException", "java.sql.SQLException"]


def test_line_numbers_past_int32_are_dropped() -> None:
    frame = parse_frame("\tat a.B.c(B.java:99999999999)")
    assert frame == Frame("a.B.c", "B.java", None, None)
    assert parse_frame("\tat a.B.c(B.java:²)") == Frame("a.B.c", "B.java:²", None, None)
