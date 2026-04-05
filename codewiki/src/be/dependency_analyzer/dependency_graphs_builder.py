from typing import Dict, List, Any
import json
import os
import subprocess
from codewiki.src.codewiki_config import CodeWikiConfig
from codewiki.src.be.dependency_analyzer.ast_parser import DependencyParser
from codewiki.src.be.dependency_analyzer.models.core import Node
from codewiki.src.be.dependency_analyzer.topo_sort import (
    build_graph_from_components,
    get_leaf_nodes,
)
from codewiki.src.utils import file_manager

import logging

logger = logging.getLogger(__name__)

_GRAPH_CACHE_VERSION = "v1"


class DependencyGraphBuilder:
    """Handles dependency analysis and graph building."""

    def __init__(self, config: CodeWikiConfig, commit_id: str = ""):
        self.config = config
        self.commit_id = commit_id

    def _cache_path(self) -> str:
        return os.path.join(self.config.dependency_graph_dir, "_graph_cache.json")

    def _get_commit_hash(self) -> str:
        if self.commit_id:
            return self.commit_id
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.config.repo_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

    def _try_load_cache(self) -> tuple[Dict[str, Any], List[str]] | None:
        cache_path = self._cache_path()
        if not os.path.exists(cache_path):
            return None
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            key = data.get("_cache_key", {})
            if (
                key.get("commit") == self._get_commit_hash()
                and key.get("version") == _GRAPH_CACHE_VERSION
                and sorted(key.get("include", [])) == sorted(self.config.include_patterns or [])
                and sorted(key.get("exclude", [])) == sorted(self.config.exclude_patterns or [])
            ):
                repo_path = os.path.abspath(self.config.repo_path)
                components = {}
                for k, v in data["components"].items():
                    # Rebuild absolute file_path from relative_path + current repo_path
                    if v.get("relative_path") and not os.path.isabs(v.get("file_path", "")):
                        v["file_path"] = os.path.join(repo_path, v["relative_path"])
                    components[k] = Node.model_validate(v)
                leaf_nodes = data["leaf_nodes"]
                logger.info(
                    "Graph cache hit — %d components, %d leaves",
                    len(components),
                    len(leaf_nodes),
                )
                return components, leaf_nodes
        except Exception as e:
            logger.debug("Graph cache load failed: %s", e)
        return None

    def _save_cache(self, components: Dict[str, Any], leaf_nodes: List[str]) -> None:
        try:
            data = {
                "_cache_key": {
                    "commit": self._get_commit_hash(),
                    "version": _GRAPH_CACHE_VERSION,
                    "include": self.config.include_patterns or [],
                    "exclude": self.config.exclude_patterns or [],
                },
                "components": {
                    k: {
                        **v.model_dump(),
                        # Store relative path only — portable across machines
                        "file_path": v.relative_path or v.file_path,
                    }
                    for k, v in components.items()
                },
                "leaf_nodes": leaf_nodes,
            }
            file_manager.ensure_directory(self.config.dependency_graph_dir)
            with open(self._cache_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            logger.info(
                "💾 Graph cache saved (%d components, %d leaves)", len(components), len(leaf_nodes)
            )
        except Exception as e:
            logger.debug("Failed to save graph cache: %s", e)

    def build_dependency_graph(self) -> tuple[Dict[str, Any], List[str]]:
        """
        Build and save dependency graph, returning components and leaf nodes.

        Returns:
            Tuple of (components, leaf_nodes)
        """
        # Try cache first
        cached = self._try_load_cache()
        if cached is not None:
            return cached

        logger.info("Graph cache miss — rebuilding AST")
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

        # All code-bearing types are valid — no type-based discrimination.
        # Functions carry business logic even in repos that also have classes.
        # Leiden's resolution parameter controls clustering granularity.
        _VALID_TYPES = {
            "class",
            "abstract class",
            "interface",
            "struct",
            "enum",
            "trait",
            "type",
            "function",
            "macro",
            "table",
            "table_array",
            "hls_top",
            "kernel_instance",
            "hls_project",
        }

        keep_leaf_nodes = []
        for leaf_node in leaf_nodes:
            if not isinstance(leaf_node, str) or not leaf_node.strip():
                continue
            if any(kw in leaf_node.lower() for kw in ("error", "exception", "failed", "invalid")):
                logger.warning("Skipping invalid leaf node identifier: '%s'", leaf_node)
                continue
            if leaf_node in components and components[leaf_node].component_type in _VALID_TYPES:
                keep_leaf_nodes.append(leaf_node)
            elif leaf_node not in components:
                logger.warning("Leaf node %s not found in components, removing it", leaf_node)

        logger.debug(
            "GraphBuild complete: %d components, %d graph nodes, %d raw leaves → %d filtered leaves",
            len(components),
            len(graph),
            len(leaf_nodes),
            len(keep_leaf_nodes),
        )
        self._save_cache(components, keep_leaf_nodes)
        return components, keep_leaf_nodes
