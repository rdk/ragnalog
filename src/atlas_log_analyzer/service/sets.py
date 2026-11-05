"""Where documents read their sets from: the :class:`Sets` protocol and set references."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from atlas_log_analyzer.service.errors import Front, OpError, hint

if TYPE_CHECKING:
    from atlas_log_analyzer.store.logset import LogSet, LogSetManifest

__all__ = ["Sets", "open_set"]


class Sets(Protocol):
    """Read access to the log sets of a store.

    Satisfied by :class:`~atlas_log_analyzer.store.store.Store` and by the web app's
    ``AppState`` (which adds its caches).
    """

    def open(self, ref: str) -> LogSet:
        """Open a set by id, name or unique id prefix (raises the store's typed errors)."""
        ...

    def manifests(self) -> list[LogSetManifest]:
        """All readable manifests, newest first."""
        ...


def open_set(sets: Sets, ref: str, *, front: Front) -> LogSet:
    """Open the set ``ref``; an unknown or ambiguous reference is an :class:`OpError`.

    An unknown set is reason ``set_not_found`` (CLI ``store_error``, API ``not_found``)
    whose message names only the reference; the CLI's hint lists the available sets.
    An ambiguous one is reason ``ambiguous_ref``.
    """
    from atlas_log_analyzer.store.store import AmbiguousLogSet, LogSetNotFound

    try:
        return sets.open(ref)
    except LogSetNotFound as exc:
        found = hint("set_not_found", front)
        if front == "cli":
            names = [m.name or m.id for m in sets.manifests()]
            found = (
                f"available sets: {', '.join(names)}"
                if names
                else "the store is empty; create a set with `atlas-log ingest PATH`"
            )
        raise OpError.because("set_not_found", front, f"no log set {ref!r}", hint=found) from exc
    except AmbiguousLogSet as exc:
        raise OpError.because("ambiguous_ref", front, str(exc)) from exc
