"""Clustering v2 partitioner: directory-prior + SCC contraction + Louvain community detection.

Aligned with v3.md section 4.3 three-step pipeline (L333-L347).
"""

import logging
from collections import defaultdict
from typing import Any

import networkx as nx
from networkx.algorithms.community import louvain_communities

from codewiki.src.be.clustering.graph_builder import build_clustering_graph

logger = logging.getLogger(__name__)

# Directory-prior intra-partition edge weight injected before Louvain.
_DIR_PRIOR_WEIGHT: float = 2.0

# Minimum cluster size; smaller clusters are merged into nearest neighbour.
_MIN_CLUSTER_SIZE: int = 3


# ---------------------------------------------------------------------------
# Step 1: directory prior grouping (v3.md L333)
# ---------------------------------------------------------------------------


def partition_by_directory(
    component_ids: list[str],
    component_file_map: dict[str, str],
) -> dict[str, set[str]]:
    """Group component_ids by their top-level directory.

    The top-level directory is the first path segment.
    Components with no directory (root files) go into "_root".

    Returns: {partition_name: set(component_ids)}
    """
    groups: dict[str, set[str]] = defaultdict(set)
    for cid in component_ids:
        path = component_file_map.get(cid, "")
        parts = path.split("/")
        if len(parts) > 1:
            top_dir = parts[0]
        else:
            top_dir = "_root"
        groups[top_dir].add(cid)
    return dict(groups)


# ---------------------------------------------------------------------------
# Step 2: SCC contraction (v3.md L334)
# ---------------------------------------------------------------------------


def contract_sccs(
    graph: nx.Graph,
    directed_edges: list[tuple[str, str]],
) -> tuple[nx.Graph, dict[str, str]]:
    """Contract strongly-connected components into super-nodes.

    Args:
        graph: Undirected weighted graph (from build_clustering_graph).
        directed_edges: (from_component, to_component) directed pairs.

    Returns:
        (contracted_graph, node_map) where node_map maps every original
        node to its super-node ID (lexicographically first in the SCC).
        Nodes not in any multi-node SCC map to themselves.
    """
    # Build directed graph from only nodes present in the undirected graph
    valid_nodes = set(graph.nodes)
    dg = nx.DiGraph()
    for u, v in directed_edges:
        if u in valid_nodes and v in valid_nodes:
            dg.add_edge(u, v)
    # Add isolated nodes
    for n in valid_nodes:
        if n not in dg:
            dg.add_node(n)

    # Find SCCs
    sccs = list(nx.strongly_connected_components(dg))

    # Build node → super-node mapping
    node_map: dict[str, str] = {}
    for scc in sccs:
        super_id = min(scc)  # lexicographically first
        for cid in scc:
            node_map[cid] = super_id

    # Ensure all graph nodes have a mapping (even if not in digraph)
    for n in valid_nodes:
        if n not in node_map:
            node_map[n] = n

    # Build contracted graph
    super_nodes = set(node_map.values())
    pair_weights: dict[tuple[str, str], float] = defaultdict(float)

    for u, v, data in graph.edges(data=True):
        su = node_map[u]
        sv = node_map[v]
        if su != sv:
            pair = (min(su, sv), max(su, sv))
            pair_weights[pair] += data.get("weight", 1.0)

    contracted = nx.Graph()
    for sn in sorted(super_nodes):
        contracted.add_node(sn)
    for (u, v), w in pair_weights.items():
        contracted.add_edge(u, v, weight=w)

    return contracted, node_map


# ---------------------------------------------------------------------------
# Step 3: community detection with directory prior injection (v3.md L335)
# ---------------------------------------------------------------------------


def detect_communities(
    graph: nx.Graph,
    dir_partitions: dict[str, set[str]],
    seed: int = 42,
    min_cluster_size: int = _MIN_CLUSTER_SIZE,
) -> list[set[str]]:
    """Run Louvain community detection with directory-prior bias.

    Injects strong intra-partition edges to bias Louvain toward respecting
    directory structure. Small clusters are merged into nearest neighbour.

    Returns: list of communities (sets of node IDs), largest first.
    """
    if graph.number_of_nodes() == 0:
        return []

    if graph.number_of_nodes() < 3:
        return [set(graph.nodes)]

    # Inject directory-prior edges
    aug = graph.copy()
    for partition_members in dir_partitions.values():
        # Only include members that are actually in the graph
        members_in_graph = [m for m in partition_members if m in aug]
        for i in range(len(members_in_graph)):
            for j in range(i + 1, len(members_in_graph)):
                a, b = members_in_graph[i], members_in_graph[j]
                if aug.has_edge(a, b):
                    aug[a][b]["weight"] += _DIR_PRIOR_WEIGHT
                else:
                    aug.add_edge(a, b, weight=_DIR_PRIOR_WEIGHT)

    # Run Louvain
    try:
        communities = list(louvain_communities(aug, weight="weight", seed=seed))
    except Exception as exc:
        logger.warning("Louvain failed (%s); returning single community", exc)
        return [set(graph.nodes)]

    if not communities:
        return [set(graph.nodes)]

    # Merge tiny communities
    communities = _merge_small_communities(communities, graph, min_cluster_size)

    # Sort: largest first, then by sorted first member for determinism
    communities.sort(key=lambda c: (-len(c), min(c) if c else ""))

    return communities


def _merge_small_communities(
    communities: list[set[str]],
    graph: nx.Graph,
    min_size: int = _MIN_CLUSTER_SIZE,
) -> list[set[str]]:
    """Merge communities smaller than min_size into their nearest neighbour."""
    while True:
        small = [c for c in communities if len(c) < min_size]
        large = [c for c in communities if len(c) >= min_size]
        if not small or not large:
            break
        for tiny in small:
            best_idx, best_score = 0, -1
            for idx, big in enumerate(large):
                score = sum(1 for n in tiny for nbr in graph.neighbors(n) if nbr in big)
                if score > best_score:
                    best_idx, best_score = idx, score
            large[best_idx] = large[best_idx] | tiny
        communities = large
    return communities


# ---------------------------------------------------------------------------
# Public API: partition_components
# ---------------------------------------------------------------------------


def partition_components(
    component_ids: list[str],
    component_file_map: dict[str, str],
    edges: list[Any],  # list[SymbolEdge]
    seed: int = 42,
) -> list[list[str]]:
    """Full three-step partition pipeline (v3.md L333-L347).

    1. Build weighted graph from edges.
    2. Directory prior grouping.
    3. SCC contraction.
    4. Louvain community detection.
    5. Expand super-nodes back to original component_ids.

    Returns: list of clusters, each a sorted list of component_ids.
    """
    if not component_ids:
        return []

    # Build weighted graph
    graph = build_clustering_graph(edges, set(component_ids), component_file_map)

    # Step 1: directory priors
    dir_partitions = partition_by_directory(component_ids, component_file_map)

    # Step 2: SCC contraction
    # Build directed edges from SymbolEdge list
    from codewiki.src.be.clustering.graph_builder import _extract_file_from_symbol

    file_to_comps: dict[str, list[str]] = defaultdict(list)
    for cid in component_ids:
        fp = component_file_map.get(cid, "")
        if fp:
            file_to_comps[fp].append(cid)

    directed_pairs: list[tuple[str, str]] = []
    for edge in edges:
        if not getattr(edge, "to_symbol", None):
            continue
        from_file = _extract_file_from_symbol(edge.from_symbol)
        to_file = _extract_file_from_symbol(edge.to_symbol)
        for fc in file_to_comps.get(from_file, []):
            for tc in file_to_comps.get(to_file, []):
                if fc != tc:
                    directed_pairs.append((fc, tc))

    super_graph, node_map = contract_sccs(graph, directed_pairs)

    # Map dir_partitions through node_map for community detection
    mapped_partitions: dict[str, set[str]] = {}
    for key, members in dir_partitions.items():
        mapped_partitions[key] = {node_map.get(m, m) for m in members}

    # Step 3: community detection
    communities = detect_communities(super_graph, mapped_partitions, seed=seed)

    if not communities:
        return [sorted(component_ids)]

    # Expand super-nodes back to original component_ids
    super_to_comps: dict[str, set[str]] = defaultdict(set)
    for cid, sup in node_map.items():
        super_to_comps[sup].add(cid)

    result: list[list[str]] = []
    for community in communities:
        expanded: set[str] = set()
        for super_node in community:
            expanded.update(super_to_comps.get(super_node, {super_node}))
        result.append(sorted(expanded))

    # Stable sort: largest first, then by first element
    result.sort(key=lambda c: (-len(c), c[0] if c else ""))

    return result
