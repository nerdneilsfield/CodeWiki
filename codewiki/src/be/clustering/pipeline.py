"""Clustering v2 pipeline orchestrator."""
import logging
import os
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
)
from codewiki.src.be.clustering.naming import name_clusters

logger = logging.getLogger(__name__)


def cluster_modules_v2(
    leaf_nodes: List[str],
    components: Dict[str, Any],  # Dict[str, Node]
    config: Any,
    index_products: Any,  # IndexProducts
    current_module_tree: dict = None,
    current_module_name: str = None,
    current_module_path: list = None,
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
    names = name_clusters(clusters, component_file_map, config)

    # Build ModuleNode tree
    children: list[ModuleNode] = []
    used_paths: set[str] = set()

    for cluster, naming in zip(clusters, names):
        mid = module_id_from_members(cluster)
        title = naming["title"]
        path = _compute_module_path(cluster, component_file_map)

        # Ensure path uniqueness by appending a counter suffix if needed
        unique_path = path
        counter = 2
        while unique_path in used_paths:
            unique_path = f"{path}_{counter}"
            counter += 1
        used_paths.add(unique_path)

        node = ModuleNode(
            module_id=mid,
            title=title,
            path=unique_path,
            description=naming.get("description", ""),
            members=ModuleMembers(
                components=sorted(cluster),
                files=sorted(
                    {component_file_map.get(c, "") for c in cluster} - {""}
                ),
            ),
        )
        children.append(node)

    root = ModuleNode(
        module_id="root",
        title="Repository",
        path="",
        children=children,
    )

    tree = ModuleTree(root=root)
    tree = canonicalize_tree(tree)

    # Validate (log warnings but never block the pipeline)
    errors = validate_tree(tree, set(leaf_nodes))
    if errors:
        for err in errors:
            logger.warning("Tree validation: %s", err)

    # Convert to legacy format and strip the root wrapper
    legacy = to_legacy_dict(tree)

    # to_legacy_dict wraps everything under root.title ("Repository").
    # We want the children level so that the output matches the v1 shape:
    # {module_title: {path, components, children}}.
    root_entry = legacy.get("Repository", {})
    return root_entry.get("children", {})


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
