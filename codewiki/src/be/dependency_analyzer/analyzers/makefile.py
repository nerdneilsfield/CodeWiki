import logging
from typing import List, Optional, Tuple
from pathlib import Path
import os

from tree_sitter import Parser, Language
import tree_sitter_make
from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship

logger = logging.getLogger(__name__)


class TreeSitterMakefileAnalyzer:
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
        language_capsule = tree_sitter_make.language()
        lang = Language(language_capsule)
        parser = Parser(lang)
        tree = parser.parse(bytes(self.content, "utf8"))
        root = tree.root_node
        lines = self.content.splitlines()

        # First pass: collect all target names (for target_dep classification)
        target_names = set()
        for node in root.children:
            if node.type == "rule":
                targets_node = next(
                    (c for c in node.children if c.type == "targets"), None
                )
                if targets_node:
                    for word in targets_node.children:
                        if word.type == "word":
                            target_names.add(self._node_text(word).strip())

        # First pass continued: create Node objects for each target
        for node in root.children:
            if node.type == "rule":
                targets_node = next(
                    (c for c in node.children if c.type == "targets"), None
                )
                if targets_node:
                    for word in targets_node.children:
                        if word.type == "word":
                            target_name = self._node_text(word).strip()
                            if target_name:
                                component_id = self._get_component_id(target_name)
                                node_obj = Node(
                                    id=component_id,
                                    name=target_name,
                                    component_type="target",
                                    file_path=str(self.file_path),
                                    relative_path=self._get_relative_path(),
                                    source_code="\n".join(
                                        lines[node.start_point[0]:node.end_point[0] + 1]
                                    ),
                                    start_line=node.start_point[0] + 1,
                                    end_line=node.end_point[0] + 1,
                                    has_docstring=False,
                                    docstring="",
                                    parameters=None,
                                    node_type="target",
                                    base_classes=None,
                                    class_name=None,
                                    display_name=f"target({target_name})",
                                    component_id=component_id,
                                )
                                self.nodes.append(node_obj)

        # Second pass: extract target → prerequisite relationships
        for node in root.children:
            if node.type == "rule":
                targets_node = next(
                    (c for c in node.children if c.type == "targets"), None
                )
                prereqs_node = next(
                    (c for c in node.children if c.type == "prerequisites"), None
                )
                if targets_node and prereqs_node:
                    target_text = " ".join(
                        self._node_text(w).strip()
                        for w in targets_node.children
                        if w.type == "word"
                    )
                    prereq_text = " ".join(
                        self._node_text(w).strip()
                        for w in prereqs_node.children
                        if w.type == "word"
                    )
                    for target_name in target_text.split():
                        caller_id = self._get_component_id(target_name)
                        for prereq in prereq_text.split():
                            # Classify relationship type by extension
                            if prereq in target_names:
                                rel_type = "target_dep"
                                callee = self._get_component_id(prereq)
                                is_resolved = True
                            elif any(prereq.endswith(ext) for ext in (".h", ".hpp", ".hxx")):
                                rel_type = "header_dep"
                                callee = prereq
                                is_resolved = False
                            elif any(prereq.endswith(ext) for ext in (".c", ".cpp", ".cc", ".cxx")):
                                rel_type = "compile_dep"
                                callee = prereq
                                is_resolved = False
                            else:
                                rel_type = "prerequisite"
                                callee = prereq
                                is_resolved = False
                            self.call_relationships.append(CallRelationship(
                                caller=caller_id,
                                callee=callee,
                                call_line=node.start_point[0] + 1,
                                is_resolved=is_resolved,
                                relationship_type=rel_type,
                            ))

                # Detect v++/vitis_hls in recipe
                recipe_node = next(
                    (c for c in node.children if c.type == "recipe"), None
                )
                if recipe_node and targets_node:
                    recipe_text = self._node_text(recipe_node)
                    if "v++" in recipe_text or "vitis_hls" in recipe_text:
                        for word in targets_node.children:
                            if word.type == "word":
                                target_name = self._node_text(word).strip()
                                self.call_relationships.append(CallRelationship(
                                    caller=self._get_component_id(target_name),
                                    callee="v++",
                                    call_line=node.start_point[0] + 1,
                                    is_resolved=False,
                                    relationship_type="hls_compile",
                                ))


def analyze_makefile_file(
    file_path: str,
    content: str,
    repo_path: str = None,
) -> Tuple[List[Node], List[CallRelationship]]:
    analyzer = TreeSitterMakefileAnalyzer(file_path, content, repo_path)
    return analyzer.nodes, analyzer.call_relationships
