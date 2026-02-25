import logging
from typing import List, Optional, Tuple
from pathlib import Path
import os

from tree_sitter import Parser, Language
import tree_sitter_rust
from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship

logger = logging.getLogger(__name__)


class TreeSitterRustAnalyzer:
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

        if rel_path.endswith(".rs"):
            rel_path = rel_path[:-3]
        return rel_path.replace("/", ".").replace("\\", ".")

    def _get_relative_path(self) -> str:
        if self.repo_path:
            try:
                return os.path.relpath(str(self.file_path), self.repo_path)
            except ValueError:
                return str(self.file_path)
        return str(self.file_path)

    def _get_component_id(self, name: str, impl_type: str = None) -> str:
        module_path = self._get_module_path()
        if impl_type:
            return f"{module_path}.{impl_type}.{name}"
        return f"{module_path}.{name}"

    def _node_text(self, node) -> str:
        return node.text.decode("utf8")

    def _analyze(self):
        language_capsule = tree_sitter_rust.language()
        lang = Language(language_capsule)
        parser = Parser(lang)
        tree = parser.parse(bytes(self.content, "utf8"))
        root = tree.root_node
        lines = self.content.splitlines()

        top_level_nodes = {}
        self._extract_nodes(root, top_level_nodes, lines, impl_type=None)
        self._extract_relationships(root, top_level_nodes)

    def _extract_nodes(self, node, top_level_nodes, lines, impl_type: Optional[str]):
        if node.type == "function_item":
            name_node = next((c for c in node.children if c.type == "identifier"), None)
            if name_node:
                fn_name = self._node_text(name_node)
                params = self._extract_params(node)
                if impl_type:
                    component_name = f"{impl_type}.{fn_name}"
                    node_type = "method"
                else:
                    component_name = fn_name
                    node_type = "function"
                component_id = self._get_component_id(fn_name, impl_type)
                node_obj = Node(
                    id=component_id,
                    name=component_name,
                    component_type=node_type,
                    file_path=str(self.file_path),
                    relative_path=self._get_relative_path(),
                    source_code="\n".join(lines[node.start_point[0]:node.end_point[0] + 1]),
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    has_docstring=False,
                    docstring="",
                    parameters=params,
                    node_type=node_type,
                    base_classes=None,
                    class_name=impl_type,
                    display_name=f"{node_type} {component_name}",
                    component_id=component_id,
                )
                self.nodes.append(node_obj)
                top_level_nodes[component_name] = node_obj
            # Don't recurse into the function body for node extraction
            return

        elif node.type == "struct_item":
            name_node = next((c for c in node.children if c.type == "type_identifier"), None)
            if name_node:
                name = self._node_text(name_node)
                component_id = self._get_component_id(name)
                node_obj = Node(
                    id=component_id,
                    name=name,
                    component_type="struct",
                    file_path=str(self.file_path),
                    relative_path=self._get_relative_path(),
                    source_code="\n".join(lines[node.start_point[0]:node.end_point[0] + 1]),
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    has_docstring=False,
                    docstring="",
                    parameters=None,
                    node_type="struct",
                    base_classes=None,
                    class_name=None,
                    display_name=f"struct {name}",
                    component_id=component_id,
                )
                self.nodes.append(node_obj)
                top_level_nodes[name] = node_obj

        elif node.type == "enum_item":
            name_node = next((c for c in node.children if c.type == "type_identifier"), None)
            if name_node:
                name = self._node_text(name_node)
                component_id = self._get_component_id(name)
                node_obj = Node(
                    id=component_id,
                    name=name,
                    component_type="enum",
                    file_path=str(self.file_path),
                    relative_path=self._get_relative_path(),
                    source_code="\n".join(lines[node.start_point[0]:node.end_point[0] + 1]),
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    has_docstring=False,
                    docstring="",
                    parameters=None,
                    node_type="enum",
                    base_classes=None,
                    class_name=None,
                    display_name=f"enum {name}",
                    component_id=component_id,
                )
                self.nodes.append(node_obj)
                top_level_nodes[name] = node_obj

        elif node.type == "trait_item":
            name_node = next((c for c in node.children if c.type == "type_identifier"), None)
            if name_node:
                name = self._node_text(name_node)
                component_id = self._get_component_id(name)
                node_obj = Node(
                    id=component_id,
                    name=name,
                    component_type="trait",
                    file_path=str(self.file_path),
                    relative_path=self._get_relative_path(),
                    source_code="\n".join(lines[node.start_point[0]:node.end_point[0] + 1]),
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    has_docstring=False,
                    docstring="",
                    parameters=None,
                    node_type="trait",
                    base_classes=None,
                    class_name=None,
                    display_name=f"trait {name}",
                    component_id=component_id,
                )
                self.nodes.append(node_obj)
                top_level_nodes[name] = node_obj
            # Extract default methods inside trait
            for child in node.children:
                if child.type == "declaration_list":
                    for trait_child in child.children:
                        self._extract_nodes(trait_child, top_level_nodes, lines,
                                            impl_type=self._node_text(name_node) if name_node else None)
            return

        elif node.type == "impl_item":
            # `impl [Trait for] Type { ... }`
            # The implemented type is the last type_identifier before the declaration_list
            type_nodes = [c for c in node.children if c.type == "type_identifier"]
            impl_type_name = self._node_text(type_nodes[-1]) if type_nodes else None

            # Find trait being implemented (if any) for base_classes
            trait_type = self._node_text(type_nodes[0]) if len(type_nodes) >= 2 else None

            if impl_type_name and impl_type_name in top_level_nodes:
                if trait_type:
                    top_level_nodes[impl_type_name].base_classes = (
                        top_level_nodes[impl_type_name].base_classes or []
                    ) + [trait_type]

            for child in node.children:
                if child.type == "declaration_list":
                    for impl_child in child.children:
                        self._extract_nodes(impl_child, top_level_nodes, lines, impl_type=impl_type_name)
            return

        for child in node.children:
            self._extract_nodes(child, top_level_nodes, lines, impl_type=impl_type)

    def _extract_params(self, fn_node) -> List[str]:
        params_node = next((c for c in fn_node.children if c.type == "parameters"), None)
        if not params_node:
            return []
        params = []
        for child in params_node.children:
            if child.type == "parameter":
                pat_node = next((c for c in child.children if c.type in ["identifier", "pattern"]), None)
                if pat_node:
                    params.append(self._node_text(pat_node))
            elif child.type == "self_parameter":
                params.append("self")
        return params

    def _extract_relationships(self, node, top_level_nodes):
        if node.type == "call_expression":
            fn_node = node.children[0] if node.children else None
            if fn_node:
                caller_id = self._find_containing_fn(node, top_level_nodes)
                if caller_id:
                    callee_name = self._resolve_call_target(fn_node)
                    if callee_name and not self._is_builtin(callee_name):
                        self.call_relationships.append(CallRelationship(
                            caller=caller_id,
                            callee=callee_name,
                            call_line=node.start_point[0] + 1,
                            is_resolved=False,
                        ))

        elif node.type == "impl_item":
            # impl Trait for Type → Type depends on Trait
            type_nodes = [c for c in node.children if c.type == "type_identifier"]
            if len(type_nodes) >= 2:
                impl_type = self._node_text(type_nodes[-1])
                trait_name = self._node_text(type_nodes[0])
                if not self._is_builtin(trait_name):
                    module_path = self._get_module_path()
                    self.call_relationships.append(CallRelationship(
                        caller=f"{module_path}.{impl_type}",
                        callee=trait_name,
                        call_line=node.start_point[0] + 1,
                        is_resolved=False,
                    ))

        for child in node.children:
            self._extract_relationships(child, top_level_nodes)

    def _resolve_call_target(self, fn_node) -> Optional[str]:
        """Extract callee name from function node in a call expression."""
        if fn_node.type == "identifier":
            return self._node_text(fn_node)
        elif fn_node.type == "field_expression":
            # obj.method  — grab just the method field identifier
            field_node = next((c for c in fn_node.children if c.type == "field_identifier"), None)
            return self._node_text(field_node) if field_node else None
        elif fn_node.type == "scoped_identifier":
            # path::to::func
            return self._node_text(fn_node).split("::")[-1]
        return None

    def _find_containing_fn(self, node, top_level_nodes) -> Optional[str]:
        current = node.parent
        while current:
            if current.type == "function_item":
                name_node = next((c for c in current.children if c.type == "identifier"), None)
                if name_node:
                    fn_name = self._node_text(name_node)
                    # Check if inside an impl block
                    impl_type = self._find_containing_impl_type(current)
                    return self._get_component_id(fn_name, impl_type)
            current = current.parent
        return None

    def _find_containing_impl_type(self, node) -> Optional[str]:
        current = node.parent
        while current:
            if current.type == "impl_item":
                type_nodes = [c for c in current.children if c.type == "type_identifier"]
                if type_nodes:
                    return self._node_text(type_nodes[-1])
            current = current.parent
        return None

    def _is_builtin(self, name: str) -> bool:
        builtins = {
            "println", "print", "eprintln", "eprint", "format", "vec", "panic",
            "assert", "assert_eq", "assert_ne", "unwrap", "expect", "clone",
            "to_string", "into", "from", "new", "default", "len", "push",
            "pop", "iter", "map", "filter", "collect", "some", "none", "ok", "err",
            "Box", "Vec", "String", "Option", "Result", "HashMap", "HashSet",
            "drop", "clone", "copy",
        }
        return name in builtins


def analyze_rust_file(
    file_path: str,
    content: str,
    repo_path: str = None,
) -> Tuple[List[Node], List[CallRelationship]]:
    analyzer = TreeSitterRustAnalyzer(file_path, content, repo_path)
    return analyzer.nodes, analyzer.call_relationships
