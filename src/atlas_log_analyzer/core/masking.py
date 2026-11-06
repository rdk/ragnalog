"""Masking of variable tokens: turns a message into a template plus variables.

Example::

    masker = Masker(JIRA_RULES + GENERIC_RULES)
    template, variables = masker.apply("Script field failed on issue: ABC-69517, field: X")
    # ("Script field failed on issue: <ISSUE>, field: X", ("ABC-69517",))

All rules are compiled into one alternation and applied in a single left-to-right
pass. At a given position the earlier rule wins; a match consumes its text, so
later rules never see it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

__all__ = ["GENERIC_RULES", "MASK_RULES_VERSION", "MaskRule", "Masker"]

MASK_RULES_VERSION = 1
_CACHE_TEXT_MAX = 4096
"""Longest text the masker caches (65,536 texts of this size stay under 300 MB)."""
"""Bump whenever a change to built-in rules alters templates; recorded in manifests."""


@dataclass(frozen=True, slots=True)
class MaskRule:
    """Replace every match of ``pattern`` with ``placeholder`` (``<NAME>`` convention)."""

    name: str
    pattern: re.Pattern[str]
    placeholder: str

    @classmethod
    def make(cls, name: str, pattern: str, placeholder: str | None = None) -> MaskRule:
        """Compile ``pattern``; the placeholder defaults to ``<NAME>``."""
        return cls(name, re.compile(pattern), placeholder or f"<{name.upper()}>")


GENERIC_RULES: Sequence[MaskRule] = (
    MaskRule.make(
        "ts",
        r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?(?!\d)",
    ),
    MaskRule.make(
        "uuid",
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
    ),
    MaskRule.make("url", r"(?<![\w.+-])[a-zA-Z][a-zA-Z0-9+.-]*://[^\s'\"<>]*[^\s'\"<>.,;:)\]]"),
    MaskRule.make("email", r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-zA-Z]{2,}\b"),
    MaskRule.make("ip", r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?(?![\w.])"),
    MaskRule.make("hex", r"\b(?:0x[0-9a-fA-F]+|(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{8,})\b"),
    MaskRule.make(
        "user",
        r"(?i:(?<=\buid: )|(?<=\buid=)|(?<=samaccountname: )|(?<=samaccountname=)"
        r"|(?<=\busername: )|(?<=\busername=)|(?<=\buser name: )|(?<=\buser: )|(?<=\buser=)"
        r"|(?<=\blogin: )|(?<=\blogin=))"
        r"[A-Za-z][\w.@-]*",
    ),
    MaskRule.make("id", r"\b(?=[A-Za-z_])(?=\w*\d{3})\w+\b"),
    MaskRule.make("num", r"(?<![\w.$-])-?\d+(?:[.,]\d+)?|(?<=[\w.$-])\d+(?:[.,]\d+)?(?![\w.])"),
)
"""Built-in, format-independent rules (timestamps, UUIDs, IPs, hex ids, numbers …), in order.

``user`` masks the identifier that follows a user-naming key (``uid:``, ``user=``,
``userName:``, ``sAMAccountName:``, ``login:``; case-insensitive), since account names
otherwise look like ordinary words (``LDAP UID: JDOEXAMP``).
``id`` masks identifiers that start with a letter or ``_`` and contain a run of at least
three digits (``KB001_0000687992_5_1``, ``JIRAUSER12345``). ``num`` masks standalone
numbers (``42``, ``-1``, ``3.5``) and numbers that end a token (``thread-8``, ``id=42``)
but keeps digits inside identifiers (``Lucene3x``, ``log4j``).
"""

_INLINE_FLAGS = ((re.IGNORECASE, "i"), (re.MULTILINE, "m"), (re.DOTALL, "s"), (re.VERBOSE, "x"))


def _combine(rules: Sequence[MaskRule]) -> re.Pattern[str] | None:
    if not rules:
        return None
    alternatives = []
    for i, rule in enumerate(rules):
        flags = "".join(ch for flag, ch in _INLINE_FLAGS if rule.pattern.flags & flag)
        body = f"(?{flags}:{rule.pattern.pattern})" if flags else rule.pattern.pattern
        alternatives.append(f"(?P<_r{i}>{body})")
    return re.compile("|".join(alternatives))


class Masker:
    """Applies an ordered sequence of :class:`MaskRule` to text."""

    def __init__(self, rules: Iterable[MaskRule], *, cache_size: int = 65536) -> None:
        self.rules: tuple[MaskRule, ...] = tuple(rules)
        self._regex = _combine(self.rules)
        self._placeholders = {f"_r{i}": rule.placeholder for i, rule in enumerate(self.rules)}
        self._cache: dict[str, tuple[str, tuple[str, ...]]] = {}
        self._template_cache: dict[str, str] = {}
        self._cache_size = cache_size

    def apply(self, text: str) -> tuple[str, tuple[str, ...]]:
        """Return ``(template, variables)``; variables in order of appearance."""
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        regex = self._regex
        if regex is None:
            result: tuple[str, tuple[str, ...]] = (text, ())
        else:
            placeholders = self._placeholders
            variables: list[str] = []
            out: list[str] = []
            pos = 0
            for m in regex.finditer(text):
                start, end = m.span()
                if start == end:
                    continue
                out.append(text[pos:start])
                out.append(placeholders[m.lastgroup or ""])
                variables.append(m.group())
                pos = end
            if not variables:
                result = (text, ())
            else:
                out.append(text[pos:])
                result = ("".join(out), tuple(variables))
        if len(text) <= _CACHE_TEXT_MAX:  # long payloads repeat rarely and would pin memory
            if len(self._cache) >= self._cache_size:
                self._cache.clear()
            self._cache[text] = result
        return result

    def template(self, text: str) -> str:
        """Return only the template (faster when variables are not needed)."""
        cached = self._cache.get(text)
        if cached is not None:
            return cached[0]
        regex = self._regex
        if regex is None:
            return text
        result = self._template_cache.get(text)
        if result is None:
            result = regex.sub(self._sub, text)
            if len(text) <= _CACHE_TEXT_MAX:
                if len(self._template_cache) >= self._cache_size:
                    self._template_cache.clear()
                self._template_cache[text] = result
        return result

    def _sub(self, m: re.Match[str]) -> str:
        return self._placeholders[m.lastgroup or ""] if m.end() > m.start() else ""
