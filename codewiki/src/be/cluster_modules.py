from typing import List, Dict, Any, Optional, Tuple, cast
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import PurePosixPath
import difflib
import json
import logging
import traceback

import networkx as nx
from networkx.algorithms.community import louvain_communities

from codewiki.src.be.dependency_analyzer.models.core import Node
from codewiki.src.be.llm_services import call_llm
from codewiki.src.be.llm_retry import with_retry_sync
from codewiki.src.be.llm_usage import LLMUsageStats
from codewiki.src.be.utils import count_tokens
from codewiki.src.codewiki_config import CodeWikiConfig
from codewiki.src.be.prompt_template import format_cluster_prompt

logger = logging.getLogger(__name__)

# Minimum number of distinct files a module must span before sub-clustering
# is attempted.  Modules with fewer files are unlikely to benefit from an
# extra LLM call and are skipped directly.
_MIN_FILES_FOR_SUB_CLUSTER = 4


def _fuzzy_match_component(name: str, components: Dict) -> Optional[str]:
    """Try to find a close match for a component name that wasn't found exactly."""
    matches = difflib.get_close_matches(name, components.keys(), n=1, cutoff=0.85)
    return matches[0] if matches else None


def _build_path_index(components: Dict[str, Node]) -> Dict[str, List[str]]:
    """Build a reverse index from relative_path to component IDs."""
    index: Dict[str, List[str]] = defaultdict(list)
    for comp_id, node in components.items():
        norm_path = node.relative_path.replace("\\", "/")
        index[norm_path].append(comp_id)
    return dict(index)


def _match_by_name_or_stem(
    name: str,
    components: Dict[str, Node],
) -> List[str]:
    """Match a short name against component name parts (after ::) or file stems.

    LLMs often return abbreviated names like ``orchestrator`` instead of the
    full component ID ``src/agents/orchestrator.py::Orchestrator``.  This
    helper catches those cases.
    """
    lower = name.lower()
    # 1. Exact match on the component-name portion (after '::')
    by_name = [cid for cid in components if "::" in cid and cid.split("::")[-1].lower() == lower]
    if by_name:
        return by_name

    # 2. Exact match on file stem (e.g. 'orchestrator' → orchestrator.py)
    by_stem = [
        cid
        for cid, node in components.items()
        if PurePosixPath(node.relative_path).stem.lower() == lower
    ]
    if by_stem:
        return by_stem

    # 3. Substring: name appears as a whole word in the component ID
    by_substr = [
        cid
        for cid in components
        if lower in cid.lower().replace("_", " ").replace("-", " ").split()
    ]
    return by_substr


def _resolve_leaf_node(
    leaf_node: str,
    components: Dict[str, Node],
    path_index: Dict[str, List[str]],
) -> List[str]:
    """
    Resolve a single leaf node string to a list of valid component IDs.

    Handles these cases (in order):
    1. Exact match in components dict.
    2. Close fuzzy match (difflib, cutoff 0.85).
    3. LLM returned a file path — expand to all components in that file.
    4. Match by component name part (after ::) or file stem.
    Returns an empty list when the node cannot be resolved at all.
    """
    if leaf_node in components:
        return [leaf_node]

    match = _fuzzy_match_component(leaf_node, components)
    if match:
        logger.debug(f"Fuzzy-corrected leaf node '{leaf_node}' → '{match}'")
        return [match]

    # LLM returned a file path — expand to all components in that file
    normalized = leaf_node.replace("\\", "/")
    file_components = path_index.get(normalized, [])
    if file_components:
        logger.debug(f"Resolved file path '{leaf_node}' to {len(file_components)} component(s)")
        return file_components

    # LLM returned a short name — try component name / file stem matching
    name_matches = _match_by_name_or_stem(leaf_node, components)
    if name_matches:
        logger.debug(
            f"Resolved short name '{leaf_node}' to {len(name_matches)} component(s) "
            f"by name/stem match"
        )
        return name_matches

    logger.warning(f"Skipping invalid leaf node '{leaf_node}' - not found in components")
    return []


def _filter_and_resolve_nodes(
    leaf_nodes: List[str],
    components: Dict[str, Node],
    path_index: Dict[str, List[str]],
) -> List[str]:
    """Resolve and deduplicate a list of raw leaf node strings."""
    seen: set = set()
    result: List[str] = []
    for raw in leaf_nodes:
        for resolved in _resolve_leaf_node(raw, components, path_index):
            if resolved not in seen:
                seen.add(resolved)
                result.append(resolved)
    return result


def format_potential_core_components(
    leaf_nodes: List[str], components: Dict[str, Node]
) -> tuple[str, str]:
    """
    Format the potential core components into a string that can be used in the prompt.

    The output uses an explicit ``File: / Component:`` format so LLMs are less
    likely to confuse file paths with component identifiers.
    """
    path_index = _build_path_index(components)
    valid_leaf_nodes = _filter_and_resolve_nodes(leaf_nodes, components, path_index)

    # Group by file
    leaf_nodes_by_file: Dict[str, List[str]] = defaultdict(list)
    for leaf_node in valid_leaf_nodes:
        leaf_nodes_by_file[components[leaf_node].relative_path].append(leaf_node)

    potential_core_components = ""
    potential_core_components_with_code = ""
    for file, nodes_in_file in dict(sorted(leaf_nodes_by_file.items())).items():
        # Use a distinct prefix so LLMs don't confuse the file path with a component name
        potential_core_components += f"File: {file}\n"
        potential_core_components_with_code += f"# {file}\n"
        for leaf_node in nodes_in_file:
            potential_core_components += f"  Component: {leaf_node}\n"
            potential_core_components_with_code += f"\t{leaf_node}\n"
            potential_core_components_with_code += f"{components[leaf_node].source_code}\n"

    return potential_core_components, potential_core_components_with_code


def heal_module_tree_components(
    module_tree: Dict[str, Any],
    components: Dict[str, Node],
) -> Dict[str, Any]:
    """
    Walk a saved module tree and resolve any file-path strings stored in
    ``components`` lists back to actual component IDs.

    This repairs trees produced by an earlier run where the clustering LLM
    returned file paths instead of component IDs.  The tree is modified
    in-place and also returned for convenience.
    """
    path_index = _build_path_index(components)

    def _heal(subtree: Dict[str, Any]) -> None:
        for module_info in subtree.values():
            raw_components = module_info.get("components", [])
            if raw_components:
                module_info["components"] = _filter_and_resolve_nodes(
                    raw_components, components, path_index
                )
            children = module_info.get("children")
            if isinstance(children, dict) and children:
                _heal(children)

    _heal(module_tree)
    return module_tree


def _heuristic_cluster_name(
    members: List[str],
    components: Dict[str, Node],
) -> str:
    """Generate a human-readable cluster name from directory structure.

    Uses the most common non-generic directory segment among the cluster's
    members, similar to GitNexus's heuristic labeling.
    """
    _GENERIC = {
        "src",
        "lib",
        "core",
        "utils",
        "common",
        "shared",
        "helpers",
        "internal",
        "pkg",
        "app",
        "main",
        "index",
        "mod",
    }
    dir_counts: Dict[str, int] = defaultdict(int)
    for cid in members:
        if cid not in components:
            continue
        parts = PurePosixPath(components[cid].relative_path).parts
        for part in parts[:-1]:  # skip the filename
            if part.lower() not in _GENERIC:
                dir_counts[part] += 1

    if dir_counts:
        best = max(dir_counts.items(), key=lambda item: item[1])[0]
        return best.replace("-", "_").replace(" ", "_")
    # Fallback: use the common file stem
    stems: Dict[str, int] = defaultdict(int)
    for cid in members:
        if cid not in components:
            continue
        stems[PurePosixPath(components[cid].relative_path).stem] += 1
    if stems:
        return max(stems.items(), key=lambda item: item[1])[0]
    return "cluster"


def graph_pre_cluster(
    leaf_nodes: List[str],
    components: Dict[str, Node],
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """Use Louvain community detection to pre-cluster components by dependency structure.

    Builds an undirected graph with two kinds of edges:
    - **dependency edges** (weight 1.0): from ``Node.depends_on``
    - **co-location edges** (weight 0.3): components sharing the same source file

    Returns:
        clusters: ``{cluster_name: [component_id, ...]}``
        cross_edges: ``{cluster_name: [other_cluster_name, ...]}`` — inter-cluster
            dependency edges (for the LLM prompt).
    """
    node_set = set(leaf_nodes) & set(components.keys())
    if len(node_set) < 3:
        return {}, {}

    G = nx.Graph()
    for cid in node_set:
        G.add_node(cid)

    # Dependency edges (bidirectional in an undirected graph)
    for cid in node_set:
        for dep in components[cid].depends_on:
            if dep in node_set:
                if G.has_edge(cid, dep):
                    G[cid][dep]["weight"] = max(G[cid][dep]["weight"], 1.0)
                else:
                    G.add_edge(cid, dep, weight=1.0)

    # Co-location edges (same file = likely related)
    by_file: Dict[str, List[str]] = defaultdict(list)
    for cid in node_set:
        by_file[components[cid].relative_path].append(cid)
    for file_nodes in by_file.values():
        for i, a in enumerate(file_nodes):
            for b in file_nodes[i + 1 :]:
                if not G.has_edge(a, b):
                    G.add_edge(a, b, weight=0.3)

    # Run Louvain community detection; cap to MAX_CLUSTERS afterwards.
    # Resolution 1.0 produces coarser communities than the original 2.0,
    # reducing the initial count before the cap step.
    # Scale cap with repo size: ~1 cluster per 50 nodes, clamped [12, 32].
    MAX_CLUSTERS = max(12, min(32, len(node_set) // 50))
    try:
        communities = cast(
            list[set[str]], louvain_communities(G, weight="weight", resolution=1.0, seed=42)
        )
    except Exception as e:
        logger.warning(f"Louvain community detection failed: {e}")
        return {}, {}

    if len(communities) <= 1:
        return {}, {}

    # Sort: largest first
    communities = cast(list[set[str]], sorted(communities, key=len, reverse=True))

    def _merge_smallest(comms: list[set[str]]) -> list[set[str]]:
        """Merge the smallest community into its most-connected neighbour."""
        comms = cast(list[set[str]], sorted(comms, key=len))  # smallest first
        smallest = comms[0]
        rest = comms[1:]
        best, best_score = 0, -1
        for idx, other in enumerate(rest):
            score = sum(1 for n in smallest for nbr in G.neighbors(n) if nbr in other)
            if score > best_score:
                best, best_score = idx, score
        rest[best] = rest[best] | smallest
        return cast(list[set[str]], sorted(rest, key=len, reverse=True))

    # Phase 1: merge tiny (< 3 members) communities
    MIN_CLUSTER_SIZE = 3
    n_before = len(communities)
    large = [c for c in communities if len(c) >= MIN_CLUSTER_SIZE]
    small = [c for c in communities if len(c) < MIN_CLUSTER_SIZE]
    if large and small:
        for tiny in small:
            best, best_score = 0, -1
            for idx, big in enumerate(large):
                score = sum(1 for n in tiny for nbr in G.neighbors(n) if nbr in big)
                if score > best_score:
                    best, best_score = idx, score
            large[best] = large[best] | tiny
        communities = cast(list[set[str]], sorted(large, key=len, reverse=True))

    # Phase 2: enforce MAX_CLUSTERS cap — keep merging smallest until at cap
    while len(communities) > MAX_CLUSTERS:
        communities = _merge_smallest(communities)

    if len(communities) != n_before:
        logger.debug(
            f"Merged {n_before} → {len(communities)} communities "
            f"(tiny merge + {MAX_CLUSTERS}-cluster cap)"
        )

    # Build cluster dict with heuristic names
    clusters: Dict[str, List[str]] = {}
    node_to_cluster: Dict[str, str] = {}
    used_names: set[str] = set()
    for community in communities:
        members = list(community)
        name = _heuristic_cluster_name(members, components)
        # Deduplicate names
        original = name
        counter = 2
        while name in used_names:
            name = f"{original}_{counter}"
            counter += 1
        used_names.add(name)
        clusters[name] = members
        for cid in members:
            node_to_cluster[cid] = name

    # Compute inter-cluster dependency edges
    cross_edges: Dict[str, set] = defaultdict(set)
    for cid in node_set:
        src_cluster = node_to_cluster.get(cid)
        if not src_cluster:
            continue
        for dep in components[cid].depends_on:
            dst_cluster = node_to_cluster.get(dep)
            if dst_cluster and dst_cluster != src_cluster:
                cross_edges[src_cluster].add(dst_cluster)

    logger.debug(
        f"Graph pre-clustering: {len(node_set)} nodes, {G.number_of_edges()} edges "
        f"→ {len(clusters)} communities"
    )

    return clusters, {k: sorted(v) for k, v in cross_edges.items()}


def _format_graph_clusters_hint(
    clusters: Dict[str, List[str]],
    cross_edges: Dict[str, List[str]],
    components: Dict[str, Node],
) -> str:
    """Format graph-based clusters as a hint section for the LLM clustering prompt."""
    lines = []
    for name, members in clusters.items():
        # Show the cluster with its members and dependency summary
        file_set = sorted({components[cid].relative_path for cid in members if cid in components})
        lines.append(
            f'  Cluster "{name}" ({len(members)} components across {len(file_set)} file(s)):'
        )
        for cid in members[:15]:
            ctype = components[cid].component_type if cid in components else "?"
            lines.append(f"    - {cid} ({ctype})")
        if len(members) > 15:
            lines.append(f"    ... and {len(members) - 15} more")
        deps = cross_edges.get(name, [])
        if deps:
            lines.append(f"    depends on → {', '.join(deps)}")

    return "\n".join(lines)


def cluster_modules(
    leaf_nodes: List[str],
    components: Dict[str, Node],
    config: CodeWikiConfig,
    current_module_tree: Optional[dict[str, Any]] = None,
    current_module_name: Optional[str] = None,
    current_module_path: Optional[List[str]] = None,
    _token_threshold: Optional[int] = None,
    index_products=None,  # NEW: when provided, use v2 pipeline
    usage_stats: LLMUsageStats | None = None,
) -> Dict[str, Any]:
    """
    Cluster the potential core components into modules.

    Uses a hybrid approach:
    1. **Graph pre-clustering** (Louvain community detection) groups components
       by actual dependency and co-location structure.
    2. **LLM refinement** takes the graph clusters as hints and produces
       semantically named modules, optionally merging or splitting clusters.
    3. **Fallback**: if the LLM fails, the graph clusters are used directly.

    ``_token_threshold`` is set only on recursive calls (not None) to
    signal that sub-module splitting should use file-count logic instead of
    token-count logic.  The top-level call skips clustering when the whole
    repo fits in one context window (token-based).  Recursive calls skip
    only single-file modules — mirroring ``is_complex_module`` which the
    documentation agents use to decide whether to call
    ``generate_sub_module_documentation``.
    """
    if current_module_tree is None:
        current_module_tree = {}
    if current_module_path is None:
        current_module_path = []

    # V2 dispatch: when index_products is provided, attempt graph-driven clustering
    if index_products is not None:
        try:
            from codewiki.src.be.clustering.pipeline import cluster_modules_v2

            result = cluster_modules_v2(
                leaf_nodes,
                components,
                config,
                index_products,
                current_module_tree,
                current_module_name,
                current_module_path,
                _token_threshold,
                usage_stats=usage_stats,
            )
            if result:  # v2 produced valid output
                return result
            # Fall through to v1 if v2 returns empty
        except Exception as e:
            logger.warning("Clustering v2 failed, falling back to v1: %s", e)

    is_recursive_call = _token_threshold is not None

    if is_recursive_call:
        # Mirror is_complex_module: only attempt sub-clustering if components
        # span more than one file.  Token size doesn't matter here — the agent
        # will sub-cluster any multi-file module regardless of token count.
        unique_files = {components[cid].relative_path for cid in leaf_nodes if cid in components}
        n_files = len(unique_files)
        logger.debug(
            f"Sub-clustering '{current_module_name}' "
            f"({len(leaf_nodes)} components, {n_files} file(s))"
        )
        if n_files < _MIN_FILES_FOR_SUB_CLUSTER:
            logger.debug(
                f"  → skipped (only {n_files} file(s), need ≥ {_MIN_FILES_FOR_SUB_CLUSTER})"
            )
            return {}
    else:
        # Top-level call: skip only if the entire repo fits in one context window
        token_threshold = config.max_token_per_module
        _, _code_for_count = format_potential_core_components(leaf_nodes, components)
        token_count = count_tokens(_code_for_count)
        logger.debug(
            f"Clustering check: {len(leaf_nodes)} leaf node(s), "
            f"{token_count} tokens (threshold: {token_threshold})"
        )
        if token_count <= token_threshold:
            logger.debug(
                f"Skipping clustering — fits in a single context window "
                f"({token_count} ≤ {token_threshold} tokens)"
            )
            return {}

    potential_core_components, potential_core_components_with_code = (
        format_potential_core_components(leaf_nodes, components)
    )

    # ── Step 1: Graph-based pre-clustering ────────────────────────────────
    graph_clusters, cross_edges = graph_pre_cluster(leaf_nodes, components)
    graph_hint = ""
    if graph_clusters:
        graph_hint = _format_graph_clusters_hint(graph_clusters, cross_edges, components)
        logger.debug(
            f"Graph pre-clustering produced {len(graph_clusters)} clusters: "
            f"{', '.join(f'{k}({len(v)})' for k, v in graph_clusters.items())}"
        )

    # ── Step 2: LLM refinement ────────────────────────────────────────────
    prompt = format_cluster_prompt(
        potential_core_components,
        current_module_tree,
        current_module_name,
        graph_clusters_hint=graph_hint,
    )
    response = with_retry_sync(call_llm, prompt, config, model=config.cluster_model, max_retries=1)
    if usage_stats and response.usage:
        usage_stats.record(
            response.model,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

    module_tree = None

    def _strip_code_fence(text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if len(lines) >= 2 and lines[-1].strip().startswith("```"):
                return "\n".join(lines[1:-1]).strip()
        return stripped

    def _try_parse(content: str) -> Optional[Dict[str, Any]]:
        try:
            payload = _strip_code_fence(content)
            parsed = json.loads(payload)
            if not isinstance(parsed, dict):
                logger.error(f"Invalid module tree format - expected dict, got {type(parsed)}")
                return None
            return parsed
        except Exception as e:
            logger.error(
                f"Failed to parse LLM response: {e}. Response: {response.content[:200]}..."
            )
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None

    if "<GROUPED_COMPONENTS>" in response.content and "</GROUPED_COMPONENTS>" in response.content:
        response_content = response.content.split("<GROUPED_COMPONENTS>")[1].split(
            "</GROUPED_COMPONENTS>"
        )[0]
        module_tree = _try_parse(response_content)
    else:
        # Accept bare JSON/dict responses as a fallback
        logger.warning(
            "LLM response missing <GROUPED_COMPONENTS> tags — attempting to parse raw response"
        )
        module_tree = _try_parse(response.content)

    # ── Step 3: Fallback to graph clusters if LLM failed ──────────────────
    if not module_tree and graph_clusters:
        logger.warning("LLM clustering failed — falling back to graph-based clusters")
        path_index = _build_path_index(components)
        module_tree = {}
        for name, members in graph_clusters.items():
            valid = _filter_and_resolve_nodes(members, components, path_index)
            if valid:
                # Derive a representative path from the most common directory
                paths = [components[cid].relative_path for cid in valid if cid in components]
                rep_path = PurePosixPath(paths[0]).parent.as_posix() if paths else ""
                module_tree[name] = {"path": rep_path, "components": valid}

    if not module_tree:
        return {}

    def _compute_rep_path(component_ids: List[str]) -> str:
        counts: Dict[str, int] = defaultdict(int)
        for cid in component_ids:
            node = components.get(cid)
            if not node:
                continue
            parent = PurePosixPath(node.relative_path).parent.as_posix()
            counts[parent] += 1
        if not counts:
            return ""
        return max(counts.items(), key=lambda item: item[1])[0]

    def _ensure_paths(tree: Dict[str, Any]) -> None:
        for info in tree.values():
            if not info.get("path"):
                info["path"] = _compute_rep_path(info.get("components", []))

    _ensure_paths(module_tree)

    # check if the module tree is valid
    if len(module_tree) <= 1:
        logger.debug(
            f"Skipping clustering for {current_module_name} because the module tree is too small: {len(module_tree)} modules"
        )
        return {}

    # Log what the LLM produced
    context = f"'{current_module_name}'" if current_module_name else "top-level"
    logger.info(
        f"LLM produced {len(module_tree)} modules for {context}: "
        + ", ".join(
            f"{name}({len(info.get('components', []))})" for name, info in module_tree.items()
        )
    )

    if current_module_tree == {}:
        current_module_tree = module_tree
    else:
        value = current_module_tree
        for key in current_module_path:
            value = value[key].setdefault("children", {})
        for module_name, module_info in module_tree.items():
            value[module_name] = module_info

    path_index = _build_path_index(components)

    def _sub_cluster(module_name: str, module_info: Dict, parent_path: List[str]) -> Dict:
        sub_leaf_nodes = module_info.get("components", [])
        valid_sub_leaf_nodes = _filter_and_resolve_nodes(sub_leaf_nodes, components, path_index)
        return cluster_modules(
            valid_sub_leaf_nodes,
            components,
            config,
            current_module_tree,
            module_name,
            parent_path,
            _token_threshold=config.max_token_per_leaf_module,
            usage_stats=usage_stats,
        )

    max_workers = max(1, config.max_concurrent)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_name = {
            executor.submit(
                _sub_cluster,
                module_name,
                module_info,
                current_module_path + [module_name],
            ): module_name
            for module_name, module_info in module_tree.items()
        }
        for future in as_completed(future_to_name):
            module_name = future_to_name[future]
            children = future.result()
            module_tree[module_name]["children"] = children
            n_children = len(children)
            if n_children:
                logger.debug(
                    f"  → '{module_name}' split into {n_children} sub-modules: "
                    + ", ".join(children.keys())
                )

    # At the top level, print a summary tree
    if not is_recursive_call:
        _log_tree_summary(module_tree)

    return module_tree


def _log_tree_summary(module_tree: Dict[str, Any], indent: int = 0) -> None:
    """Recursively log the clustered module tree for visibility."""
    prefix = "  " * indent
    for name, info in module_tree.items():
        children = info.get("children") or {}
        n_comp = len(info.get("components", []))
        suffix = f"  [{n_comp} components]"
        if children:
            logger.debug(f"{prefix}├── {name}{suffix}")
            _log_tree_summary(children, indent + 1)
        else:
            logger.debug(f"{prefix}└── {name}{suffix}  [leaf]")
