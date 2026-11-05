"""The sample ``cluster-report.v1`` payload and an N-node variant derived from it.

``data/cluster_payload.json`` holds two invented nodes, ``NODE1`` and ``NODE2`` (NODE2's
logs start six hours earlier, NODE1 has a gap). :func:`with_nodes` derives a larger
cluster for the multi-node layout: the extra nodes copy NODE1/NODE2 with scaled counts,
later coverage and renamed entries, so every field stays consistent.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

DATA = Path(__file__).parent / "data" / "cluster_payload.json"


def load_payload() -> dict[str, Any]:
    """The two-node sample payload (a fresh copy)."""
    payload: dict[str, Any] = json.loads(DATA.read_text(encoding="utf-8"))
    return payload


def _scale(values: list[int], factor: float, start: int) -> list[int]:
    return [0 if i < start else round(v * factor) for i, v in enumerate(values)]


def with_nodes(n: int) -> dict[str, Any]:
    """The sample grown to ``n`` nodes (``n >= 2``); NODE3.. copy NODE1/NODE2."""
    p = load_payload()
    if n <= 2:
        return p
    nodes = [f"NODE{i + 1}" for i in range(n)]
    cap_nodes = {c["node"]: c for c in p["capture"]["nodes"]}
    tl = p["timeline"]["nodes"]
    for i in range(2, n):
        node = nodes[i]
        src = "NODE1" if i % 2 == 0 else "NODE2"
        factor = 0.55 + 0.2 * (i % 4)
        start = tl[src]["coverage"][0] + i  # later first entry: more hatching
        c = copy.deepcopy(cap_nodes[src])
        c["node"] = node
        c["set"] = {"id": f"{i:02d}" + c["set"]["id"][2:], "name": f"acme-jira_{node.lower()}"}
        c["gaps"] = []
        c["coverage"]["since"] = c["first_ts"] = p["timeline"]["buckets"][start][:-1] + ".000Z"
        for key in ("entries", "errors", "warnings"):
            c[key] = round(c[key] * factor)
        c["verdict"] = None if i % 3 == 0 else c["verdict"]
        c["baseline"] = None if i % 3 == 0 else c["baseline"]
        cap_nodes[node] = c
        tl[node] = {
            "levels": {lv: _scale(v, factor, start) for lv, v in tl[src]["levels"].items()},
            "coverage": [start, tl[src]["coverage"][1]],
        }
        for g in p["groups"]:
            hourly = [[b, round(c * factor)] for b, c in g["hourly"][src] if b >= start]
            hourly = [[b, c] for b, c in hourly if c]
            g["hourly"][node] = hourly
            count = sum(c for _, c in hourly)
            pn = copy.deepcopy(g["per_node"][src])
            pn["count"] = count
            pn["rate_per_hour"] = round(pn["rate_per_hour"] * factor, 3)
            if not count:
                pn["first_seen"] = pn["last_seen"] = None
            g["per_node"][node] = pn
            g["total"] += count
            ids = []
            for eid in g["examples"].get(src, [])[:2]:
                new = f"{i:02d}{eid[2:]}"
                entry = copy.deepcopy(p["entries"][eid])
                entry["node"] = node
                entry["set"] = c["set"]["id"]
                p["entries"][new] = entry
                ids.append(new)
            if ids and count:
                g["examples"][node] = ids
        for ev in list(p["events"]["items"]):
            copied = ev["kind"] in ("job_run", "start", "cache_replication")
            if ev["node"] == src and copied and ev["ts"] >= c["first_ts"]:
                p["events"]["items"].append({**ev, "node": node, "entry_id": ""})
        p["interactions"]["mentions"].append(
            {
                "from": node,
                "to": "NODE1",
                "count": 4 + i,
                "groups": p["interactions"]["mentions"][0]["groups"],
            }
        )
    for g in p["groups"]:
        present = [x for x in nodes if g["per_node"][x]["count"]]
        if len(present) > 1 and g["spread"] == "only":
            g["spread"], g["skew"] = "skewed", None
    p["events"]["items"].sort(key=lambda e: (e["ts"], e["node"]))
    p["nodes"] = nodes
    p["capture"]["nodes"] = [cap_nodes[x] for x in nodes]
    p["capture"]["window"]["since"] = max(c["first_ts"] for c in p["capture"]["nodes"])
    p["interactions"]["concurrent_jobs"][0]["nodes"] = nodes
    return p
