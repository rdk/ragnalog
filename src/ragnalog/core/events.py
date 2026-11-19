r"""Event rules: which groups of a log mark cluster events (a restart, an index snapshot …).

A rule matches a **group**, by its logger and its masked template, never raw text,
so a capture's events come from scanning ``groups.parquet`` (1-3k rows per set)
and then reading only the entries of the matching signatures. Rules need no
re-ingest and can change without rebuilding sets.

A format lists its rules in the optional attribute ``event_rules`` (a tuple of
:class:`EventRule`, default ``()``; read it with :func:`event_rules`). It is not a
member of the :class:`~ragnalog.core.formats.base.LogFormat` protocol,
like ``version``, so formats without rules still pass ``isinstance``. The first
rule that matches a group decides its kind.

Placeholders: the masking rules replace variable tokens in templates (``<NUM>``,
``<ID>`` …). A rule's regex is written so that it matches the template *and* the
raw message (its variable parts are generic, like ``[^,\\s]+``); when the subject a
rule captures from the template contains a placeholder (``NODE<NUM>``), the
subject is taken from each entry's message instead (:meth:`EventRule.subject`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from typing import Final

__all__ = [
    "EVENT_KINDS",
    "EVENT_RULES_VERSION",
    "SUBJECT_LOGGER",
    "EventRule",
    "abbreviate",
    "classify",
    "event_rules",
    "has_placeholder",
]

EVENT_RULES_VERSION: Final = 1
"""Bump when the built-in rules (``core/formats/*_events.py``) or their matching change."""

EVENT_KINDS: Final = (
    "start",
    "stop",
    "health_failed",
    "cache_replication",
    "cache_peer",
    "index_replication",
    "index_snapshot",
    "index_restore",
    "index_replay",
    "reindex",
    "cluster_lock",
    "job_run",
)
"""Kinds of events, in the order a timeline legend lists them."""

SUBJECT_LOGGER: Final = "@logger"
"""``subject_group`` value meaning "the logger's class name" (``GroupsSyncJobRunnerImpl``)."""

_PLACEHOLDER = re.compile(r"<(?:[A-Z][A-Z0-9_]*|\*)>")


def has_placeholder(text: str) -> bool:
    """Whether ``text`` holds a masking placeholder such as ``<NUM>`` or ``<*>``."""
    return _PLACEHOLDER.search(text) is not None


def abbreviate(name: str) -> str:
    """Return a logger or logger prefix with every package reduced to its first letter.

    Jira shortens logger packages to their initials as far as its layout needs
    (``c.a.j.index.ha.DefaultNodeReindexService``, ``c.a.jira.startup.…``), so
    names are compared in this form: ``com.atlassian.jira.index.ha.X`` and
    ``c.a.j.index.ha.X`` both become ``c.a.j.i.h.X``. A trailing ``.`` (a package
    prefix) is kept and its last part abbreviated too.
    """
    if not name:
        return ""
    parts = name.split(".")
    if name.endswith("."):
        return ".".join(p[:1] for p in parts[:-1]) + "."
    return ".".join([*(p[:1] for p in parts[:-1]), parts[-1]])


@cache
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


@dataclass(frozen=True, slots=True)
class EventRule:
    """Groups whose logger and template match mark events of one kind.

    Attributes:
        kind: One of :data:`EVENT_KINDS`.
        logger_prefix: The logger, or package (ending in ``.``), the group's logger
            must start with; compared with packages abbreviated (:func:`abbreviate`).
            Empty: any logger.
        template_re: Regex searched in the template (and, for the subject, in the
            message); empty matches every template.
        subject_group: Name of the regex group holding the event's subject (a node
            id, a health check, a job key), :data:`SUBJECT_LOGGER` for the logger's
            class name, or ``None``.
        logger_re: Optional regex searched in the logger as stored (``JobRunner$``).

    """

    kind: str
    logger_prefix: str
    template_re: str
    subject_group: str | None = None
    logger_re: str = ""

    def matches(self, logger: str | None, template: str | None) -> re.Match[str] | None:
        """Return the template match when the rule applies to a group, else ``None``."""
        name = logger or ""
        if self.logger_prefix and not abbreviate(name).startswith(abbreviate(self.logger_prefix)):
            return None
        if self.logger_re and _compiled(self.logger_re).search(name) is None:
            return None
        return _compiled(self.template_re).search(template or "")

    def subject(
        self, logger: str | None, match: re.Match[str] | None, message: str | None
    ) -> str | None:
        """Return the subject of one entry of a matching group.

        Taken from the template ``match`` when it holds no placeholder, else from
        the entry's ``message``; ``None`` when the rule has no subject or the
        group did not take part in the match.
        """
        group = self.subject_group
        if group is None:
            return None
        if group == SUBJECT_LOGGER:
            return (logger or "").rsplit(".", 1)[-1] or None
        found = _group(match, group)
        if found is not None and not has_placeholder(found):
            return found
        if message:
            from_message = _group(_compiled(self.template_re).search(message), group)
            if from_message is not None:
                return from_message
        return found

    @property
    def needs_message(self) -> bool:
        """Whether a subject may have to come from each entry's message."""
        return self.subject_group is not None and self.subject_group != SUBJECT_LOGGER


def _group(match: re.Match[str] | None, name: str) -> str | None:
    if match is None or name not in match.re.groupindex:
        return None
    value = match.group(name)
    if value is None:
        return None
    value = value.strip().rstrip(".")
    return value or None


def classify(
    rules: tuple[EventRule, ...], logger: str | None, template: str | None
) -> tuple[EventRule, re.Match[str]] | None:
    """Return the first rule matching a group and its template match, or ``None``."""
    for rule in rules:
        m = rule.matches(logger, template)
        if m is not None:
            return rule, m
    return None


def event_rules(fmt: object) -> tuple[EventRule, ...]:
    """Return a format's event rules (its ``event_rules`` attribute; ``()`` when none)."""
    rules = getattr(fmt, "event_rules", ())
    return tuple(r for r in rules if isinstance(r, EventRule))
