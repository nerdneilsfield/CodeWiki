import logging
from typing import List, Optional, Tuple
from pathlib import Path
import os

from tree_sitter import Parser, Language
import tree_sitter_toml
from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship

logger = logging.getLogger(__name__)


class TreeSitterTOMLAnalyzer:
    """
    TOML analyzer — extracts top-level tables and arrays-of-tables as structural
    nodes. No call relationships are generated (TOML is configuration, not code).
    """

    def __init__(self, file_path: str, content: str, repo_path: str = None):
        self.file_path = Path(file_path)
        self.content = content
        self.repo_path = repo_path or ""
        self.nodes: List[Node] = []
        self.call_relationships: List[CallRelationship] = []
        self._analyze()

    def _get_module_path(self) -> str:
        if self.repo_path:
            try:
                rel_path = os.path.relpath(str(self.file_path), self.repo_path)
            except ValueError:
                rel_path = str(self.file_path)
        else:
            rel_path = str(self.file_path)
        if rel_path.endswith(".toml"):
            rel_path = rel_path[:-5]
        return rel_path.replace("/", ".").replace("\\", ".")

    def _get_relative_path(self) -> str:
        if self.repo_path:
            try:
                return os.path.relpath(str(self.file_path), self.repo_path)
            except ValueError:
                return str(self.file_path)
        return str(self.file_path)

    def _get_component_id(self, name: str) -> str:
        return f"{self._get_module_path()}.{name}"

    def _node_text(self, node) -> str:
        return node.text.decode("utf8")

    def _analyze(self):
        language_capsule = tree_sitter_toml.language()
        lang = Language(language_capsule)
        parser = Parser(lang)
        tree = parser.parse(bytes(self.content, "utf8"))
        root = tree.root_node
        lines = self.content.splitlines()
        self._extract_nodes(root, lines)

    def _extract_nodes(self, root, lines):
        seen = set()
        for node in root.children:
            if node.type == "table":
                # [section.subsection] — grab the key path as name
                key_node = next(
                    (c for c in node.children if c.type in ("key", "dotted_key", "bare_key", "quoted_key")), None
                )
                if key_node:
                    name = self._node_text(key_node).strip()
                    # Only register the top-level section (before first dot)
                    top_name = name.split(".")[0].strip('"').strip("'")
                    if top_name and top_name not in seen:
                        seen.add(top_name)
                        component_id = self._get_component_id(top_name)
                        node_obj = Node(
                            id=component_id,
                            name=top_name,
                            component_type="table",
                            file_path=str(self.file_path),
                            relative_path=self._get_relative_path(),
                            source_code="\n".join(lines[node.start_point[0]:node.end_point[0] + 1]),
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            has_docstring=False,
                            docstring="",
                            parameters=None,
                            node_type="table",
                            base_classes=None,
                            class_name=None,
                            display_name=f"[{top_name}]",
                            component_id=component_id,
                        )
                        self.nodes.append(node_obj)

            elif node.type == "table_array_element":
                key_node = next(
                    (c for c in node.children if c.type in ("key", "dotted_key", "bare_key", "quoted_key")), None
                )
                if key_node:
                    name = self._node_text(key_node).strip()
                    top_name = name.split(".")[0].strip('"').strip("'")
                    if top_name and top_name not in seen:
                        seen.add(top_name)
                        component_id = self._get_component_id(top_name)
                        node_obj = Node(
                            id=component_id,
                            name=top_name,
                            component_type="table_array",
                            file_path=str(self.file_path),
                            relative_path=self._get_relative_path(),
                            source_code="\n".join(lines[node.start_point[0]:node.end_point[0] + 1]),
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            has_docstring=False,
                            docstring="",
                            parameters=None,
                            node_type="table_array",
                            base_classes=None,
                            class_name=None,
                            display_name=f"[[{top_name}]]",
                            component_id=component_id,
                        )
                        self.nodes.append(node_obj)


def analyze_toml_file(
    file_path: str,
    content: str,
    repo_path: str = None,
) -> Tuple[List[Node], List[CallRelationship]]:
    analyzer = TreeSitterTOMLAnalyzer(file_path, content, repo_path)
    return analyzer.nodes, analyzer.call_relationships
