"""Bind the service's request dataclasses to query parameters (the CLI binds the same ones).

``Depends(SomeDataclass)`` would make FastAPI read a ``list[str]`` field from the
request body; :func:`query_model` builds a dependency whose parameters are the
dataclass fields, each a ``Query`` with the field's default and its description
from the ``Attributes:`` section of the class docstring::

    @router.get("/sets/{ref}/entries")
    def entries(ref: str, q: Annotated[EntryQuery, Depends(query_model(EntryQuery))]) -> ...
"""

from __future__ import annotations

import dataclasses
import inspect
import re
import typing
from collections.abc import Callable
from functools import cache
from typing import Any

from fastapi import Query

__all__ = ["field_docs", "query_model"]

_ATTRIBUTE = re.compile(r"^(?P<indent>\s+)(?P<name>\w+): (?P<text>.*)$")


def field_docs(cls: type) -> dict[str, str]:
    """Descriptions of the fields in the ``Attributes:`` section of ``cls``'s docstring."""
    docs: dict[str, str] = {}
    lines = inspect.cleandoc(cls.__doc__ or "").splitlines()
    try:
        start = lines.index("Attributes:") + 1
    except ValueError:
        return docs
    name, indent = None, ""
    for line in lines[start:]:
        if not line.strip():
            break
        m = _ATTRIBUTE.match(line)
        if m is not None and (name is None or len(m["indent"]) <= len(indent)):
            name, indent = m["name"], m["indent"]
            docs[name] = m["text"].strip()
        elif name is not None:
            docs[name] += " " + line.strip()
    return docs


@cache
def query_model[T](cls: type[T]) -> Callable[..., T]:
    """Return a FastAPI dependency that builds dataclass ``cls`` from query parameters."""
    if not dataclasses.is_dataclass(cls):
        raise TypeError(f"{cls.__name__} is not a dataclass")
    hints = typing.get_type_hints(cls)
    docs = field_docs(cls)
    params = []
    for f in dataclasses.fields(cls):
        if f.default is not dataclasses.MISSING:
            default: Any = f.default
        elif f.default_factory is not dataclasses.MISSING:
            default = f.default_factory()
        else:
            default = ...
        params.append(
            inspect.Parameter(
                f.name,
                inspect.Parameter.KEYWORD_ONLY,
                default=Query(default, description=docs.get(f.name)),
                annotation=hints[f.name],
            )
        )

    def dependency(**values: Any) -> T:
        return cls(**values)

    dependency.__signature__ = inspect.Signature(params, return_annotation=cls)  # type: ignore[attr-defined]
    dependency.__name__ = f"query_{cls.__name__}"
    return dependency
