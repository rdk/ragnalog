"""Optional Drain3 adapter that proposes merging signatures with similar templates.

Requires the ``mine`` extra (``pip install atlas-log-analyzer[mine]``). Drain runs
over the distinct *templates* only, never over raw entries, so it is cheap; its
cluster ids are not stable and are therefore only used to propose merges.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

__all__ = ["MergeProposal", "propose_merges"]


@dataclass(frozen=True, slots=True)
class MergeProposal:
    """Signatures whose templates Drain put into one cluster."""

    cluster_id: int
    signatures: tuple[str, ...]
    """Sorted, distinct signature hashes (at least two)."""
    drain_template: str
    """Drain's template of the cluster (``<*>`` marks the variable tokens)."""


def _template_miner(sim_th: float, depth: int) -> Any:
    try:
        from drain3 import TemplateMiner
        from drain3.template_miner_config import TemplateMinerConfig
    except ImportError as exc:
        raise ImportError(
            "template mining needs the optional 'drain3' dependency; "
            "install it with: pip install 'atlas-log-analyzer[mine]'"
        ) from exc
    config = TemplateMinerConfig()
    config.profiling_enabled = False
    config.drain_sim_th = sim_th
    config.drain_depth = depth
    return TemplateMiner(config=config)


def propose_merges(
    templates: Iterable[tuple[str, str]], *, sim_th: float = 0.4, depth: int = 4
) -> list[MergeProposal]:
    """Cluster templates with Drain3 and propose merges for clusters of ≥ 2 signatures.

    Args:
        templates: ``(signature, template)`` pairs, typically one per group.
        sim_th: Drain similarity threshold.
        depth: Drain parse-tree depth.

    Returns:
        Proposals ordered by cluster id.

    Raises:
        ImportError: When ``drain3`` is not installed.

    """
    miner = _template_miner(sim_th, depth)
    members: dict[int, set[str]] = {}
    for signature, template in sorted(set(templates)):
        result = miner.add_log_message(template)
        members.setdefault(int(result["cluster_id"]), set()).add(signature)
    clusters = {int(c.cluster_id): c for c in miner.drain.clusters}
    proposals = []
    for cluster_id in sorted(members):
        signatures = members[cluster_id]
        if len(signatures) < 2:
            continue
        cluster = clusters.get(cluster_id)
        drain_template = cluster.get_template() if cluster is not None else ""
        proposals.append(MergeProposal(cluster_id, tuple(sorted(signatures)), drain_template))
    return proposals
