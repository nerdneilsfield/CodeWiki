from typing import List, Dict, Any, Optional
from collections import defaultdict
import difflib
import logging
import traceback

from codewiki.src.be.dependency_analyzer.models.core import Node
from codewiki.src.be.llm_services import call_llm
from codewiki.src.be.utils import count_tokens
from codewiki.src.config import Config
from codewiki.src.be.prompt_template import format_cluster_prompt

logger = logging.getLogger(__name__)


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


def _resolve_leaf_node(
    leaf_node: str,
    components: Dict[str, Node],
    path_index: Dict[str, List[str]],
) -> List[str]:
    """
    Resolve a single leaf node string to a list of valid component IDs.

    Handles three cases:
    1. Exact match in components dict — returns [leaf_node].
    2. Close fuzzy match — returns the matched ID.
    3. The LLM returned a file path instead of a component ID — expands to
       all components belonging to that file.
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
        logger.debug(
            f"Resolved file path '{leaf_node}' to {len(file_components)} component(s)"
        )
        return file_components

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


def format_potential_core_components(leaf_nodes: List[str], components: Dict[str, Node]) -> tuple[str, str]:
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


def cluster_modules(
    leaf_nodes: List[str],
    components: Dict[str, Node],
    config: Config,
    current_module_tree: dict[str, Any] = {},
    current_module_name: str = None,
    current_module_path: List[str] = []
) -> Dict[str, Any]:
    """
    Cluster the potential core components into modules.
    """
    potential_core_components, potential_core_components_with_code = format_potential_core_components(leaf_nodes, components)

    token_count = count_tokens(potential_core_components_with_code)
    logger.info(
        f"Clustering check: {len(leaf_nodes)} leaf node(s), "
        f"{token_count} tokens (threshold: {config.max_token_per_module})"
    )
    if token_count <= config.max_token_per_module:
        logger.info(
            f"Skipping clustering — repository fits in a single context window "
            f"({token_count} ≤ {config.max_token_per_module} tokens)"
        )
        return {}

    prompt = format_cluster_prompt(potential_core_components, current_module_tree, current_module_name)
    response = call_llm(prompt, config, model=config.cluster_model)

    #parse the response
    try:
        if "<GROUPED_COMPONENTS>" not in response or "</GROUPED_COMPONENTS>" not in response:
            logger.error(f"Invalid LLM response format - missing component tags: {response[:200]}...")
            return {}
        
        response_content = response.split("<GROUPED_COMPONENTS>")[1].split("</GROUPED_COMPONENTS>")[0]
        module_tree = eval(response_content)
        
        if not isinstance(module_tree, dict):
            logger.error(f"Invalid module tree format - expected dict, got {type(module_tree)}")
            return {}
            
    except Exception as e:
        logger.error(f"Failed to parse LLM response: {e}. Response: {response[:200]}...")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return {}

    # check if the module tree is valid
    if len(module_tree) <= 1:
        logger.debug(f"Skipping clustering for {current_module_name} because the module tree is too small: {len(module_tree)} modules")
        return {}

    if current_module_tree == {}:
        current_module_tree = module_tree
    else:
        value = current_module_tree
        for key in current_module_path:
            value = value[key]["children"]
        for module_name, module_info in module_tree.items():
            del module_info["path"]
            value[module_name] = module_info

    path_index = _build_path_index(components)
    for module_name, module_info in module_tree.items():
        sub_leaf_nodes = module_info.get("components", [])

        valid_sub_leaf_nodes = _filter_and_resolve_nodes(sub_leaf_nodes, components, path_index)
        
        current_module_path.append(module_name)
        module_info["children"] = {}
        module_info["children"] = cluster_modules(valid_sub_leaf_nodes, components, config, current_module_tree, module_name, current_module_path)
        current_module_path.pop()

    return module_tree