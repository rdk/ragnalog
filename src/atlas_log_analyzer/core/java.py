r"""Java stack-trace parsing shared by log4j-style formats.

Recognised layout::

    fully.qualified.Type: message
    message continuation (until the first frame / marker line)
    \tat [module/]pkg.Class.method(File.java:123)
    \tat pkg.Class.method(Native Method)
    \t... 9 more
    Caused by: other.Type: message
    \t...
    \tSuppressed: some.Type: message      (ignored, see below)

Groovy compile failures (``MultipleCompilationErrorsException: startup failed:``)
have no frames; their message spans the remaining lines.

``Suppressed:`` blocks are skipped together with everything indented deeper than
the ``Suppressed:`` line itself: they describe secondary failures (typically from
``close()``) and would otherwise make the chain of the primary failure unstable.
Only the first chain in a text is parsed; text after it is ignored.

Among the frames, a mangled frame line (characters lost when a log was copied out
of a terminal or chat window: an indented ``at …`` that does not parse, or a line
ending like a frame, ``…bar(Foo.java:64)``) is skipped instead of ending the
chain. A ``Caused by:`` line that lost its prefix cannot be told from unrelated
text, so the chain still ends there.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from atlas_log_analyzer.core.models import ExceptionInfo, Frame

__all__ = [
    "LIBRARY_PACKAGES",
    "is_app_frame",
    "parse_exceptions",
    "parse_frame",
]

LIBRARY_PACKAGES: tuple[str, ...] = (
    # JDK and JVM languages
    "java.",
    "javax.",
    "jakarta.",
    "jdk.",
    "sun.",
    "com.sun.",
    "kotlin.",
    "scala.",
    "groovy.",
    "org.codehaus.groovy.",
    # Jira platform and the libraries it ships
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
    "org.glassfish.",
    "io.netty.",
    "com.zaxxer.",
    # JDBC drivers
    "org.postgresql.",
    "oracle.",
    "com.mysql.",
    "com.microsoft.sqlserver.",
)
"""Package prefixes of library/platform code; frames elsewhere are application frames."""


def is_app_frame(location: str, app_packages: Sequence[str] | None = None) -> bool:
    """Tell whether a frame location (``pkg.Class.method``) is application code.

    Core reads no environment: the prefixes come from the caller's settings
    (``ATLAS_LOG_APP_PACKAGES``, :class:`atlas_log_analyzer.config.Settings`).

    Args:
        location: ``Frame.location``, e.g. ``com.example.Foo.bar``.
        app_packages: When given (non-empty), a frame is application code iff it starts
            with one of these prefixes; otherwise iff it is outside :data:`LIBRARY_PACKAGES`.

    """
    if app_packages:
        return location.startswith(tuple(app_packages))
    return not location.startswith(LIBRARY_PACKAGES)


_TYPE = r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+"
_HEADER_RE = re.compile(rf"(?P<type>{_TYPE})(?::(?: (?P<msg>.*)|$)|$)")
_CAUSED_RE = re.compile(rf"\s*Caused by: (?P<type>{_TYPE})(?::(?: ?(?P<msg>.*))?)?$")
_FRAME_RE = re.compile(
    r"\s+at (?:(?P<module>[\w.@-]+)/(?:[\w.@-]*/)?)?(?P<loc>[^\s(]+)\((?P<src>[^)]*)\)"
)
_MORE_RE = re.compile(r"\s+\.\.\. (?P<n>\d+) (?:more|common frames omitted)")
_DAMAGED_FRAME_RE = re.compile(
    r"\s+at \S"
    r"|.*(?:\.(?:java|kt|kts|groovy|scala)(?::\d+)?|Native Method|Unknown Source)\)\s*$"
)
"""A mangled frame line: an indented ``at …`` that does not parse, or a line ending
like a frame (``…bar(Foo.java:64)``). Copies of logs out of terminals and chat
windows drop characters at wrap points (``    ai.rf.web.Foo.bar(Foo.java:64)``,
``    at a.B.doFiervletContainer.java:430)``)."""
_EXC_SUFFIXES = ("Exception", "Error", "Throwable", "Failure", "Fault")
_MAX_SCAN_LINES = 2000
_INT32 = 2**31 - 1
"""Largest line number or omitted-frame count stored (the columns are int32)."""
_WS = (" ", "\t")


def parse_frame(line: str) -> Frame | None:
    """Parse one ``at …`` line; ``None`` when ``line`` is not a frame."""
    m = _FRAME_RE.match(line)
    if m is None:
        return None
    src = m.group("src")
    file: str | None = None
    lineno: int | None = None
    if src not in ("Native Method", "Unknown Source", ""):
        name, sep, num = src.rpartition(":")
        if sep and num.isascii() and num.isdigit():
            # a line number past int32 is garbage: keep the file, drop the number
            file, lineno = name, (int(num) if int(num) <= _INT32 else None)
        else:
            file = src
    return Frame(m.group("loc"), file, lineno, m.group("module"))


def _is_marker(line: str) -> bool:
    """Tell whether ``line`` ends a multi-line exception message."""
    if line[:1] in _WS:
        return line.lstrip().startswith(("at ", "... ", "Suppressed: ", "Caused by: "))
    return line.startswith("Caused by: ")


def _find_start(lines: Sequence[str]) -> tuple[int, re.Match[str]] | None:
    """Index and match of the first line that starts an exception chain."""
    n = min(len(lines), _MAX_SCAN_LINES)
    nxt: list[int] | None = None  # index of the first marker at or after each line, on demand
    for i in range(n):
        line = lines[i]
        if not line or line[0] in " \t":
            continue
        m = _HEADER_RE.match(line)
        if m is None:
            continue
        simple = m.group("type").rsplit(".", 1)[-1]
        if simple.endswith(_EXC_SUFFIXES):
            return i, m
        # Accept unusual type names only when frames follow the message: the first marker
        # after it is a frame. One pass from the end finds every line's next marker (a body
        # of many header-like lines and no frames was quadratic).
        if nxt is None:
            nxt = [n] * (n + 1)
            for k in range(n - 1, -1, -1):
                nxt[k] = k if _is_marker(lines[k]) else nxt[k + 1]
        j = nxt[i + 1]
        if j < n and lines[j].lstrip().startswith("at "):
            return i, m
    return None


def parse_exceptions(text: str | Sequence[str]) -> tuple[ExceptionInfo, ...]:
    """Parse the first Java exception chain found in ``text``.

    Args:
        text: Continuation text of a log entry, either as one string or as lines.

    Returns:
        The chain, outermost exception first; ``()`` when no exception is found.
        Never raises on malformed input.

    """
    lines = text.split("\n") if isinstance(text, str) else text
    if not lines:
        return ()
    try:
        return _parse(lines)
    except Exception:  # defensive: garbage must never abort parsing
        return ()


def _parse(lines: Sequence[str]) -> tuple[ExceptionInfo, ...]:
    found = _find_start(lines)
    if found is None:
        return ()
    start, m = found
    chain: list[ExceptionInfo] = []
    exc_type = m.group("type")
    first_msg = m.group("msg")
    msg_lines: list[str] = [] if first_msg is None else [first_msg]
    has_msg = first_msg is not None
    frames: list[Frame] = []
    omitted = 0
    in_frames = False
    skip_indent = -1

    for i in range(start + 1, len(lines)):
        line = lines[i]
        c = line[:1]
        if skip_indent >= 0:
            indent = len(line) - len(line.lstrip()) if c in _WS else 0
            if indent > skip_indent:
                continue
            # a suppressed exception's own cause is printed at the indent of "Suppressed:"
            if indent == skip_indent and line.lstrip().startswith("Caused by: "):
                continue
            skip_indent = -1
        if c in _WS:
            stripped = line.lstrip()
            if stripped.startswith("at "):
                frame = parse_frame(line)
                if frame is not None:
                    frames.append(frame)
                    in_frames = True
                    continue
            elif stripped.startswith("... "):
                more = _MORE_RE.match(line)
                if more is not None:
                    n = more.group("n")
                    omitted = min(int(n), _INT32) if n.isascii() else 0
                    in_frames = True
                    continue
            elif stripped.startswith("Suppressed: "):
                skip_indent = len(line) - len(stripped)
                in_frames = True
                continue
            elif stripped.startswith("Caused by: "):
                c = "C"
                line = stripped
        if c == "C" and line.startswith("Caused by: "):
            cm = _CAUSED_RE.match(line)
            if cm is not None:
                chain.append(_exc(exc_type, has_msg, msg_lines, frames, omitted))
                exc_type = cm.group("type")
                msg = cm.group("msg")
                has_msg = msg is not None
                msg_lines = [] if msg is None else [msg]
                frames = []
                omitted = 0
                in_frames = False
                continue
        if in_frames:
            if _DAMAGED_FRAME_RE.match(line):
                continue  # a mangled frame: skip it, the frames go on
            break  # end of the chain
        msg_lines.append(line)
        has_msg = True
    chain.append(_exc(exc_type, has_msg, msg_lines, frames, omitted))
    return tuple(chain)


def _exc(
    exc_type: str, has_msg: bool, msg_lines: list[str], frames: list[Frame], omitted: int
) -> ExceptionInfo:
    message = "\n".join(msg_lines) if has_msg else None
    return ExceptionInfo(exc_type, message, tuple(frames), omitted)
