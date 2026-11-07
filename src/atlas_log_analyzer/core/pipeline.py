"""The ingest recipe and the pure stages that turn parsed entries into signed groups.

A :class:`Recipe` names everything that decides an entry's signature: the format
(and its version), the masking rules and the signature spec. Two log sets are
comparable exactly when their :attr:`Recipe.key` is equal::

    recipe = Recipe(JiraFormat(), DEFAULT_SPEC)
    recipe.key                 # "jira@3|L+G+E+F0|sig1|mask2"
    grouper = recipe.grouper()
    signed = grouper.sign(unique_entries(parse_sources(sources, recipe.fmt)))
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field

import xxhash

from atlas_log_analyzer.core.formats.base import LogFormat, titles_from_body
from atlas_log_analyzer.core.grouping import Grouper
from atlas_log_analyzer.core.masking import GENERIC_RULES, MASK_RULES_VERSION, Masker, MaskRule
from atlas_log_analyzer.core.models import LogEntry
from atlas_log_analyzer.core.signature import SIGNATURE_VERSION, SignatureSpec, Signer

__all__ = [
    "RECIPE_PARTS",
    "Recipe",
    "format_version",
    "key_format_version",
    "recipe_differences",
    "recipe_key",
    "rules_digest",
    "unique_entries",
]

RECIPE_PARTS = ("format", "signature_spec", "signature_version", "mask_rules_version", "rules")
"""Names of the ``|``-separated parts of a recipe key, in order (``rules`` is optional)."""


def format_version(fmt: LogFormat) -> int:
    """Return the format's ``version`` attribute, ``1`` when it has none.

    Bump a format's version when its masking rules or the entries it builds change
    signatures. Read with ``getattr`` so third-party formats without the attribute
    still satisfy the :class:`~atlas_log_analyzer.core.formats.base.LogFormat` protocol.
    """
    return int(getattr(fmt, "version", 1))


def key_format_version(key: str) -> int | None:
    """Format version a recipe key names (``3`` for ``"jira@3|L+G+E+F0|sig1|mask2"``).

    ``None`` when the key's first part has no ``@<int>``.
    """
    _, sep, version = key.partition("|")[0].rpartition("@")
    return int(version) if sep and version.isdigit() else None


def rules_digest(rules: Sequence[MaskRule]) -> str:
    """xxh3-64 hex of the rules' names, patterns, flags and placeholders, in order."""
    text = "\n".join(
        f"{r.name}\x1f{r.pattern.pattern}\x1f{r.pattern.flags}\x1f{r.placeholder}" for r in rules
    )
    return xxhash.xxh3_64_hexdigest(text.encode("utf-8", "surrogatepass"))


def recipe_key(
    format_name: str,
    format_version: int,
    spec_key: str,
    *,
    signature_version: int = SIGNATURE_VERSION,
    mask_rules_version: int = MASK_RULES_VERSION,
    rules: str | None = None,
) -> str:
    """Build a recipe key such as ``"jira@3|L+G+E+F0|sig1|mask2"``.

    Args:
        format_name: Format registry key.
        format_version: See :func:`format_version`.
        spec_key: :attr:`SignatureSpec.key`.
        signature_version: :data:`~atlas_log_analyzer.core.signature.SIGNATURE_VERSION`.
        mask_rules_version: :data:`~atlas_log_analyzer.core.masking.MASK_RULES_VERSION`.
        rules: :func:`rules_digest` of extra masking rules; appended as ``|rules:<digest>``.

    """
    key = (
        f"{format_name}@{format_version}|{spec_key}|sig{signature_version}|mask{mask_rules_version}"
    )
    return f"{key}|rules:{rules}" if rules else key


def _split(key: str) -> dict[str, str]:
    parts = key.split("|")
    out = dict(zip(RECIPE_PARTS[:4], parts[:4], strict=False))
    extra = [p.removeprefix("rules:") for p in parts[4:]]
    out["rules"] = "|".join(extra) if extra else "none"
    return out


def recipe_differences(a: str, b: str) -> list[str]:
    """Describe the parts in which two recipe keys differ, e.g. ``"signature_spec: L+G+E+F0 != …"``.

    Empty when the keys are equal.
    """
    if a == b:
        return []
    pa, pb = _split(a), _split(b)
    out = [
        f"{name}: {pa.get(name)} != {pb.get(name)}"
        for name in RECIPE_PARTS
        if pa.get(name) != pb.get(name)
    ]
    return out or [f"recipe: {a} != {b}"]


@dataclass(frozen=True, slots=True)
class Recipe:
    """Format, signature spec and extra masking rules: everything that decides signatures.

    ``extra_rules`` are applied before the format's rules, which come before
    :data:`~atlas_log_analyzer.core.masking.GENERIC_RULES` (at a given position the
    earlier rule wins).
    """

    fmt: LogFormat
    spec: SignatureSpec
    extra_rules: tuple[MaskRule, ...] = field(default=())

    @property
    def rules(self) -> tuple[MaskRule, ...]:
        """All masking rules, in the order the masker applies them."""
        return (*self.extra_rules, *self.fmt.mask_rules, *GENERIC_RULES)

    @property
    def key(self) -> str:
        """Comparability key, e.g. ``"jira@3|L+G+E+F0|sig1|mask2"`` (see :func:`recipe_key`)."""
        return recipe_key(
            self.fmt.name,
            format_version(self.fmt),
            self.spec.key,
            rules=rules_digest(self.extra_rules) if self.extra_rules else None,
        )

    def masker(self) -> Masker:
        """Build a :class:`Masker` over :attr:`rules`."""
        return Masker(self.rules)

    def signer(self) -> Signer:
        """Build a :class:`Signer` with :meth:`masker`, :attr:`spec` and the format's titles.

        ``titles_from_body`` comes from the format
        (:func:`~atlas_log_analyzer.core.formats.base.titles_from_body`).
        """
        return Signer(self.masker(), self.spec, titles_from_body=titles_from_body(self.fmt))

    def grouper(self, *, max_examples: int = 3) -> Grouper:
        """Build a :class:`Grouper` over :meth:`signer`."""
        return Grouper(self.signer(), max_examples=max_examples)


def unique_entries(entries: Iterable[LogEntry]) -> Iterator[LogEntry]:
    """Drop entries whose ``id`` was already seen (first occurrence wins).

    Put this in front of the grouper so group statistics match the
    deduplicated ``entries.parquet``.
    """
    seen: set[str] = set()
    for e in entries:
        if e.id not in seen:
            seen.add(e.id)
            yield e
