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

MASK_RULES_VERSION = 5
"""Bump whenever a change to built-in rules alters templates; recorded in manifests.

2: an IP address that ends a sentence (``from 10.0.0.1.``) is masked whole.
3: IPv6 addresses (``fe80::1``, eight groups) are ``<IP>``; an IPv4 address needs numbers
up to 255; JSON Web Tokens (``eyJ…``) are ``<TOKEN>``; an email's local part may hold
``'`` (``o'neil@…``) and ``user@localhost`` is an email; the user name in a home path
(``C:\\Users\\NAME``, ``/home/NAME``, ``/Users/NAME``) is ``<USER>``.
4: a date written by Java's ``Date.toString()`` (``Tue Nov 24 00:00:00 CET 2026``) is one
``<TS>``; its weekday and month names stayed in the template, so a message holding one
got a new signature every day.
5: an RFC 1123 / RFC 2822 date (``Tue, 24 Nov 2026 10:00:00 GMT``, HTTP and mail headers)
is one ``<TS>``, for the same reason.
"""
_CACHE_TEXT_MAX = 4096
"""Longest text the masker caches (65,536 texts of this size stay under 300 MB)."""


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
    MaskRule.make(  # EEE MMM dd HH:mm:ss zzz yyyy; zzz: CET, GMT+01:00, ChST or none
        "javadate",
        r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r" [ \d]?\d \d{2}:\d{2}:\d{2}(?: [A-Z][A-Za-z]{0,5}(?:[+-]\d{1,2}(?::?\d{2})?)?)?"
        r" \d{4}(?!\d)",
        "<TS>",
    ),
    MaskRule.make(  # EEE, dd MMM yyyy HH:mm[:ss] [zone]: RFC 1123 (HTTP), RFC 2822 (mail)
        "httpdate",
        r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), [ \d]?\d"
        r" (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4} \d{2}:\d{2}(?::\d{2})?"
        r"(?: (?:[A-Z]{1,5}|[+-]\d{4}))?\b",
        "<TS>",
    ),
    MaskRule.make(
        "uuid",
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
    ),
    MaskRule.make("url", r"(?<![\w.+-])[a-zA-Z][a-zA-Z0-9+.-]*://[^\s'\"<>]*[^\s'\"<>.,;:)\]]"),
    MaskRule.make("token", r"(?<![\w-])eyJ[\w-]{10,}(?:\.[\w-]+){0,2}"),
    MaskRule.make(
        "email",
        r"(?<![\w.+-])(?<!\w')\w[\w.+'-]*@(?:[\w-]+(?:\.[\w-]+)*\.[a-zA-Z]{2,}|localhost)\b",
    ),
    MaskRule.make(
        "ip6",
        r"(?<![\w:.])(?=[0-9A-Fa-f:]*\d)"  # a digit: "Dead::beef" is no address
        r"(?:(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}"
        r"|(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4})*)?::(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4})*)?)"
        r"(?:%\w+)?(?![\w:])",
        "<IP>",
    ),
    MaskRule.make(
        "ip",
        r"(?<![\w.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)"
        r"(?::\d{1,5})?(?!\w|\.\w)",
    ),
    MaskRule.make(
        "home",
        r"(?i:(?<=\\users\\)|(?<=\\\\users\\\\)|(?<=/users/)|(?<=/home/))[^\\/\s'\"<>:|]+",
        "<USER>",
    ),
    MaskRule.make("hex", r"\b(?:0x[0-9a-fA-F]+|(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{8,})\b"),
    MaskRule.make(
        "user",
        r"(?i:(?<=\buid: )|(?<=\buid=)|(?<=samaccountname: )|(?<=samaccountname=)"
        r"|(?<=\busername: )|(?<=\busername=)|(?<=\buser name: )|(?<=\buser: )|(?<=\buser=)"
        r"|(?<=\blogin: )|(?<=\blogin=))"
        r"[A-Za-z][\w.@-]*",
    ),
    MaskRule.make("id", r"\b(?=[A-Za-z_])(?=\w*\d{3})\w+\b"),
    # the second branch starts only where a run of digits starts and never gives digits back:
    # trying every start inside a long run (v.1111…x in a JQL) took time quadratic in its length
    MaskRule.make(
        "num",
        r"(?<![\w.$-])-?\d+(?:[.,]\d+)?|(?<=[\w.$-])(?<!\d)\d++(?:[.,]\d++)?(?![\w.])",
    ),
)
"""Built-in, format-independent rules (timestamps, UUIDs, IPs, hex ids, numbers …), in order.

``ts`` masks ISO timestamps (``2026-11-24 00:00:00,000+0100``); ``javadate`` masks the form
Java's ``Date.toString()`` writes (``Tue Nov 24 00:00:00 CET 2026``, also without the zone,
as C's ``ctime`` writes it) as ``<TS>`` too, since the weekday and month names would
otherwise give a message a new template every day; ``httpdate`` does the same for RFC 1123
and RFC 2822 dates (``Tue, 24 Nov 2026 10:00:00 GMT``, ``Mon, 2 Nov 2026 09:05 +0100``).

``user`` masks the identifier that follows a user-naming key (``uid:``, ``user=``,
``userName:``, ``sAMAccountName:``, ``login:``; case-insensitive), since account names
otherwise look like ordinary words (``LDAP UID: JDOEXAMP``). ``home`` masks the user
name in a home path (``C:\\Users\\NAME``, ``/home/NAME``); ``token`` JSON Web Tokens.
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
