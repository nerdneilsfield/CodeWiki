"""Build weighted NetworkX graph from IndexProducts for clustering.

Aligned with v3.md section 5.1 edge weight strategy.
"""
import networkx as nx
from collections import defaultdict

from codewiki.src.be.index.models import EdgeType, Confidence, SymbolEdge


# ---------------------------------------------------------------------------
# Weight policy — v3.md section 5.1 L472
# ---------------------------------------------------------------------------

# IMPORTS and EXTENDS are structural edges: weight 1.0 for HIGH/MEDIUM, 0.7 for LOW.
# CALLS are behavioural edges: tiered by confidence.
WEIGHT_MAP: dict[tuple[EdgeType, Confidence], float] = {
    (EdgeType.IMPORTS, Confidence.HIGH): 1.0,
    (EdgeType.IMPORTS, Confidence.MEDIUM): 1.0,
    (EdgeType.IMPORTS, Confidence.LOW): 0.7,
    (EdgeType.EXTENDS, Confidence.HIGH): 1.0,
    (EdgeType.EXTENDS, Confidence.MEDIUM): 1.0,
    (EdgeType.EXTENDS, Confidence.LOW): 0.7,
    (EdgeType.CALLS, Confidence.HIGH): 0.5,
    (EdgeType.CALLS, Confidence.MEDIUM): 0.3,
    (EdgeType.CALLS, Confidence.LOW): 0.2,
}

# Components that share a file have a structural coupling bonus.
CO_LOCATION_WEIGHT: float = 0.3

# Accumulated edge weight upper bound — prevents a single pair from dominating.
MAX_EDGE_WEIGHT: float = 3.0

# Fallback weight for edge-type/confidence combos not in WEIGHT_MAP.
_DEFAULT_WEIGHT: float = 0.2


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_clustering_graph(
    edges: list[SymbolEdge],
    component_ids: set[str],
    component_file_map: dict[str, str],  # component_id → relative_path
) -> nx.Graph:
    """Build an undirected weighted graph over component_ids from index edges.

    Algorithm:
    1. Add all component_ids as nodes (preserves isolated components).
    2. Build a file → [component_id] inverted index from component_file_map.
    3. For each resolved SymbolEdge, map (from_file, to_file) → component pairs
       and accumulate the typed weight.
    4. Add co-location edges (weight CO_LOCATION_WEIGHT) for components that
       share the same source file.
    5. Write all accumulated pair weights to the graph, capped at MAX_EDGE_WEIGHT.

    Args:
        edges: All SymbolEdge objects (typically from IndexProducts.edges).
        component_ids: Set of component IDs to include as nodes.  Components
            not referenced by any edge are still added as isolated nodes.
        component_file_map: Maps component_id → relative_path of its source
            file.  Used both for symbol-to-component resolution and co-location
            edge generation.

    Returns:
        nx.Graph with component_ids as nodes and weighted edges.  The graph is
        undirected; ``weight`` is stored on every edge attribute dict.
    """
    graph: nx.Graph = nx.Graph()

    # Step 1: add every component as a node (sorted for determinism).
    for cid in sorted(component_ids):
        graph.add_node(cid)

    # Step 2: build file → components index.
    file_to_components: dict[str, list[str]] = defaultdict(list)
    for cid in component_ids:
        file_path = component_file_map.get(cid, "")
        if file_path:
            file_to_components[file_path].append(cid)

    # Accumulated weights keyed by canonical (sorted) component pair.
    pair_weights: dict[tuple[str, str], float] = defaultdict(float)

    # Step 3: process symbol edges → component-pair weights.
    for edge in edges:
        if not edge.to_symbol:
            continue  # unresolved edge — skip per spec

        weight = WEIGHT_MAP.get((edge.edge_type, edge.confidence), _DEFAULT_WEIGHT)

        from_file = _extract_file_from_symbol(edge.from_symbol)
        to_file = _extract_file_from_symbol(edge.to_symbol)

        from_comps = file_to_components.get(from_file, [])
        to_comps = file_to_components.get(to_file, [])

        # Cross product of components in source file × target file.
        # Use a per-edge seen set to avoid double-counting when from_file == to_file
        # (i.e. two symbols in the same file: both orderings collapse to the same pair).
        seen_pairs_this_edge: set[tuple[str, str]] = set()
        for fc in from_comps:
            for tc in to_comps:
                if fc == tc:
                    continue  # no self-loops
                pair = (min(fc, tc), max(fc, tc))
                if pair in seen_pairs_this_edge:
                    continue  # already counted for this edge
                seen_pairs_this_edge.add(pair)
                pair_weights[pair] += weight

    # Step 4: co-location edges — components in the same file are structurally coupled.
    for comps in file_to_components.values():
        for i in range(len(comps)):
            for j in range(i + 1, len(comps)):
                pair = (min(comps[i], comps[j]), max(comps[i], comps[j]))
                pair_weights[pair] += CO_LOCATION_WEIGHT

    # Step 5: write edges to graph with cap.
    for (u, v), w in pair_weights.items():
        graph.add_edge(u, v, weight=min(w, MAX_EDGE_WEIGHT))

    return graph


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_file_from_symbol(symbol_id: str) -> str:
    """Extract the relative file path from a symbol_id string.

    Supported formats:
    - ``"py:src/auth/login.py#LoginService(class)"``  →  ``"src/auth/login.py"``
    - ``"ts:src/app.ts#AppService(class)"``           →  ``"src/app.ts"``
    - ``"file:src/main.py"``                          →  ``"src/main.py"``

    Returns an empty string for unrecognised formats.
    """
    if not symbol_id:
        return ""

    if symbol_id.startswith("file:"):
        return symbol_id[len("file:"):]

    # Format: "lang:path#name(kind)" — both delimiter chars must be present.
    if ":" in symbol_id and "#" in symbol_id:
        after_colon = symbol_id.split(":", 1)[1]
        return after_colon.split("#", 1)[0]

    return ""
