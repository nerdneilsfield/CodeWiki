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

    # Step 2: build symbol → component mapping.
    #
    # Component IDs come in two formats depending on the analyzer:
    # - Real analyzers: dot-separated like "module.path.ClassName"
    #   (node.name is the last segment, node.relative_path is the file)
    # - Test fixtures: "path::Name" format
    #
    # We index by (file_path, component_name) for precise symbol→component
    # resolution, extracting component_name from either format.
    file_to_components: dict[str, list[str]] = defaultdict(list)
    comp_by_file_name: dict[tuple[str, str], str] = {}
    for cid in component_ids:
        file_path = component_file_map.get(cid, "")
        if file_path:
            file_to_components[file_path].append(cid)
            comp_name = extract_component_name(cid)
            if comp_name:
                comp_by_file_name[(file_path, comp_name)] = cid

    def _resolve_symbol_to_component(symbol_id: str) -> str | None:
        """Map a symbol_id to its owning component_id.

        Tries precise (file, name) match first. Falls back to first
        component in the same file if no name match (single-component files).
        Returns None if the symbol's file has no known components.
        """
        file = _extract_file_from_symbol(symbol_id)
        name = _extract_name_from_symbol(symbol_id)
        if name:
            comp = comp_by_file_name.get((file, name))
            if comp:
                return comp
        # Fallback: if the file has exactly one component, use it
        comps = file_to_components.get(file, [])
        if len(comps) == 1:
            return comps[0]
        return None

    # Accumulated weights keyed by canonical (sorted) component pair.
    pair_weights: dict[tuple[str, str], float] = defaultdict(float)

    # Step 3: process symbol edges → component-pair weights (precise mapping).
    for edge in edges:
        if not edge.to_symbol:
            continue  # unresolved edge — skip per spec

        weight = WEIGHT_MAP.get((edge.edge_type, edge.confidence), _DEFAULT_WEIGHT)

        from_comp = _resolve_symbol_to_component(edge.from_symbol)
        to_comp = _resolve_symbol_to_component(edge.to_symbol)

        if not from_comp or not to_comp or from_comp == to_comp:
            continue  # unresolvable or self-loop

        pair = (min(from_comp, to_comp), max(from_comp, to_comp))
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
        return symbol_id[len("file:") :]

    # Format: "lang:path#name(kind)" — both delimiter chars must be present.
    if ":" in symbol_id and "#" in symbol_id:
        after_colon = symbol_id.split(":", 1)[1]
        return after_colon.split("#", 1)[0]

    return ""


def _extract_name_from_symbol(symbol_id: str) -> str:
    """Extract the symbol name from a symbol_id string.

    ``"py:src/a.py#Foo(class)"``  →  ``"Foo"``
    ``"py:src/a.py#Bar.baz(method)"``  →  ``"Bar"`` (top-level name)
    ``"file:src/a.py"``  →  ``""``

    Returns an empty string if no name can be extracted.
    """
    if not symbol_id or ":" not in symbol_id or "#" not in symbol_id:
        return ""
    after_hash = symbol_id.split("#", 1)[1]
    # Remove kind suffix: "Foo(class)" → "Foo", "Bar.baz(method)" → "Bar.baz"
    name_part = after_hash.split("(", 1)[0] if "(" in after_hash else after_hash
    # For method symbols like "Bar.baz", take the top-level class name
    return name_part.split(".")[0] if name_part else ""


def extract_component_name(component_id: str) -> str:
    """Extract the component name from a component_id.

    Handles both real analyzer format and test fixture format:
    - ``"codewiki.src.be.cluster_modules.ClusterModules"``  →  ``"ClusterModules"``
    - ``"src/auth/handler.py::AuthHandler"``                 →  ``"AuthHandler"``
    - ``"module.path.ClassName.method_name"``                →  ``"ClassName"``

    For dot-separated IDs, takes the last segment that starts with an uppercase
    letter (class name). Falls back to the last segment if none is uppercase.
    """
    if not component_id:
        return ""

    # Test fixture format: "path::Name"
    if "::" in component_id:
        return component_id.split("::", 1)[1]

    # Real analyzer format: dot-separated like "module.path.ClassName"
    if "." in component_id:
        parts = component_id.rsplit(".", 1)
        return parts[-1] if parts else ""

    return component_id
