import logging
from typing import List, Optional, Tuple
from pathlib import Path
import os

from tree_sitter import Parser, Language
import tree_sitter_go
from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship

logger = logging.getLogger(__name__)


class TreeSitterGoAnalyzer:
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
        if rel_path.endswith(".go"):
            rel_path = rel_path[:-3]
        return rel_path.replace("/", ".").replace("\\", ".")

    def _get_relative_path(self) -> str:
        if self.repo_path:
            try:
                return os.path.relpath(str(self.file_path), self.repo_path)
            except ValueError:
                return str(self.file_path)
        return str(self.file_path)

    def _get_component_id(self, name: str, receiver_type: str = None) -> str:
        module_path = self._get_module_path()
        if receiver_type:
            return f"{module_path}.{receiver_type}.{name}"
        return f"{module_path}.{name}"

    def _node_text(self, node) -> str:
        return node.text.decode("utf8")

    def _analyze(self):
        language_capsule = tree_sitter_go.language()
        lang = Language(language_capsule)
        parser = Parser(lang)
        tree = parser.parse(bytes(self.content, "utf8"))
        root = tree.root_node
        lines = self.content.splitlines()

        top_level_nodes = {}
        self._extract_nodes(root, top_level_nodes, lines)
        self._extract_relationships(root, top_level_nodes)

    def _extract_nodes(self, node, top_level_nodes, lines):
        if node.type == "function_declaration":
            name_node = next((c for c in node.children if c.type == "identifier"), None)
            if name_node:
                name = self._node_text(name_node)
                params = self._extract_params(node)
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
                    parameters=params,
                    node_type="function",
                    base_classes=None,
                    class_name=None,
                    display_name=f"func {name}",
                    component_id=component_id,
                )
                self.nodes.append(node_obj)
                top_level_nodes[name] = node_obj

        elif node.type == "method_declaration":
            # receiver: first parameter_list child contains the receiver type
            receiver_type = self._get_receiver_type(node)
            name_node = next((c for c in node.children if c.type == "field_identifier"), None)
            if name_node:
                name = self._node_text(name_node)
                params = self._extract_params(node)
                component_name = f"{receiver_type}.{name}" if receiver_type else name
                component_id = self._get_component_id(name, receiver_type)
                node_obj = Node(
                    id=component_id,
                    name=component_name,
                    component_type="method",
                    file_path=str(self.file_path),
                    relative_path=self._get_relative_path(),
                    source_code="\n".join(lines[node.start_point[0]:node.end_point[0] + 1]),
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    has_docstring=False,
                    docstring="",
                    parameters=params,
                    node_type="method",
                    base_classes=None,
                    class_name=receiver_type,
                    display_name=f"func ({receiver_type}) {name}",
                    component_id=component_id,
                )
                self.nodes.append(node_obj)
                top_level_nodes[component_name] = node_obj

        elif node.type == "type_declaration":
            for spec in node.children:
                if spec.type == "type_spec":
                    name_node = next((c for c in spec.children if c.type == "type_identifier"), None)
                    type_node = spec.children[-1] if spec.children else None
                    if name_node and type_node:
                        name = self._node_text(name_node)
                        if type_node.type == "struct_type":
                            node_type = "struct"
                        elif type_node.type == "interface_type":
                            node_type = "interface"
                        else:
                            node_type = "type"
                        component_id = self._get_component_id(name)
                        node_obj = Node(
                            id=component_id,
                            name=name,
                            component_type=node_type,
                            file_path=str(self.file_path),
                            relative_path=self._get_relative_path(),
                            source_code="\n".join(lines[node.start_point[0]:node.end_point[0] + 1]),
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            has_docstring=False,
                            docstring="",
                            parameters=None,
                            node_type=node_type,
                            base_classes=None,
                            class_name=None,
                            display_name=f"{node_type} {name}",
                            component_id=component_id,
                        )
                        self.nodes.append(node_obj)
                        top_level_nodes[name] = node_obj

        for child in node.children:
            if node.type not in ("function_declaration", "method_declaration"):
                self._extract_nodes(child, top_level_nodes, lines)

    def _get_receiver_type(self, method_node) -> Optional[str]:
        """Extract receiver type name from a method_declaration."""
        # The first parameter_list is the receiver list
        param_lists = [c for c in method_node.children if c.type == "parameter_list"]
        if not param_lists:
            return None
        receiver_list = param_lists[0]
        for child in receiver_list.children:
            if child.type == "parameter_declaration":
                for c in child.children:
                    if c.type == "type_identifier":
                        return self._node_text(c)
                    elif c.type == "pointer_type":
                        type_id = next((x for x in c.children if x.type == "type_identifier"), None)
                        if type_id:
                            return self._node_text(type_id)
        return None

    def _extract_params(self, fn_node) -> List[str]:
        param_lists = [c for c in fn_node.children if c.type == "parameter_list"]
        # Skip receiver list (first one) for method_declaration
        if fn_node.type == "method_declaration" and len(param_lists) >= 2:
            params_node = param_lists[1]
        elif param_lists:
            params_node = param_lists[0]
        else:
            return []
        params = []
        for child in params_node.children:
            if child.type == "parameter_declaration":
                id_node = next((c for c in child.children if c.type == "identifier"), None)
                if id_node:
                    params.append(self._node_text(id_node))
        return params

    def _extract_relationships(self, node, top_level_nodes):
        if node.type == "call_expression":
            fn_node = node.children[0] if node.children else None
            if fn_node:
                caller_id = self._find_containing_fn(node, top_level_nodes)
                callee_name = self._resolve_call_target(fn_node)
                if caller_id and callee_name and not self._is_builtin(callee_name):
                    self.call_relationships.append(CallRelationship(
                        caller=caller_id,
                        callee=callee_name,
                        call_line=node.start_point[0] + 1,
                        is_resolved=False,
                    ))

        for child in node.children:
            self._extract_relationships(child, top_level_nodes)

    def _resolve_call_target(self, fn_node) -> Optional[str]:
        if fn_node.type == "identifier":
            return self._node_text(fn_node)
        elif fn_node.type == "selector_expression":
            field = next((c for c in fn_node.children if c.type == "field_identifier"), None)
            return self._node_text(field) if field else None
        elif fn_node.type == "qualified_type":
            return self._node_text(fn_node).split(".")[-1]
        return None

    def _find_containing_fn(self, node, top_level_nodes) -> Optional[str]:
        current = node.parent
        while current:
            if current.type == "function_declaration":
                name_node = next((c for c in current.children if c.type == "identifier"), None)
                if name_node:
                    return self._get_component_id(self._node_text(name_node))
            elif current.type == "method_declaration":
                receiver_type = self._get_receiver_type(current)
                name_node = next((c for c in current.children if c.type == "field_identifier"), None)
                if name_node:
                    return self._get_component_id(self._node_text(name_node), receiver_type)
            current = current.parent
        return None

    def _is_builtin(self, name: str) -> bool:
        builtins = {
            "make", "new", "len", "cap", "append", "copy", "delete", "close",
            "panic", "recover", "print", "println", "error", "nil",
            "fmt", "log", "os", "io", "strings", "strconv",
        }
        return name in builtins


def analyze_go_file(
    file_path: str,
    content: str,
    repo_path: str = None,
) -> Tuple[List[Node], List[CallRelationship]]:
    analyzer = TreeSitterGoAnalyzer(file_path, content, repo_path)
    return analyzer.nodes, analyzer.call_relationships
