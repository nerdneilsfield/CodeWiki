import logging
import threading
from typing import List, Optional, Tuple
from pathlib import Path
import sys
import os

from tree_sitter import Parser, Language
import tree_sitter_cpp
from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship

logger = logging.getLogger(__name__)

_CPP_LANGUAGE: "Language | None" = None
_CPP_LANGUAGE_LOCK = threading.Lock()
_CPP_PARSER_LOCAL = threading.local()


def _get_cpp_parser() -> "Parser":
    global _CPP_LANGUAGE
    if _CPP_LANGUAGE is None:
        with _CPP_LANGUAGE_LOCK:
            if _CPP_LANGUAGE is None:
                _CPP_LANGUAGE = Language(tree_sitter_cpp.language())
    p = getattr(_CPP_PARSER_LOCAL, "parser", None)
    if p is None:
        _CPP_PARSER_LOCAL.parser = Parser(_CPP_LANGUAGE)
    return _CPP_PARSER_LOCAL.parser


class TreeSitterCppAnalyzer:
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

        for ext in [".cpp", ".cc", ".cxx", ".hpp", ".h"]:
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

    def _get_component_id(self, name: str, parent_class: str | None = None) -> str:
        module_path = self._get_module_path()
        if parent_class:
            return (
                f"{module_path}.{parent_class}.{name}" if module_path else f"{parent_class}.{name}"
            )
        return f"{module_path}.{name}" if module_path else name

    def _analyze(self):
        parser = _get_cpp_parser()
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
            containing_class = None

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

            if node.type == "class_specifier":
                node_type = "class"
                for child in node.children:
                    if child.type == "type_identifier":
                        node_name = child.text.decode()
                        break
            elif node.type == "struct_specifier":
                node_type = "struct"
                for child in node.children:
                    if child.type == "type_identifier":
                        node_name = child.text.decode()
                        break
            elif node.type == "function_definition":
                containing_class = self._find_containing_class_for_method(node)
                if containing_class:
                    node_type = "method"
                else:
                    node_type = "function"

                declarator = next(
                    (c for c in node.children if c.type == "function_declarator"), None
                )
                if declarator:
                    self._current_func_declarator = declarator
                    for child in declarator.children:
                        if child.type == "identifier":
                            node_name = child.text.decode()
                            break
                        elif child.type == "field_identifier":
                            node_name = child.text.decode()
                            break
                        elif child.type == "qualified_identifier":
                            identifiers = [c for c in child.children if c.type == "identifier"]
                            if identifiers:
                                node_name = identifiers[-1].text.decode()
                                break
                else:
                    self._current_func_declarator = None
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
                        elif child.type == "identifier":
                            node_name = child.text.decode()
                            break
            elif node.type == "template_declaration":
                inner_class = next((c for c in node.children if c.type == "class_specifier"), None)
                inner_func = next(
                    (c for c in node.children if c.type == "function_definition"), None
                )
                if inner_class:
                    node_type = "template_class"
                    for child in inner_class.children:
                        if child.type == "type_identifier":
                            node_name = child.text.decode()
                            break
                elif inner_func:
                    node_type = "template_function"
                    declarator = next(
                        (c for c in inner_func.children if c.type == "function_declarator"), None
                    )
                    if declarator:
                        for child in declarator.children:
                            if child.type == "identifier":
                                node_name = child.text.decode()
                                break
            elif node.type == "namespace_definition":
                node_type = "namespace"
                found_namespace_keyword = False
                for child in node.children:
                    if child.type == "namespace":
                        found_namespace_keyword = True
                    elif found_namespace_keyword and child.type == "identifier":
                        node_name = child.text.decode()
                        break

            if node_type and node_name:
                if node_type == "method":
                    component_id = self._get_component_id(node_name, containing_class or None)
                    top_level_key = component_id
                else:
                    component_id = self._get_component_id(node_name)
                    top_level_key = node_name

                relative_path = self._get_relative_path()

                _params = None
                if node_type in ("function", "method", "template_function") and getattr(
                    self, "_current_func_declarator", None
                ):
                    _params = self._extract_parameters(self._current_func_declarator)
                    self._current_func_declarator = None
                _hls_pragmas = None
                _is_hls_kernel = False
                if node_type in ("function", "method"):
                    _hls_pragmas = self._extract_hls_pragmas(node)
                    if _hls_pragmas and self._is_in_extern_c(node):
                        _is_hls_kernel = True
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
                    class_name=containing_class if node_type == "method" else None,
                    display_name=f"{node_type} {node_name}",
                    component_id=component_id,
                    hls_pragmas=_hls_pragmas,
                    is_hls_kernel=_is_hls_kernel,
                )

                top_level_nodes[top_level_key] = node_obj

                if node_type in [
                    "class",
                    "struct",
                    "function",
                    "template_class",
                    "template_function",
                    "method",
                ]:
                    self.nodes.append(node_obj)

            # Push children in reverse so left-to-right order is preserved
            stack.extend(reversed(node.children))

    def _extract_hls_pragmas(self, func_node):
        """Extract HLS pragmas from within a function body."""
        from codewiki.src.be.dependency_analyzer.models.core import HLSPragma

        pragmas = []
        self._collect_pragmas(func_node, pragmas)
        return pragmas if pragmas else None

    def _collect_pragmas(self, node, pragmas):
        if node.type == "preproc_call":
            text = node.text.decode().strip()
            if "#pragma" in text.lower() and "HLS" in text.upper():
                pragma = self._parse_hls_pragma(text, node.start_point[0] + 1)
                if pragma:
                    pragmas.append(pragma)
        for child in node.children:
            self._collect_pragmas(child, pragmas)

    def _parse_hls_pragma(self, text: str, line: int):
        from codewiki.src.be.dependency_analyzer.models.core import HLSPragma

        parts = text.split()
        hls_idx = None
        for i, p in enumerate(parts):
            if p.upper() == "HLS":
                hls_idx = i
                break
        if hls_idx is None or hls_idx + 1 >= len(parts):
            return None
        pragma_type = parts[hls_idx + 1].upper()
        params = {}
        for part in parts[hls_idx + 2 :]:
            if "=" in part:
                k, v = part.split("=", 1)
                params[k.lower()] = v
            elif part not in ("#pragma", "HLS", pragma_type):
                if "subtype" not in params:
                    params["subtype"] = part
        target = params.get("port") or params.get("variable")
        semantic = self._pragma_semantic(pragma_type, params)
        return HLSPragma(
            pragma_type=pragma_type,
            params=params,
            target=target,
            line=line,
            hardware_semantic=semantic,
        )

    def _pragma_semantic(self, pragma_type: str, params: dict) -> str:
        subtype = params.get("subtype", "")
        port = params.get("port", "")
        bundle = params.get("bundle", "")
        if pragma_type == "INTERFACE":
            m = {
                "m_axi": "AXI Master memory interface" + (", bundle " + bundle if bundle else ""),
                "s_axilite": "AXI-Lite control/status register" + (" for " + port if port else ""),
                "axis": "AXI-Stream data port" + (" " + port if port else ""),
                "ap_none": "Wire port (no handshake)" + (" " + port if port else ""),
            }
            return m.get(subtype.lower(), "Hardware interface (" + subtype + ")")
        m2 = {
            "PIPELINE": "Pipelined with initiation interval "
            + params.get("ii", "auto")
            + " cycles",
            "DATAFLOW": "Task-level pipelining with automatic FIFOs between functions",
            "UNROLL": "Loop unrolled "
            + str(params.get("factor", "fully"))
            + "x for parallel execution",
            "ARRAY_PARTITION": "Array partitioned for parallel memory access",
            "INLINE": "Function inlined into caller (no separate hardware module)",
            "STREAM": "Variable implemented as hardware FIFO",
        }
        return m2.get(pragma_type, "HLS " + pragma_type + " directive")

    def _is_in_extern_c(self, node) -> bool:
        """Check if node is inside an extern C linkage specification."""
        current = node.parent
        while current:
            if current.type == "linkage_specification":
                for child in current.children:
                    if child.type == "string_literal":
                        raw = child.text.decode().replace('"', "").replace("'", "").strip()
                        if raw == "C":
                            return True
            current = current.parent
        return False

    def _extract_parameters(self, func_declarator_node):
        """Extract parameter names from a C++ function declarator."""
        params = []
        param_list = next(
            (c for c in func_declarator_node.children if c.type == "parameter_list"), None
        )
        if not param_list:
            return None

        for child in param_list.children:
            if child.type == "parameter_declaration":
                # Try reference_declarator first (const int& ref)
                ref = next((c for c in child.children if c.type == "reference_declarator"), None)
                if ref:
                    ident = next((c for c in ref.children if c.type == "identifier"), None)
                    if ident:
                        params.append(ident.text.decode())
                        continue
                # Try pointer_declarator (int* ptr)
                ptr = next((c for c in child.children if c.type == "pointer_declarator"), None)
                if ptr:
                    ident = next((c for c in ptr.children if c.type == "identifier"), None)
                    if ident:
                        params.append(ident.text.decode())
                        continue
                # Direct identifier (e.g. last identifier in param)
                idents = [c for c in child.children if c.type == "identifier"]
                if idents:
                    params.append(idents[-1].text.decode())
                    continue
                # Fallback: full text
                text = child.text.decode().strip()
                if text:
                    params.append(text)
        return params if params else None

    def _is_global_variable(self, node) -> bool:
        """Check if a declaration node is a global variable."""
        parent = node.parent
        while parent:
            if parent.type in ["function_definition", "class_specifier", "struct_specifier"]:
                return False
            parent = parent.parent
        return True

    def _find_containing_class_for_method(self, node):
        """Find the class that contains this method definition."""
        current = node.parent
        while current:
            if current.type == "class_specifier":
                # Get class name
                for child in current.children:
                    if child.type == "type_identifier":
                        return child.text.decode()
            elif current.type == "struct_specifier":
                # Get struct name
                for child in current.children:
                    if child.type == "type_identifier":
                        return child.text.decode()
            current = current.parent
        return None

    def _extract_relationships(self, root, top_level_nodes):
        """Extract relationships using an iterative DFS to avoid recursion limits."""
        stack = [root]
        while stack:
            node = stack.pop()

            if node.type == "call_expression":
                containing_function = self._find_containing_function_or_method(
                    node, top_level_nodes
                )
                if containing_function:
                    containing_function_id = self._get_component_id_for_function(
                        containing_function, top_level_nodes
                    )

                    called_function = None
                    for child in node.children:
                        if child.type == "identifier":
                            called_function = child.text.decode()
                            break
                        elif child.type == "field_expression":
                            method_name = None
                            for field_child in child.children:
                                if field_child.type == "field_identifier":
                                    method_name = field_child.text.decode()
                                    break
                            if method_name:
                                called_function = method_name
                                break
                        elif child.type == "qualified_identifier":

                            def _extract_from_qualified(qnode):
                                last_child = qnode.children[-1] if qnode.children else None
                                if last_child is None:
                                    return None
                                if last_child.type == "identifier":
                                    return last_child.text.decode()
                                if last_child.type == "template_function":
                                    ident = next(
                                        (c for c in last_child.children if c.type == "identifier"),
                                        None,
                                    )
                                    return ident.text.decode() if ident else None
                                if last_child.type == "qualified_identifier":
                                    return _extract_from_qualified(last_child)
                                return None

                            qname = _extract_from_qualified(child)
                            if qname:
                                called_function = qname
                                break
                        elif child.type == "template_function":
                            ident = next(
                                (c for c in child.children if c.type == "identifier"), None
                            )
                            if ident:
                                called_function = ident.text.decode()
                                break

                    if called_function and not self._is_system_function(called_function):
                        target_class = self._find_class_containing_method(
                            called_function, top_level_nodes
                        )
                        if target_class:
                            target_class_id = self._get_component_id(target_class)
                            self.call_relationships.append(
                                CallRelationship(
                                    caller=containing_function_id,
                                    callee=target_class_id,
                                    call_line=node.start_point[0] + 1,
                                    relationship_type="calls",
                                )
                            )
                        elif called_function in top_level_nodes:
                            called_function_id = self._get_component_id(called_function)
                            self.call_relationships.append(
                                CallRelationship(
                                    caller=containing_function_id,
                                    callee=called_function_id,
                                    call_line=node.start_point[0] + 1,
                                    relationship_type="calls",
                                )
                            )

            elif node.type == "base_class_clause":
                containing_class = self._find_containing_class(node)
                if containing_class:
                    for child in node.children:
                        if child.type == "type_identifier":
                            base_class = child.text.decode()
                            containing_class_id = self._get_component_id(containing_class)
                            self.call_relationships.append(
                                CallRelationship(
                                    caller=containing_class_id,
                                    callee=base_class,
                                    call_line=node.start_point[0] + 1,
                                    relationship_type="inherits",
                                )
                            )

            elif node.type == "new_expression":
                containing_function = self._find_containing_function_or_method(
                    node, top_level_nodes
                )
                if containing_function:
                    containing_function_id = self._get_component_id_for_function(
                        containing_function, top_level_nodes
                    )
                    for child in node.children:
                        if child.type == "type_identifier":
                            class_name = child.text.decode()
                            if class_name in top_level_nodes:
                                class_id = self._get_component_id(class_name)
                                self.call_relationships.append(
                                    CallRelationship(
                                        caller=containing_function_id,
                                        callee=class_id,
                                        call_line=node.start_point[0] + 1,
                                        relationship_type="creates",
                                    )
                                )
                            break

            elif node.type == "identifier":
                parent = node.parent
                if parent and parent.type not in [
                    "function_definition",
                    "class_specifier",
                    "declaration",
                    "function_declarator",
                ]:
                    var_name = node.text.decode()
                    if (
                        var_name in top_level_nodes
                        and top_level_nodes[var_name].component_type == "variable"
                    ):
                        containing_function = self._find_containing_function_or_method(
                            node, top_level_nodes
                        )
                        if containing_function and containing_function != var_name:
                            containing_function_id = self._get_component_id_for_function(
                                containing_function, top_level_nodes
                            )
                            self.call_relationships.append(
                                CallRelationship(
                                    caller=containing_function_id,
                                    callee=var_name,
                                    call_line=node.start_point[0] + 1,
                                    relationship_type="uses",
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

    def _find_containing_function_or_method(self, node, top_level_nodes):
        """Find the function or method that contains this node."""
        current = node.parent
        while current:
            if current.type == "function_definition":
                declarator = next(
                    (c for c in current.children if c.type == "function_declarator"), None
                )
                if declarator:
                    identifier = next(
                        (c for c in declarator.children if c.type == "identifier"), None
                    )
                    if identifier:
                        func_name = identifier.text.decode()
                        return func_name
            current = current.parent
        return None

    def _get_component_id_for_function(self, func_name, top_level_nodes):
        if func_name in top_level_nodes:
            node_obj = top_level_nodes[func_name]
            if hasattr(node_obj, "class_name") and node_obj.class_name:
                return self._get_component_id(func_name, node_obj.class_name)
            else:
                return self._get_component_id(func_name)
        return self._get_component_id(func_name)

    def _find_containing_class(self, node):
        """Find the class that contains this node."""
        current = node.parent
        while current:
            if current.type == "class_specifier":
                # Get class name
                for child in current.children:
                    if child.type == "type_identifier":
                        return child.text.decode()
            current = current.parent
        return None

    def _is_system_function(self, func_name: str) -> bool:
        """Check if function is a system/library function."""
        system_functions = {
            "printf",
            "scanf",
            "malloc",
            "free",
            "strlen",
            "strcpy",
            "strcmp",
            "cout",
            "cin",
            "endl",
            "std",
            "new",
            "delete",
        }
        return func_name in system_functions

    def _find_class_containing_method(self, method_name, top_level_nodes):
        for node_name, node_obj in top_level_nodes.items():
            if node_obj.component_type in ["class", "struct"]:
                if self._class_has_method(node_obj, method_name):
                    return node_name
        return None

    def _class_has_method(self, class_node, method_name):
        lines = class_node.source_code.split("\n")
        for line in lines:
            if f"{method_name}(" in line and (
                "void" in line or "int" in line or "bool" in line or class_node.name in line
            ):
                return True
        return False


def analyze_cpp_file(
    file_path: str, content: str, repo_path: str | None = None
) -> Tuple[List[Node], List[CallRelationship]]:
    analyzer = TreeSitterCppAnalyzer(file_path, content, repo_path)
    return analyzer.nodes, analyzer.call_relationships
