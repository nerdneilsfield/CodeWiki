import os
import json
import logging
import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple, Optional, Any, Union
from pathlib import Path
import re

from codewiki.src.be.dependency_analyzer.analysis.analysis_service import AnalysisService
from codewiki.src.be.dependency_analyzer.models.core import Node


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class DependencyParser:
    """Parser for extracting code components from multi-language repositories."""

    def __init__(
        self,
        repo_path: str,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ):
        """
        Initialize the dependency parser.

        Args:
            repo_path: Path to the repository
            include_patterns: File patterns to include (e.g., ["*.cs", "*.py"])
            exclude_patterns: File/directory patterns to exclude (e.g., ["*Tests*"])
        """
        self.repo_path = os.path.abspath(repo_path)
        self.components: Dict[str, Node] = {}
        self.modules: Set[str] = set()
        self.include_patterns = include_patterns
        self.exclude_patterns = exclude_patterns

        self.analysis_service = AnalysisService()

    def parse_repository(self, filtered_folders: Optional[List[str]] = None) -> Dict[str, Node]:
        logger.debug(f"Parsing repository at {self.repo_path}")

        # Log custom patterns if set
        if self.include_patterns:
            logger.info(f"Using custom include patterns: {self.include_patterns}")
        if self.exclude_patterns:
            logger.info(f"Using custom exclude patterns: {self.exclude_patterns}")

        structure_result = self.analysis_service._analyze_structure(
            self.repo_path,
            include_patterns=self.include_patterns,
            exclude_patterns=self.exclude_patterns,
        )

        call_graph_result = self.analysis_service._analyze_call_graph(
            structure_result["file_tree"], self.repo_path
        )

        self._build_components_from_analysis(call_graph_result)

        logger.debug(f"Found {len(self.components)} components across {len(self.modules)} modules")
        return self.components

    def _build_components_from_analysis(self, call_graph_result: Dict):
        functions = call_graph_result.get("functions", [])
        relationships = call_graph_result.get("relationships", [])

        component_id_mapping = {}
        name_to_comp_ids: dict[str, list[str]] = defaultdict(list)

        for func_dict in functions:
            component_id = func_dict.get("id", "")
            if not component_id:
                continue

            node = Node(
                id=component_id,
                name=func_dict.get("name", ""),
                component_type=func_dict.get(
                    "component_type", func_dict.get("node_type", "function")
                ),
                file_path=func_dict.get("file_path", ""),
                relative_path=func_dict.get("relative_path", ""),
                source_code=func_dict.get("source_code", func_dict.get("code_snippet", "")),
                start_line=func_dict.get("start_line", 0),
                end_line=func_dict.get("end_line", 0),
                has_docstring=func_dict.get("has_docstring", bool(func_dict.get("docstring", ""))),
                docstring=func_dict.get("docstring", "") or "",
                parameters=func_dict.get("parameters", []),
                node_type=func_dict.get("node_type", "function"),
                base_classes=func_dict.get("base_classes"),
                class_name=func_dict.get("class_name"),
                display_name=func_dict.get("display_name", ""),
                component_id=component_id,
                # HLS-specific fields — preserved from compiled-language analyzers
                is_hls_kernel=func_dict.get("is_hls_kernel", False),
                hls_pragmas=func_dict.get("hls_pragmas") or None,
            )

            self.components[component_id] = node

            component_id_mapping[component_id] = component_id
            legacy_id = f"{func_dict.get('file_path', '')}:{func_dict.get('name', '')}"
            if legacy_id and legacy_id != component_id:
                component_id_mapping[legacy_id] = component_id

            # Build name→comp_id index for O(1) callee resolution below.
            _node_name = func_dict.get("name", "")
            if _node_name:
                name_to_comp_ids[_node_name].append(component_id)
            _short_name = component_id.split(".")[-1] if "." in component_id else ""
            if _short_name and _short_name != _node_name:
                name_to_comp_ids[_short_name].append(component_id)

            if "." in component_id:
                module_parts = component_id.split(".")[:-1]
                module_path = ".".join(module_parts)
                if module_path:
                    self.modules.add(module_path)

        processed_relationships = 0
        for rel_dict in relationships:
            caller_id = rel_dict.get("caller", "")
            callee_id = rel_dict.get("callee", "")
            is_resolved = rel_dict.get("is_resolved", False)

            caller_component_id = component_id_mapping.get(caller_id)

            callee_component_id = component_id_mapping.get(callee_id)
            if not callee_component_id:
                candidates = name_to_comp_ids.get(callee_id, [])
                callee_component_id = candidates[0] if candidates else None

            if caller_component_id and caller_component_id in self.components:
                if callee_component_id:
                    self.components[caller_component_id].depends_on.add(callee_component_id)
                    processed_relationships += 1

        # Second pass: HLS file-based relationships.
        # hls_source / hls_compile callees are file paths (e.g. "mm2s.cpp"), not
        # node IDs, so they can't be resolved by the normal ID lookup above.
        # Build a filename → [component_ids] index, then wire up HLS nodes.
        _file_to_comps: Dict[str, List[str]] = defaultdict(list)
        for comp_id, comp_node in self.components.items():
            fname = os.path.basename(comp_node.relative_path or comp_node.file_path or "")
            if fname:
                _file_to_comps[fname].append(comp_id)

        _HLS_FILE_REL_TYPES = {"hls_source", "hls_compile"}
        for rel_dict in relationships:
            if rel_dict.get("relationship_type") not in _HLS_FILE_REL_TYPES:
                continue
            caller_id = rel_dict.get("caller", "")
            callee_id = rel_dict.get("callee", "")  # e.g. "mm2s.cpp" or "src/mm2s.cpp"
            caller_comp_id = component_id_mapping.get(caller_id)
            if not caller_comp_id or caller_comp_id not in self.components:
                continue
            # Match by filename basename (strip leading paths and "./" prefixes)
            callee_fname = os.path.basename(callee_id.lstrip("./"))
            for dep_comp_id in _file_to_comps.get(callee_fname, []):
                if dep_comp_id != caller_comp_id:
                    self.components[caller_comp_id].depends_on.add(dep_comp_id)
                    processed_relationships += 1

    def _determine_component_type(self, func_dict: Dict) -> str:
        if func_dict.get("is_method", False):
            return "method"

        node_type = func_dict.get("node_type", "")
        if node_type in [
            "class",
            "interface",
            "struct",
            "enum",
            "record",
            "abstract class",
            "annotation",
            "delegate",
        ]:
            return node_type

        return "function"

    def _file_to_module_path(self, file_path: str) -> str:
        path = file_path
        extensions = [
            ".py",
            ".js",
            ".ts",
            ".java",
            ".cs",
            ".cpp",
            ".hpp",
            ".h",
            ".c",
            ".tsx",
            ".jsx",
            ".cc",
            ".mjs",
            ".cxx",
            ".cc",
            ".cjs",
        ]
        for ext in extensions:
            if path.endswith(ext):
                path = path[: -len(ext)]
                break
        return path.replace(os.path.sep, ".")

    def save_dependency_graph(self, output_path: str):
        result = {}
        for component_id, component in self.components.items():
            component_dict = component.model_dump()
            if "depends_on" in component_dict and isinstance(component_dict["depends_on"], set):
                component_dict["depends_on"] = list(component_dict["depends_on"])
            result[component_id] = component_dict

        dir_name = os.path.dirname(output_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        logger.debug(f"Saved {len(self.components)} components to {output_path}")
        return result
