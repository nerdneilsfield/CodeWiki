import logging
import threading
from typing import List, Optional, Tuple
from pathlib import Path
import sys
import os

from tree_sitter import Parser, Language
import tree_sitter_c
from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship

logger = logging.getLogger(__name__)

_C_LANGUAGE: "Language | None" = None
_C_LANGUAGE_LOCK = threading.Lock()
_C_PARSER_LOCAL = threading.local()


def _get_c_parser() -> "Parser":
    global _C_LANGUAGE
    if _C_LANGUAGE is None:
        with _C_LANGUAGE_LOCK:
            if _C_LANGUAGE is None:
                _C_LANGUAGE = Language(tree_sitter_c.language())
    p = getattr(_C_PARSER_LOCAL, "parser", None)
    if p is None:
        _C_PARSER_LOCAL.parser = Parser(_C_LANGUAGE)
    return _C_PARSER_LOCAL.parser


class TreeSitterCAnalyzer:
    def __init__(self, file_path: str, content: str, repo_path: str | None = None):
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

        for ext in [".c", ".h"]:
            if rel_path.endswith(ext):
                rel_path = rel_path[: -len(ext)]
                break
        return rel_path.replace("/", ".").replace("\\", ".")

    def _get_relative_path(self) -> str:
        if self.repo_path:
            try:
                return os.path.relpath(str(self.file_path), self.repo_path)
            except ValueError:
                return str(self.file_path)
        else:
            return str(self.file_path)

    def _get_component_id(self, name: str) -> str:
        module_path = self._get_module_path()
        return f"{module_path}.{name}" if module_path else name

    def _analyze(self):
        parser = _get_c_parser()
        tree = parser.parse(bytes(self.content, "utf8"))
        root = tree.root_node
        lines = self.content.splitlines()

        top_level_nodes = {}

        # collect all top-level nodes using recursive traversal
        self._extract_nodes(root, top_level_nodes, lines)

        # extract relationships between top-level nodes
        self._extract_relationships(root, top_level_nodes)

    def _extract_nodes(self, root, top_level_nodes, lines):
        """Extract top-level nodes using an iterative DFS to avoid hitting Python's
        recursion limit on deeply nested headers."""
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = None
            node_name = None

            if node.type == "preproc_include":
                path_node = next(
                    (c for c in node.children if c.type in ("string_literal", "system_lib_string")),
                    None,
                )
                if path_node:
                    include_path = path_node.text.decode().strip('"').strip("<").strip(">")
                    module_path = self._get_module_path()
                    self.call_relationships.append(
                        CallRelationship(
                            caller=f"{module_path}.__file__",
                            callee=include_path,
                            call_line=node.start_point[0] + 1,
                            is_resolved=False,
                            relationship_type="include",
                        )
                    )
                # preproc_include has no interesting children — skip them
                continue

            if node.type == "function_definition":
                node_type = "function"
                declarator = next(
                    (c for c in node.children if c.type == "function_declarator"), None
                )
                if declarator:
                    identifier = next(
                        (c for c in declarator.children if c.type == "identifier"), None
                    )
                    if identifier:
                        node_name = identifier.text.decode()
                        self._current_func_declarator = declarator
                else:
                    self._current_func_declarator = None
            elif node.type == "struct_specifier":
                node_type = "struct"
                for child in node.children:
                    if child.type == "type_identifier":
                        node_name = child.text.decode()
                        break
            elif node.type == "type_definition":
                struct_spec = next((c for c in node.children if c.type == "struct_specifier"), None)
                if struct_spec:
                    node_type = "struct"
                    type_declarator = next(
                        (c for c in node.children if c.type == "type_identifier"), None
                    )
                    if type_declarator:
                        node_name = type_declarator.text.decode()
            elif node.type == "declaration":
                if self._is_global_variable(node):
                    node_type = "variable"
                    for child in node.children:
                        if child.type == "init_declarator":
                            identifier = next(
                                (c for c in child.children if c.type == "identifier"), None
                            )
                            if identifier:
                                node_name = identifier.text.decode()
                                break
                            pointer_declarator = next(
                                (c for c in child.children if c.type == "pointer_declarator"), None
                            )
                            if pointer_declarator:
                                identifier = next(
                                    (
                                        c
                                        for c in pointer_declarator.children
                                        if c.type == "identifier"
                                    ),
                                    None,
                                )
                                if identifier:
                                    node_name = identifier.text.decode()
                                    break
                        elif child.type == "identifier":
                            node_name = child.text.decode()
                            break
            elif node.type == "enum_specifier":
                node_type = "enum"
                for child in node.children:
                    if child.type == "type_identifier":
                        node_name = child.text.decode()
                        break
            elif node.type == "union_specifier":
                node_type = "union"
                for child in node.children:
                    if child.type == "type_identifier":
                        node_name = child.text.decode()
                        break
            elif node.type == "preproc_def":
                node_type = "macro"
                for child in node.children:
                    if child.type == "identifier":
                        node_name = child.text.decode()
                        break
            elif node.type == "preproc_function_def":
                node_type = "macro"
                for child in node.children:
                    if child.type == "identifier":
                        node_name = child.text.decode()
                        break

            if node_type and node_name:
                component_id = self._get_component_id(node_name)
                relative_path = self._get_relative_path()
                _params = None
                if node_type == "function" and getattr(self, "_current_func_declarator", None):
                    _params = self._extract_parameters(self._current_func_declarator)
                    self._current_func_declarator = None
                node_obj = Node(
                    id=component_id,
                    name=node_name,
                    component_type=node_type,
                    file_path=str(self.file_path),
                    relative_path=relative_path,
                    source_code="\n".join(lines[node.start_point[0] : node.end_point[0] + 1]),
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    has_docstring=False,
                    docstring="",
                    parameters=_params,
                    node_type=node_type,
                    base_classes=None,
                    class_name=None,
                    display_name=f"{node_type} {node_name}",
                    component_id=component_id,
                )

                if node_type in ["function", "struct", "enum", "union", "macro"]:
                    self.nodes.append(node_obj)
                top_level_nodes[node_name] = node_obj

            # Push children in reverse so left-to-right order is preserved
            stack.extend(reversed(node.children))

    def _extract_parameters(self, func_declarator_node):
        """Extract parameter names from a function declarator."""
        params = []
        param_list = next(
            (c for c in func_declarator_node.children if c.type == "parameter_list"), None
        )
        if not param_list:
            return None

        for child in param_list.children:
            if child.type == "parameter_declaration":
                # Try pointer_declarator first (int* data)
                ptr = next((c for c in child.children if c.type == "pointer_declarator"), None)
                if ptr:
                    ident = next((c for c in ptr.children if c.type == "identifier"), None)
                    if ident:
                        params.append(ident.text.decode())
                        continue
                # Direct identifier (int count)
                ident = next((c for c in child.children if c.type == "identifier"), None)
                if ident:
                    params.append(ident.text.decode())
                    continue
                # Fallback: use full text
                params.append(child.text.decode().strip())
        return params if params else None

    def _is_global_variable(self, node) -> bool:
        parent = node.parent
        while parent:
            if parent.type == "function_definition":
                return False
            parent = parent.parent
        return True

    def _extract_relationships(self, root, top_level_nodes):
        """Extract relationships using an iterative DFS to avoid hitting Python's
        recursion limit on deeply nested ASTs."""
        stack = [root]
        while stack:
            node = stack.pop()

            # 1. function calls other functions
            if node.type == "call_expression":
                containing_function = self._find_containing_function(node, top_level_nodes)
                if containing_function:
                    containing_function_id = self._get_component_id(containing_function)

                    function_node = next((c for c in node.children if c.type == "identifier"), None)
                    if function_node:
                        called_function = function_node.text.decode()
                        if not self._is_system_function(called_function):
                            self.call_relationships.append(
                                CallRelationship(
                                    caller=containing_function_id,
                                    callee=called_function,
                                    call_line=node.start_point[0] + 1,
                                    is_resolved=False,
                                    relationship_type="call",
                                )
                            )
                    else:
                        # field_expression call: obj.method() or ptr->method()
                        field_expr = next(
                            (c for c in node.children if c.type == "field_expression"), None
                        )
                        if field_expr:
                            field_id = next(
                                (c for c in field_expr.children if c.type == "field_identifier"),
                                None,
                            )
                            if field_id:
                                called_function = field_id.text.decode()
                                if not self._is_system_function(called_function):
                                    self.call_relationships.append(
                                        CallRelationship(
                                            caller=containing_function_id,
                                            callee=called_function,
                                            call_line=node.start_point[0] + 1,
                                            is_resolved=False,
                                            relationship_type="call",
                                        )
                                    )

            # 2. function uses global variables
            if node.type == "identifier":
                containing_function = self._find_containing_function(node, top_level_nodes)
                if containing_function:
                    var_name = node.text.decode()
                    if (
                        var_name in top_level_nodes
                        and top_level_nodes[var_name].component_type == "variable"
                    ):
                        containing_function_id = self._get_component_id(containing_function)
                        var_component_id = self._get_component_id(var_name)
                        self.call_relationships.append(
                            CallRelationship(
                                caller=containing_function_id,
                                callee=var_component_id,
                                call_line=node.start_point[0] + 1,
                                is_resolved=True,
                            )
                        )

            stack.extend(reversed(node.children))

    def _find_containing_function(self, node, top_level_nodes):
        """Find the function that contains this node."""
        current = node.parent
        while current:
            if current.type == "function_definition":
                # Get function name
                declarator = next(
                    (c for c in current.children if c.type == "function_declarator"), None
                )
                if declarator:
                    identifier = next(
                        (c for c in declarator.children if c.type == "identifier"), None
                    )
                    if identifier:
                        func_name = identifier.text.decode()
                        if func_name in top_level_nodes:
                            return func_name
            current = current.parent
        return None

    def _is_system_function(self, func_name: str) -> bool:
        """Check if function is a system/library function."""
        # Common C library functions
        system_functions = {
            "printf",
            "scanf",
            "malloc",
            "free",
            "strlen",
            "strcpy",
            "strcmp",
            "memcpy",
            "memset",
            "exit",
            "abort",
            "fopen",
            "fclose",
            "fread",
            "fwrite",
            "SDL_Init",
            "SDL_CreateWindow",
            "SDL_Log",
            "SDL_GetError",
            "SDL_Quit",
        }
        return func_name in system_functions


def analyze_c_file(
    file_path: str, content: str, repo_path: str | None = None
) -> Tuple[List[Node], List[CallRelationship]]:
    analyzer = TreeSitterCAnalyzer(file_path, content, repo_path)
    return analyzer.nodes, analyzer.call_relationships
