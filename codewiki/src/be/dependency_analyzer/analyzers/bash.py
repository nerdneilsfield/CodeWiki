import logging
from typing import List, Optional, Tuple
from pathlib import Path
import os

from tree_sitter import Parser, Language
import tree_sitter_bash
from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship

logger = logging.getLogger(__name__)

_BUILTIN_COMMANDS = {
    "echo", "printf", "read", "exit", "return", "export", "local", "declare",
    "unset", "set", "shift", "source", ".", "cd", "pwd", "ls", "mkdir", "rm",
    "cp", "mv", "cat", "grep", "sed", "awk", "find", "test", "[", "[[",
    "if", "then", "else", "fi", "for", "while", "do", "done", "case", "esac",
    "true", "false", ":", "eval", "exec", "trap", "wait", "kill", "jobs",
    "pushd", "popd", "type", "which", "command", "builtin",
}


class TreeSitterBashAnalyzer:
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
        for ext in (".sh", ".bash", ".zsh"):
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
        language_capsule = tree_sitter_bash.language()
        lang = Language(language_capsule)
        parser = Parser(lang)
        tree = parser.parse(bytes(self.content, "utf8"))
        root = tree.root_node
        lines = self.content.splitlines()

        top_level_nodes = {}
        self._extract_nodes(root, top_level_nodes, lines)
        self._extract_relationships(root, top_level_nodes)

    def _extract_nodes(self, node, top_level_nodes, lines):
        if node.type == "function_definition":
            name_node = next((c for c in node.children if c.type == "word"), None)
            if name_node:
                name = self._node_text(name_node)
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
                    display_name=f"function {name}",
                    component_id=component_id,
                )
                self.nodes.append(node_obj)
                top_level_nodes[name] = node_obj

        for child in node.children:
            if node.type != "function_definition":
                self._extract_nodes(child, top_level_nodes, lines)

    def _extract_relationships(self, node, top_level_nodes):
        if node.type == "command":
            cmd_name_node = next(
                (c for c in node.children if c.type in ("command_name", "word")), None
            )
            if cmd_name_node:
                # command_name may wrap a word
                if cmd_name_node.type == "command_name":
                    word = next((c for c in cmd_name_node.children if c.type == "word"), None)
                    cmd_name = self._node_text(word) if word else ""
                else:
                    cmd_name = self._node_text(cmd_name_node)

                if cmd_name and cmd_name not in _BUILTIN_COMMANDS:
                    caller_id = self._find_containing_fn(node, top_level_nodes)
                    if caller_id and cmd_name in top_level_nodes:
                        self.call_relationships.append(CallRelationship(
                            caller=caller_id,
                            callee=self._get_component_id(cmd_name),
                            call_line=node.start_point[0] + 1,
                            is_resolved=True,
                        ))

        for child in node.children:
            self._extract_relationships(child, top_level_nodes)

    def _find_containing_fn(self, node, top_level_nodes) -> Optional[str]:
        current = node.parent
        while current:
            if current.type == "function_definition":
                name_node = next((c for c in current.children if c.type == "word"), None)
                if name_node:
                    return self._get_component_id(self._node_text(name_node))
            current = current.parent
        return None


def analyze_bash_file(
    file_path: str,
    content: str,
    repo_path: str = None,
) -> Tuple[List[Node], List[CallRelationship]]:
    analyzer = TreeSitterBashAnalyzer(file_path, content, repo_path)
    return analyzer.nodes, analyzer.call_relationships
