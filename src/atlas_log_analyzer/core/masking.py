"""Masking of variable tokens: turns a message into a template plus variables.

Example::

    masker = Masker(JIRA_RULES + GENERIC_RULES)
    template, variables = masker.apply("Script field failed on issue: ABC-69517, field: Finished On")
    # ("Script field failed on issue: <ISSUE>, field: Finished On", ("ABC-69517",))
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

__all__ = ["GENERIC_RULES", "MASK_RULES_VERSION", "MaskRule", "Masker"]

MASK_RULES_VERSION = 1
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


GENERIC_RULES: Sequence[MaskRule] = ()
"""Built-in, format-independent rules (timestamps, UUIDs, IPs, hex ids, numbers …), in order."""


class Masker:
    """Applies an ordered sequence of :class:`MaskRule` to text."""

    def __init__(self, rules: Iterable[MaskRule]) -> None:
        self.rules: tuple[MaskRule, ...] = tuple(rules)

    def apply(self, text: str) -> tuple[str, tuple[str, ...]]:
        """Return ``(template, variables)``; variables in order of appearance."""
        raise NotImplementedError

    def template(self, text: str) -> str:
        """Return only the template (faster when variables are not needed)."""
        raise NotImplementedError
