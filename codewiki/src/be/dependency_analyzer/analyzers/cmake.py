import logging
from typing import List, Optional, Tuple
from pathlib import Path
import os

from tree_sitter import Parser, Language
import tree_sitter_cmake
from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship

logger = logging.getLogger(__name__)

# CMake commands that represent meaningful structural relationships
_STRUCTURAL_COMMANDS = {
    "add_executable", "add_library", "add_subdirectory",
    "include", "find_package", "target_link_libraries",
    "target_include_directories", "target_compile_options",
    "add_custom_target", "add_custom_command",
    "install", "configure_file",
}


class TreeSitterCMakeAnalyzer:
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
        for ext in (".cmake", ".txt"):
            if rel_path.endswith(ext):
                rel_path = rel_path[:-len(ext)]
                break
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
        language_capsule = tree_sitter_cmake.language()
        lang = Language(language_capsule)
        parser = Parser(lang)
        tree = parser.parse(bytes(self.content, "utf8"))
        root = tree.root_node
        lines = self.content.splitlines()

        top_level_nodes = {}
        self._extract_nodes(root, top_level_nodes, lines)
        self._extract_relationships(root, top_level_nodes)

    def _extract_nodes(self, node, top_level_nodes, lines):
        if node.type == "function_def":
            fn_cmd = next((c for c in node.children if c.type == "function_command"), None)
            if fn_cmd:
                args = next((c for c in fn_cmd.children if c.type == "argument_list"), None)
                if args:
                    first_arg = next((c for c in args.children if c.type == "argument"), None)
                    if first_arg:
                        name = self._node_text(first_arg).strip().lower()
                        component_id = self._get_component_id(name)
                        node_obj = Node(
                            id=component_id,
                            name=name,
                            component_type="function",
                            file_path=str(self.file_path),
                            relative_path=self._get_relative_path(),
                            source_code="\n".join(lines[node.start_point[0]:node.end_point[0] + 1]),
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            has_docstring=False,
                            docstring="",
                            parameters=None,
                            node_type="function",
                            base_classes=None,
                            class_name=None,
                            display_name=f"function({name})",
                            component_id=component_id,
                        )
                        self.nodes.append(node_obj)
                        top_level_nodes[name] = node_obj

        elif node.type == "macro_def":
            macro_cmd = next((c for c in node.children if c.type == "macro_command"), None)
            if macro_cmd:
                args = next((c for c in macro_cmd.children if c.type == "argument_list"), None)
                if args:
                    first_arg = next((c for c in args.children if c.type == "argument"), None)
                    if first_arg:
                        name = self._node_text(first_arg).strip().lower()
                        component_id = self._get_component_id(name)
                        node_obj = Node(
                            id=component_id,
                            name=name,
                            component_type="macro",
                            file_path=str(self.file_path),
                            relative_path=self._get_relative_path(),
                            source_code="\n".join(lines[node.start_point[0]:node.end_point[0] + 1]),
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            has_docstring=False,
                            docstring="",
                            parameters=None,
                            node_type="macro",
                            base_classes=None,
                            class_name=None,
                            display_name=f"macro({name})",
                            component_id=component_id,
                        )
                        self.nodes.append(node_obj)
                        top_level_nodes[name] = node_obj

        for child in node.children:
            self._extract_nodes(child, top_level_nodes, lines)

    def _extract_relationships(self, node, top_level_nodes):
        if node.type == "normal_command":
            cmd_id = next((c for c in node.children if c.type == "identifier"), None)
            if cmd_id:
                cmd_name = self._node_text(cmd_id).lower()
                # Call to user-defined function/macro
                if cmd_name in top_level_nodes:
                    caller = self._find_containing_fn(node, top_level_nodes)
                    callee_id = self._get_component_id(cmd_name)
                    if caller:
                        self.call_relationships.append(CallRelationship(
                            caller=caller,
                            callee=callee_id,
                            call_line=node.start_point[0] + 1,
                            is_resolved=True,
                        ))
                # Structural dependency (include, find_package, etc.)
                elif cmd_name in _STRUCTURAL_COMMANDS:
                    args = next((c for c in node.children if c.type == "argument_list"), None)
                    if args:
                        first_arg = next((c for c in args.children if c.type == "argument"), None)
                        if first_arg:
                            target_name = self._node_text(first_arg).strip()
                            caller = self._find_containing_fn(node, top_level_nodes)
                            module_path = self._get_module_path()
                            callee_ref = f"{cmd_name}:{target_name}"
                            if caller:
                                self.call_relationships.append(CallRelationship(
                                    caller=caller,
                                    callee=callee_ref,
                                    call_line=node.start_point[0] + 1,
                                    is_resolved=False,
                                ))

        for child in node.children:
            self._extract_relationships(child, top_level_nodes)

    def _find_containing_fn(self, node, top_level_nodes) -> Optional[str]:
        current = node.parent
        while current:
            if current.type in ("function_def", "macro_def"):
                cmd_type = "function_command" if current.type == "function_def" else "macro_command"
                fn_cmd = next((c for c in current.children if c.type == cmd_type), None)
                if fn_cmd:
                    args = next((c for c in fn_cmd.children if c.type == "argument_list"), None)
                    if args:
                        first = next((c for c in args.children if c.type == "argument"), None)
                        if first:
                            name = self._node_text(first).strip().lower()
                            return self._get_component_id(name)
            current = current.parent
        # top-level script context
        return f"{self._get_module_path()}.__script__"


def analyze_cmake_file(
    file_path: str,
    content: str,
    repo_path: str = None,
) -> Tuple[List[Node], List[CallRelationship]]:
    analyzer = TreeSitterCMakeAnalyzer(file_path, content, repo_path)
    return analyzer.nodes, analyzer.call_relationships
