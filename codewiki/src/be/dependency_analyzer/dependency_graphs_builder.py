from typing import Dict, List, Any
import os
from codewiki.src.codewiki_config import CodeWikiConfig
from codewiki.src.be.dependency_analyzer.ast_parser import DependencyParser
from codewiki.src.be.dependency_analyzer.topo_sort import (
    build_graph_from_components,
    get_leaf_nodes,
)
from codewiki.src.utils import file_manager

import logging

logger = logging.getLogger(__name__)


class DependencyGraphBuilder:
    """Handles dependency analysis and graph building."""

    def __init__(self, config: CodeWikiConfig):
        self.config = config

    def build_dependency_graph(self) -> tuple[Dict[str, Any], List[str]]:
        """
        Build and save dependency graph, returning components and leaf nodes.

        Returns:
            Tuple of (components, leaf_nodes)
        """
        # Ensure output directory exists
        file_manager.ensure_directory(self.config.dependency_graph_dir)

        # Prepare dependency graph path
        repo_name = os.path.basename(os.path.normpath(self.config.repo_path))
        sanitized_repo_name = "".join(c if c.isalnum() else "_" for c in repo_name)
        dependency_graph_path = os.path.join(
            self.config.dependency_graph_dir, f"{sanitized_repo_name}_dependency_graph.json"
        )
        filtered_folders_path = os.path.join(
            self.config.dependency_graph_dir, f"{sanitized_repo_name}_filtered_folders.json"
        )

        # Get custom include/exclude patterns from config
        include_patterns = self.config.include_patterns if self.config.include_patterns else None
        exclude_patterns = self.config.exclude_patterns if self.config.exclude_patterns else None

        parser = DependencyParser(
            self.config.repo_path,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
        )

        filtered_folders = None
        # if os.path.exists(filtered_folders_path):
        #     logger.debug(f"Loading filtered folders from {filtered_folders_path}")
        #     filtered_folders = file_manager.load_json(filtered_folders_path)
        # else:
        #     # Parse repository
        #     filtered_folders = parser.filter_folders()
        #     # Save filtered folders
        #     file_manager.save_json(filtered_folders, filtered_folders_path)

        # Parse repository
        components = parser.parse_repository(filtered_folders)
        comp_types = {}
        for c in components.values():
            comp_types[c.component_type] = comp_types.get(c.component_type, 0) + 1
        logger.debug(
            "Parsed %d components: %s",
            len(components),
            ", ".join(f"{t}={n}" for t, n in sorted(comp_types.items(), key=lambda x: -x[1])),
        )

        # Save dependency graph
        parser.save_dependency_graph(dependency_graph_path)

        # Build graph for traversal
        graph = build_graph_from_components(components)

        # Get leaf nodes
        leaf_nodes = get_leaf_nodes(graph, components)

        # Keep leaf nodes whose component type represents a meaningful code unit.
        # Primary types: class-like structures from all supported languages.
        # Secondary types (function/macro/table): only when no primary types exist
        # in the repo (e.g. pure-C, pure-Bash, pure-CMake, pure-TOML repos).
        PRIMARY_TYPES = {
            "class",  # Python, Java, C#, PHP, JavaScript, TypeScript
            "abstract class",  # PHP, Java
            "interface",  # Java, C#, TypeScript, Go, PHP
            "struct",  # C, C++, Go, Rust
            "enum",  # Rust, PHP, Java, C#, TypeScript
            "trait",  # Rust, PHP
            "type",  # Go (type aliases / named types)
            # HLS / compiled-language types (always meaningful leaf nodes)
            "hls_top",  # Vitis HLS top function (set_top / syn.top)
            "kernel_instance",  # Instantiated HLS kernel (nk= in connectivity)
        }
        SECONDARY_TYPES = {
            "function",  # Python, C, C++, Bash, CMake, Go
            "macro",  # CMake, Rust
            "table",  # TOML top-level tables
            "table_array",  # TOML arrays of tables
            "hls_project",  # Vitis HLS project container (open_project)
        }

        # Check types among actual leaf nodes (not all components).
        # Using all components would cause false-positives: e.g. an hls_top node
        # exists in components but is never a leaf (it depends on C++ functions),
        # so checking all components would incorrectly suppress including "function".
        available_leaf_types = {
            components[ln].component_type for ln in leaf_nodes if ln in components
        }

        valid_types = PRIMARY_TYPES.copy()
        # Fall back to secondary types when no OOP class-like primary types exist
        # among the actual leaf nodes (hls_top / kernel_instance don't count here
        # since HLS projects are function-based and need function leaves too).
        OOP_PRIMARY = PRIMARY_TYPES - {"hls_top", "kernel_instance"}
        if not available_leaf_types & OOP_PRIMARY:
            valid_types |= SECONDARY_TYPES

        keep_leaf_nodes = []
        for leaf_node in leaf_nodes:
            # Skip any leaf nodes that are clearly error strings or invalid identifiers
            if (
                not isinstance(leaf_node, str)
                or leaf_node.strip() == ""
                or any(
                    err_keyword in leaf_node.lower()
                    for err_keyword in ["error", "exception", "failed", "invalid"]
                )
            ):
                logger.warning(f"Skipping invalid leaf node identifier: '{leaf_node}'")
                continue

            if leaf_node in components:
                if components[leaf_node].component_type in valid_types:
                    keep_leaf_nodes.append(leaf_node)
                else:
                    # logger.debug(f"Leaf node {leaf_node} is a {components[leaf_node].component_type}, removing it")
                    pass
            else:
                logger.warning(f"Leaf node {leaf_node} not found in components, removing it")

        logger.debug(
            "GraphBuild complete: %d components, %d graph nodes, %d raw leaves → %d filtered leaves",
            len(components),
            len(graph),
            len(leaf_nodes),
            len(keep_leaf_nodes),
        )
        return components, keep_leaf_nodes
