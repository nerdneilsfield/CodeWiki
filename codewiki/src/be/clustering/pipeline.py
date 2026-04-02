"""Clustering v2 pipeline orchestrator."""

import logging
import os
import subprocess
from collections import Counter
from typing import Any, Dict, List, Optional

from codewiki.src.be.clustering.models import (
    ModuleNode,
    ModuleTree,
    ModuleMembers,
    ModuleConstraints,
    module_id_from_members,
    canonicalize_tree,
    validate_tree,
    to_legacy_dict,
    TreeValidationError,
)
from codewiki.src.be.clustering.graph_builder import extract_component_name
from codewiki.src.be.clustering.naming import name_clusters

logger = logging.getLogger(__name__)


_EXTRA_TOP_LEVEL_MODULES = [
    {"module_id": "getting-started", "title": "Getting Started", "path": "getting-started"},
    {"module_id": "tutorial", "title": "Tutorial", "path": "tutorial"},
    {"module_id": "best-practices", "title": "Best Practices", "path": "best-practices"},
]


def cluster_modules_v2(
    leaf_nodes: List[str],
    components: Dict[str, Any],  # Dict[str, Node]
    config: Any,
    index_products: Any,  # IndexProducts
    current_module_tree: Optional[dict] = None,
    current_module_name: Optional[str] = None,
    current_module_path: Optional[list] = None,
    _token_threshold: Optional[int] = None,
) -> Dict[str, Any]:
    """Clustering v2: graph-driven structure + heuristic naming.

    Drop-in replacement for cluster_modules() when index_products is available.
    Returns v1-compatible dict format.
    """
    if current_module_tree is None:
        current_module_tree = {}
    if current_module_path is None:
        current_module_path = []

    # Early exit: too few components to form meaningful clusters
    if len(leaf_nodes) < 4:
        return {}

    # Build component -> file map (normalise backslashes)
    component_file_map: dict[str, str] = {}
    for cid in leaf_nodes:
        node = components.get(cid)
        if node:
            component_file_map[cid] = getattr(node, "relative_path", "").replace("\\", "/")

    # Run partitioning pipeline
    from codewiki.src.be.clustering.partitioner import partition_components

    clusters = partition_components(
        component_ids=leaf_nodes,
        component_file_map=component_file_map,
        edges=index_products.edges if index_products else [],
        seed=42,
    )

    # Skip if only 1 cluster (no meaningful grouping)
    if len(clusters) <= 1:
        return {}

    # Name clusters
    names = name_clusters(clusters, component_file_map, config, components)

    # Naming freeze: if module_id matches previous tree, reuse old title/path/description
    frozen_names = _apply_naming_freeze(clusters, names, current_module_tree)

    # Build ModuleNode tree
    children: list[ModuleNode] = []
    used_paths: set[str] = set()

    for cluster, naming in zip(clusters, frozen_names):
        mid = module_id_from_members(cluster)
        title = naming["title"]
        # Use frozen path if available, otherwise compute fresh
        path = naming.get("frozen_path") or _compute_module_path(cluster, component_file_map)

        # Ensure path uniqueness by appending a counter suffix if needed
        unique_path = path
        counter = 2
        while unique_path in used_paths:
            unique_path = f"{path}_{counter}"
            counter += 1
        used_paths.add(unique_path)

        # Populate members.symbols from IndexProducts symbol table
        member_symbols: list[str] = []
        if index_products and hasattr(index_products, "symbol_table"):
            for cid in cluster:
                file_path = component_file_map.get(cid, "")
                comp_name = extract_component_name(cid)
                for sym in index_products.symbol_table.by_file(file_path):
                    if sym.name == comp_name or not comp_name:
                        member_symbols.append(sym.symbol_id)

        # Populate constraints from EdgeIndex
        public_api: list[str] = []
        boundary_edges_list: list[dict] = []
        if index_products and hasattr(index_products, "symbol_table"):
            for sid in member_symbols:
                sym = index_products.symbol_table.get(sid)
                if sym and sym.export_status.value == "exported":
                    public_api.append(sid)

        if index_products and hasattr(index_products, "edge_index"):
            cluster_syms = set(member_symbols)
            for sid in member_symbols:
                for edge in index_products.edge_index.callees_of(sid):
                    if edge.to_symbol and edge.to_symbol not in cluster_syms:
                        boundary_edges_list.append(
                            {
                                "from": edge.from_symbol,
                                "to": edge.to_symbol or edge.to_unresolved or "",
                                "type": edge.edge_type.value,
                            }
                        )
                        if len(boundary_edges_list) >= 10:
                            break
                if len(boundary_edges_list) >= 10:
                    break

        node = ModuleNode(
            module_id=mid,
            title=title,
            path=unique_path,
            description=naming.get("description", ""),
            members=ModuleMembers(
                components=sorted(cluster),
                symbols=sorted(set(member_symbols)),
                files=sorted({component_file_map.get(c, "") for c in cluster} - {""}),
            ),
            constraints=ModuleConstraints(
                public_api_symbols=sorted(public_api),
                boundary_edges=boundary_edges_list,
            ),
        )
        children.append(node)

    # Get commit hash for generated_from
    commit_hash = _get_commit_hash(index_products)

    root = ModuleNode(
        module_id="root",
        title="Repository",
        path="",
        children=children,
        extra_top_level_modules=_EXTRA_TOP_LEVEL_MODULES,
    )

    tree = ModuleTree(
        root=root,
        generated_from={
            "commit": commit_hash or "",
            "index_version": "2",
        },
    )
    tree = canonicalize_tree(tree)

    # Validate — raise on errors per v3.md L599
    errors = validate_tree(tree, set(leaf_nodes))
    if errors:
        raise TreeValidationError(errors)

    # Convert to legacy format and strip the root wrapper
    legacy = to_legacy_dict(tree)

    # to_legacy_dict wraps everything under root.title ("Repository").
    # We want the children level so that the output matches the v1 shape:
    # {module_title: {path, components, children}}.
    root_entry = legacy.get("Repository", {})
    result = root_entry.get("children", {})

    # Log stability metrics if a previous tree is available (non-blocking).
    if current_module_tree:
        try:
            from codewiki.src.be.clustering.stability import measure_tree_stability

            report = measure_tree_stability(current_module_tree, result)
            logger.info(f"Clustering stability: {report.summary()}")
        except Exception:
            pass

    return result


def _apply_naming_freeze(
    clusters: list[list[str]],
    names: list[dict],
    previous_tree: dict | None,
) -> list[dict]:
    """Apply naming freeze: reuse old title/path/description when module_id matches.

    If a cluster's module_id (computed from sorted members) appears in the
    previous tree, its naming is replaced with the previous values. This
    prevents unnecessary title/path churn when members haven't changed.

    Args:
        clusters: current cluster member lists
        names: current naming results (from LLM or heuristic)
        previous_tree: v1 legacy format from last run, or None/empty

    Returns:
        New names list with frozen entries where applicable.
    """
    if not previous_tree:
        return names

    # Build module_id → {title, path, description} from previous tree
    prev_by_id: dict[str, dict] = {}
    _index_previous_tree(previous_tree, prev_by_id)

    result = []
    frozen_count = 0
    for cluster, naming in zip(clusters, names):
        mid = module_id_from_members(cluster)
        if mid in prev_by_id:
            prev = prev_by_id[mid]
            result.append(
                {
                    "cluster_idx": naming.get("cluster_idx", 0),
                    "title": prev["title"],
                    "description": prev.get("description", naming.get("description", "")),
                    "frozen_path": prev.get("path", ""),
                }
            )
            frozen_count += 1
        else:
            result.append(naming)

    if frozen_count:
        logger.info(
            f"Naming freeze: reused {frozen_count}/{len(clusters)} module names from previous tree"
        )

    return result


def _index_previous_tree(tree: dict, index: dict) -> None:
    """Walk previous v1 tree, compute module_id for each module, store naming."""
    for title, info in tree.items():
        if not isinstance(info, dict):
            continue
        components = info.get("components", [])
        if components:
            mid = module_id_from_members(components)
            index[mid] = {
                "title": title,
                "path": info.get("path", ""),
                "description": "",  # v1 format doesn't store description
            }
        children = info.get("children", {})
        if children and isinstance(children, dict):
            _index_previous_tree(children, index)


def _compute_module_path(
    cluster_components: list[str],
    component_file_map: dict[str, str],
) -> str:
    """Compute the most common parent directory for the path field."""
    dirs = []
    for cid in cluster_components:
        path = component_file_map.get(cid, "")
        if "/" in path:
            dirs.append(os.path.dirname(path))

    if not dirs:
        return "modules"

    return Counter(dirs).most_common(1)[0][0]


def _get_commit_hash(index_products: Any) -> str | None:
    """Extract commit hash from index_products cache or git.

    Note: runs in current working directory. The DocumentationGenerator
    typically sets cwd to the repo path before running.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None
