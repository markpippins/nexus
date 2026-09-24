"""Bounded thread-pull over AST neighborhoods (Structure S5).

Implements the read-only bounded thread-pull operation per to-do thread
925f229d and architect ruling 143b0e04 Q3: a Structure *query/expansion
service* — not parser behavior, not a blackboard authority. Anchored
structural fact → source link → parseable target → finite AST neighborhood,
with every bound explicit:

- **Direction** (required): ``ancestors`` (node_path parents), ``descendants``
  (node_path subtree), ``siblings`` (same parent, other nodes), or
  ``references`` (S4 candidate/reference edges out of the anchor).
- **Budgets** (all explicit, all enforced): ``max_depth`` (path-segments for
  ancestors/descendants/siblings; edge-hops for references), ``max_nodes``,
  ``max_bytes`` (canonical-JSON size of the returned items).
- **Cycle policy**: ``skip_visited`` (default — visit each node once,
  deterministic) or ``fail_closed`` (raise on revisits). The references
  direction follows S4 candidate/finding edges, which can close cycles
  (A's FK points at B, B's at A); the policy names the behavior.
- **Deterministic ordering**: results sort by (depth, node_path,
  observation_id). Same read set in, same neighborhood out — byte-stable.
- **Honest truncation**: when a budget cuts the traversal, the result
  reports what was omitted (``truncated``, ``truncation`` detail with the
  remaining frontier size) — partial results never masquerade as complete.
- **Provenance on every item**: each returned item is (or embeds) a full
  observation dict with source/identity/anchor — no bare node paths.
- **Visible missing/unparseable targets**: a references-hop naming an
  observation that is not in the run, or an anchor that cannot be located,
  yields an explicit ``unresolved_targets`` entry — never silence.

Read-only: pure function over the caller-supplied run dict. No I/O.
"""

from __future__ import annotations

import json
from typing import Any

try:
    from . import bridge as br  # package context
except ImportError:  # pragma: no cover - direct-run context
    import bridge as br  # type: ignore[no-redef]

DIRECTIONS = ("ancestors", "descendants", "siblings", "references")
CYCLE_POLICIES = ("skip_visited", "fail_closed")
DEFAULT_BUDGETS = {"max_depth": 4, "max_nodes": 64, "max_bytes": 65536}


class QueryError(ValueError):
    """Malformed or out-of-contract query."""


class CycleError(QueryError):
    """Cycle encountered under the fail_closed policy."""


def _path_segments(node_path: str) -> list[str]:
    return [s for s in node_path.split(".") if s]


def _parent_path(node_path: str) -> str | None:
    segs = _path_segments(node_path)
    if len(segs) <= 1:
        return None
    return ".".join(segs[:-1])


def _canonical_size(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _observation_index(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for obs in run.get("observations", []):
        index[obs.get("observation_id", "")] = obs
    return index


def _add_edge(edges: dict[str, list[str]], a: str, b: str) -> None:
    """Record one UNDIRECTED query edge.

    Neither channel determines direction: bridge candidates are recognition
    output whose mapping authority belongs to Resolution/Aspects, and
    name-based links are source-anchored (never UUID resolution). A query
    edge is a walkable adjacency, not an admitted relation.
    """
    if a == b:
        return
    edges.setdefault(a, []).append(b)
    edges.setdefault(b, []).append(a)


def _reference_edges(run: dict[str, Any]) -> dict[str, list[str]]:
    """observation_id -> adjacent observation_ids (from S4 bridge output).

    The run dict may embed a ``bridge`` section (S4 ``derive_candidates``
    output); each candidate's evidence_refs link their primary ref to every
    further ref. Bridge results are recognition output over governed
    vocabulary — using them as *query edges* does not admit anything.
    """
    edges: dict[str, list[str]] = {}
    bridge = run.get("bridge") or {}
    for cand in bridge.get("candidates", []):
        refs = [r for r in (cand.get("evidence_refs") or []) if r]
        if len(refs) < 2:
            continue
        for target in refs[1:]:
            _add_edge(edges, refs[0], target)
    return edges


def _table_link_edges(run: dict[str, Any]) -> dict[str, list[str]]:
    """Cross-observation edges from FK/derivation facts to their targets.

    Deterministic name-based linking inside the run: an FK observation links
    to the table observation whose payload 'table' equals its
    'references_table' (source-anchored linking — never UUID resolution).
    """
    by_table: dict[str, list[str]] = {}
    for obs in run.get("observations", []):
        if obs.get("fact_kind") == "table":
            name = (obs.get("payload") or {}).get("table")
            if name:
                by_table.setdefault(str(name), []).append(obs["observation_id"])
    edges: dict[str, list[str]] = {}
    for obs in run.get("observations", []):
        p = obs.get("payload") or {}
        target_table = None
        if obs.get("fact_kind") == "foreign_key":
            target_table = p.get("references_table")
        elif obs.get("fact_kind") == "operation" and p.get("action") == "insert_select":
            src = str(p.get("source", ""))
            for t in by_table:
                if t and t in src:
                    target_table = t
                    break
        if target_table and target_table in by_table:
            for oid in by_table[target_table]:
                _add_edge(edges, obs["observation_id"], oid)
    return edges


def _frontier_key(t: tuple[str, int], index: dict[str, dict[str, Any]]) -> tuple:
    """Deterministic frontier ordering; dangling oids (not yet resolved)
    sort after resolvable ones by (depth, '', oid) — never crash."""
    oid, depth = t
    obs = index.get(oid)
    path = obs["anchor"]["node_path"] if obs else ""
    return (depth, path, oid)


def _sort_key(item: dict[str, Any]) -> tuple:
    return (
        item.get("_depth", 0),
        item.get("anchor", {}).get("node_path", ""),
        item.get("observation_id", ""),
    )


def _check_query(query: dict[str, Any]) -> None:
    direction = query.get("direction")
    if direction not in DIRECTIONS:
        raise QueryError(f"direction must be one of {DIRECTIONS}, got {direction!r}")
    policy = query.get("cycle_policy", "skip_visited")
    if policy not in CYCLE_POLICIES:
        raise QueryError(f"cycle_policy must be one of {CYCLE_POLICIES}, got {policy!r}")
    budgets = {**DEFAULT_BUDGETS, **(query.get("budgets") or {})}
    for key in DEFAULT_BUDGETS:
        v = budgets[key]
        if not isinstance(v, int) or v < 1:
            raise QueryError(f"budget {key} must be a positive integer, got {v!r}")
    if not query.get("anchor_observation_id"):
        raise QueryError("anchor_observation_id is required")


def pull_neighborhood(run: dict[str, Any], query: dict[str, Any]) -> dict[str, Any]:
    """Execute one bounded thread-pull over a run's AST neighborhood.

    The run dict may carry an embedded ``bridge`` section (S4) for the
    references direction. The result echoes the run's read-set fingerprint
    and the parser/grammar revisions under which the neighborhood was
    produced (ruling Q3 requirement).
    """
    _check_query(query)
    direction = query["direction"]
    policy = query.get("cycle_policy", "skip_visited")
    budgets = {**DEFAULT_BUDGETS, **(query.get("budgets") or {})}
    kind_filter = query.get("fact_kind_filter")
    anchor_id = query["anchor_observation_id"]

    index = _observation_index(run)
    anchor = index.get(anchor_id)
    if anchor is None:
        raise QueryError(f"anchor observation {anchor_id!r} not in run")

    items: list[dict[str, Any]] = []
    unresolved_targets: list[dict[str, Any]] = []
    emitted: set[str] = set()
    queued: set[str] = {anchor_id}
    bytes_used = 0
    truncated = False
    truncation: dict[str, Any] | None = None
    frontier: list[tuple[str, int]] = []

    # seed frontier per direction; depth is RELATIVE distance from the anchor
    anchor_depth = len(_path_segments(anchor["anchor"]["node_path"]))
    anchor_path = anchor["anchor"]["node_path"]
    def _admit(oid: str, depth: int) -> None:
        """Push one frontier entry, enforcing the cycle policy.

        A node re-encountered through a second path (already queued or
        emitted, or the anchor itself) is a cycle/crossing: fail_closed
        raises, skip_visited silently skips. Structural directions cannot
        trigger it (node paths are unique per run); the references direction
        follows S4 edges, which can.
        """
        if oid == anchor_id or oid in queued or oid in emitted:
            if policy == "fail_closed":
                raise CycleError(
                    f"cycle detected at {oid!r} under fail_closed policy"
                )
            return
        queued.add(oid)
        frontier.append((oid, depth))

    if direction == "references":
        # union the two edge channels per source — a dict-level merge would
        # CLOBBER one channel's edges wherever both name the same node
        merged: dict[str, list[str]] = {}
        for src, dsts in _reference_edges(run).items():
            merged.setdefault(src, []).extend(dsts)
        for src, dsts in _table_link_edges(run).items():
            merged.setdefault(src, []).extend(dsts)
        edges = merged
        for target in sorted(set(edges.get(anchor_id, []))):
            _admit(target, 1)
    else:
        parent = _parent_path(anchor_path)
        for obs in run.get("observations", []):
            if obs["observation_id"] == anchor_id:
                continue
            path = obs["anchor"]["node_path"]
            if direction == "ancestors":
                if anchor_path.startswith(path + "."):
                    depth = anchor_depth - len(_path_segments(path))
                    _admit(obs["observation_id"], depth)
            elif direction == "descendants":
                if path.startswith(anchor_path + "."):
                    depth = len(_path_segments(path)) - anchor_depth
                    _admit(obs["observation_id"], depth)
            elif direction == "siblings":
                if parent and _parent_path(path) == parent:
                    _admit(obs["observation_id"], 1)
    frontier.sort(key=lambda t: _frontier_key(t, index))

    max_depth = budgets["max_depth"]
    max_nodes = budgets["max_nodes"]
    max_bytes = budgets["max_bytes"]

    while frontier:
        if len(items) >= max_nodes:
            truncated = True
            truncation = {"budget": "max_nodes", "frontier_remaining": len(frontier)}
            break
        oid, depth = frontier.pop(0)
        if oid in emitted:  # invariant guard; _admit dedupes at push time
            continue
        if depth > max_depth:
            # max_depth EXHAUSTION is not truncation — mark bounded-honesty
            # and skip the node (never expanded), but keep draining the
            # frontier so in-budget nodes behind it are still returned.
            truncated = True
            if truncation is None or truncation.get("budget") != "max_nodes":
                truncation = {"budget": "max_depth", "frontier_remaining": 0}
            continue
        target = index.get(oid)
        if target is None:
            unresolved_targets.append({
                "observation_id": oid,
                "reason": "referenced observation not present in run",
                "at_depth": depth,
            })
            continue
        if kind_filter and target.get("fact_kind") not in kind_filter:
            emitted.add(oid)
            continue
        candidate_item = {
            **target,
            "_depth": depth,
            "_via_direction": direction,
        }
        size = _canonical_size(candidate_item)
        if bytes_used + size > max_bytes:
            truncated = True
            truncation = {
                "budget": "max_bytes",
                "frontier_remaining": len(frontier) + 1,
            }
            break
        emitted.add(oid)
        items.append(candidate_item)
        bytes_used += size
        # expand deeper (structural directions only; references are one
        # anchored hop per depth level via the same edge tables)
        if direction == "references":
            for nxt in sorted(set(edges.get(oid, []))):
                _admit(nxt, depth + 1)
            frontier.sort(key=lambda t: _frontier_key(t, index))

    items.sort(key=_sort_key)
    return {
        "query": {
            "direction": direction,
            "anchor_observation_id": anchor_id,
            "cycle_policy": policy,
            "budgets": budgets,
            "fact_kind_filter": list(kind_filter) if kind_filter else None,
        },
        "provenance": {
            "read_set_fingerprint": run.get("read_set_fingerprint"),
            "run_id": run.get("run_id"),
            "parser": run.get("parser"),
            "contract_revision": run.get("contract_revision"),
        },
        "anchor": {
            "observation_id": anchor_id,
            "node_path": anchor["anchor"]["node_path"],
            "fact_kind": anchor.get("fact_kind"),
        },
        "items": items,
        "item_count": len(items),
        "bytes_returned": bytes_used,
        "truncated": truncated,
        "truncation": truncation,
        "unresolved_targets": sorted(
            unresolved_targets, key=lambda u: (u["at_depth"], u["observation_id"])
        ),
        "authority_status": "non_authoritative",
        "note": (
            "bounded query/expansion service output; presentation on the "
            "blackboard is adapter work (ruling Q3), not a Structure authority"
        ),
    }
