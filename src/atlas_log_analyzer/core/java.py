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
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from atlas_log_analyzer.core.models import ExceptionInfo, Frame

__all__ = ["parse_exceptions", "parse_frame"]

_TYPE = r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+"
_HEADER_RE = re.compile(rf"(?P<type>{_TYPE})(?::(?: (?P<msg>.*)|$)|$)")
_CAUSED_RE = re.compile(rf"\s*Caused by: (?P<type>{_TYPE})(?::(?: ?(?P<msg>.*))?)?$")
_FRAME_RE = re.compile(
    r"\s+at (?:(?P<module>[\w.@-]+)/(?:[\w.@-]*/)?)?(?P<loc>[^\s(]+)\((?P<src>[^)]*)\)"
)
_MORE_RE = re.compile(r"\s+\.\.\. (?P<n>\d+) (?:more|common frames omitted)")
_EXC_SUFFIXES = ("Exception", "Error", "Throwable", "Failure", "Fault")
_MAX_SCAN_LINES = 2000
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
        if sep and num.isdigit():
            file, lineno = name, int(num)
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
        # Accept unusual type names only when frames follow the message.
        for j in range(i + 1, n):
            if _is_marker(lines[j]):
                if lines[j].lstrip().startswith("at "):
                    return i, m
                break
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
            if c in _WS and len(line) - len(line.lstrip()) > skip_indent:
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
                    omitted = int(more.group("n"))
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
