"""TS/JS adapter: extracts classes, methods, functions, and imports via tree-sitter."""
import hashlib
import logging
import os
import threading
from typing import Optional

from tree_sitter import Parser, Language

from codewiki.src.be.index.models import (
    Symbol, ImportStatement, SymbolKind, Visibility, ExportStatus, SourceRange,
)

logger = logging.getLogger(__name__)

# ── Parser singletons (one per language) ─────────────────────────────────────

_TS_LANGUAGE: "Language | None" = None
_TS_LANGUAGE_LOCK = threading.Lock()
_TS_PARSER_LOCAL = threading.local()

_JS_LANGUAGE: "Language | None" = None
_JS_LANGUAGE_LOCK = threading.Lock()
_JS_PARSER_LOCAL = threading.local()


def _get_ts_parser() -> "Parser | None":
    global _TS_LANGUAGE
    try:
        if _TS_LANGUAGE is None:
            with _TS_LANGUAGE_LOCK:
                if _TS_LANGUAGE is None:
                    import tree_sitter_typescript
                    _TS_LANGUAGE = Language(tree_sitter_typescript.language_typescript())
        p = getattr(_TS_PARSER_LOCAL, "parser", None)
        if p is None:
            _TS_PARSER_LOCAL.parser = Parser(_TS_LANGUAGE)
        return _TS_PARSER_LOCAL.parser
    except Exception as e:
        logger.error(f"Failed to initialise TypeScript parser: {e}")
        return None


def _get_js_parser() -> "Parser | None":
    global _JS_LANGUAGE
    try:
        if _JS_LANGUAGE is None:
            with _JS_LANGUAGE_LOCK:
                if _JS_LANGUAGE is None:
                    import tree_sitter_javascript
                    _JS_LANGUAGE = Language(tree_sitter_javascript.language())
        p = getattr(_JS_PARSER_LOCAL, "parser", None)
        if p is None:
            _JS_PARSER_LOCAL.parser = Parser(_JS_LANGUAGE)
        return _JS_PARSER_LOCAL.parser
    except Exception as e:
        logger.error(f"Failed to initialise JavaScript parser: {e}")
        return None


class TSJSIndexAdapter:
    """Parses a TypeScript or JavaScript file and produces Symbol + ImportStatement objects."""

    def __init__(self, file_path: str, content: str, repo_path: str, language: str = "typescript"):
        self.file_path = file_path
        self.content = content
        self.repo_path = repo_path
        self.language = language  # "typescript" or "javascript"
        self.lines = content.splitlines()

        # Compute relative path once
        try:
            self._rel_path = os.path.relpath(file_path, repo_path).replace("\\", "/")
        except ValueError:
            self._rel_path = file_path

        # Language prefix for symbol IDs
        self._lang_prefix = "ts" if language == "typescript" else "js"

        self._symbols: list[Symbol] = []
        self._imports: list[ImportStatement] = []

    def extract(self) -> tuple[list[Symbol], list[ImportStatement]]:
        """Parse the file and return symbols and imports."""
        parser = _get_ts_parser() if self.language == "typescript" else _get_js_parser()
        if parser is None:
            return [], []

        try:
            tree = parser.parse(bytes(self.content, "utf8"))
            root = tree.root_node
            self._visit_node(root, parent_class_sym=None)
        except Exception as e:
            logger.error(f"Error parsing {self.file_path}: {e}", exc_info=True)

        return self._symbols, self._imports

    # ── AST traversal ──────────────────────────────────────────────────────

    def _visit_node(self, node, parent_class_sym: Optional[Symbol]) -> None:
        """Recursively visit AST nodes, extracting top-level declarations."""
        ntype = node.type

        if ntype == "export_statement":
            self._handle_export_statement(node)
            return  # children handled inside

        if ntype == "class_declaration":
            self._handle_class(node, exported=False)
            return

        if ntype == "function_declaration":
            self._handle_function(node, exported=False)
            return

        if ntype == "import_statement":
            self._handle_import(node)
            return

        # Recurse into other top-level nodes (program, module, etc.)
        for child in node.children:
            self._visit_node(child, parent_class_sym)

    def _handle_export_statement(self, node) -> None:
        """Handle `export class X {}` and `export function f() {}`."""
        for child in node.children:
            if child.type == "class_declaration":
                self._handle_class(child, exported=True)
                return
            if child.type == "function_declaration":
                self._handle_function(child, exported=True)
                return

    # ── Class handling ─────────────────────────────────────────────────────

    def _handle_class(self, node, exported: bool) -> None:
        name_node = self._find_child_by_type(node, "type_identifier") or \
                    self._find_child_by_type(node, "identifier")
        if name_node is None:
            return

        name = self._node_text(name_node)
        sid = self._make_symbol_id(name, SymbolKind.CLASS)

        # Extract methods first to collect child IDs
        child_ids: list[str] = []
        body = self._find_child_by_type(node, "class_body")
        if body:
            for item in body.children:
                if item.type == "method_definition":
                    child_sym = self._handle_method(item, class_name=name, class_sid=sid)
                    if child_sym:
                        child_ids.append(child_sym.symbol_id)

        sym = Symbol(
            symbol_id=sid,
            lang=self.language,
            kind=SymbolKind.CLASS,
            name=name,
            qualified_name=f"{self._rel_path}#{name}",
            file_path=self._rel_path,
            range=self._make_range(node),
            signature=f"class {name}",
            visibility=Visibility.PUBLIC,
            export_status=ExportStatus.EXPORTED if exported else ExportStatus.NOT_EXPORTED,
            children=child_ids,
            source_hash=self._source_hash(node),
        )
        self._symbols.append(sym)

    # ── Method handling ────────────────────────────────────────────────────

    def _handle_method(self, node, class_name: str, class_sid: str) -> Optional[Symbol]:
        """Extract a method_definition node inside a class body."""
        name_node = self._find_child_by_type(node, "property_identifier") or \
                    self._find_child_by_type(node, "identifier")
        if name_node is None:
            return None

        name = self._node_text(name_node)
        sid = self._make_symbol_id(f"{class_name}.{name}", SymbolKind.METHOD)

        sym = Symbol(
            symbol_id=sid,
            lang=self.language,
            kind=SymbolKind.METHOD,
            name=name,
            qualified_name=f"{self._rel_path}#{class_name}.{name}",
            file_path=self._rel_path,
            range=self._make_range(node),
            signature=f"{class_name}.{name}()",
            visibility=Visibility.PUBLIC,
            export_status=ExportStatus.UNKNOWN,
            parent_symbol_id=class_sid,
            source_hash=self._source_hash(node),
        )
        self._symbols.append(sym)
        return sym

    # ── Function handling ──────────────────────────────────────────────────

    def _handle_function(self, node, exported: bool) -> None:
        name_node = self._find_child_by_type(node, "identifier")
        if name_node is None:
            return

        name = self._node_text(name_node)
        sid = self._make_symbol_id(name, SymbolKind.FUNCTION)

        sym = Symbol(
            symbol_id=sid,
            lang=self.language,
            kind=SymbolKind.FUNCTION,
            name=name,
            qualified_name=f"{self._rel_path}#{name}",
            file_path=self._rel_path,
            range=self._make_range(node),
            signature=f"function {name}()",
            visibility=Visibility.PUBLIC,
            export_status=ExportStatus.EXPORTED if exported else ExportStatus.NOT_EXPORTED,
            source_hash=self._source_hash(node),
        )
        self._symbols.append(sym)

    # ── Import handling ────────────────────────────────────────────────────

    def _handle_import(self, node) -> None:
        """Handle import_statement nodes.

        Handles three forms:
          import { Foo, Bar } from './module';   → named imports
          import React from 'react';             → default import
          import * as path from 'path';          → namespace import
        """
        # Find the module path string (inside `from_clause` or directly)
        module_path = self._extract_import_source(node)
        if module_path is None:
            return

        line = node.start_point[0] + 1

        # Find the import_clause
        import_clause = self._find_child_by_type(node, "import_clause")
        if import_clause is None:
            # bare `import 'module'` — no names
            self._imports.append(ImportStatement(
                file_path=self._rel_path,
                module_path=module_path,
                imported_names=[],
                alias=None,
                line=line,
            ))
            return

        # Check for namespace import: `* as name`
        namespace_import = self._find_child_by_type(import_clause, "namespace_import")
        if namespace_import is not None:
            # `* as name` — find the identifier after `as`
            alias = self._extract_namespace_alias(namespace_import)
            self._imports.append(ImportStatement(
                file_path=self._rel_path,
                module_path=module_path,
                imported_names=["*"],
                alias=alias,
                line=line,
            ))
            return

        # Check for named imports: `{ Foo, Bar }`
        named_imports = self._find_child_by_type(import_clause, "named_imports")
        if named_imports is not None:
            names = self._extract_named_imports(named_imports)
            self._imports.append(ImportStatement(
                file_path=self._rel_path,
                module_path=module_path,
                imported_names=names,
                alias=None,
                line=line,
            ))
            return

        # Default import: `React` (identifier directly inside import_clause)
        default_name = None
        for child in import_clause.children:
            if child.type == "identifier":
                default_name = self._node_text(child)
                break

        self._imports.append(ImportStatement(
            file_path=self._rel_path,
            module_path=module_path,
            imported_names=[default_name] if default_name else [],
            alias=None,
            line=line,
        ))

    def _extract_import_source(self, import_node) -> Optional[str]:
        """Extract the module path string from an import_statement."""
        # Look for `from_clause` child first
        from_clause = self._find_child_by_type(import_node, "from_clause")
        if from_clause is not None:
            string_node = self._find_child_by_type(from_clause, "string")
            if string_node:
                return self._strip_quotes(self._node_text(string_node))

        # Fallback: string directly inside import_statement
        for child in import_node.children:
            if child.type == "string":
                return self._strip_quotes(self._node_text(child))

        return None

    def _extract_namespace_alias(self, namespace_import_node) -> Optional[str]:
        """Extract the alias identifier from a namespace_import node (`* as X`)."""
        for child in namespace_import_node.children:
            if child.type == "identifier":
                return self._node_text(child)
        return None

    def _extract_named_imports(self, named_imports_node) -> list[str]:
        """Extract list of names from a named_imports node (`{ Foo, Bar }`)."""
        names: list[str] = []
        for child in named_imports_node.children:
            if child.type == "import_specifier":
                # The first identifier in the specifier is the imported name
                for subchild in child.children:
                    if subchild.type == "identifier":
                        names.append(self._node_text(subchild))
                        break
        return names

    # ── Helpers ────────────────────────────────────────────────────────────

    def _make_symbol_id(self, qualified_name: str, kind: SymbolKind) -> str:
        return f"{self._lang_prefix}:{self._rel_path}#{qualified_name}({kind.value})"

    def _make_range(self, node) -> SourceRange:
        return SourceRange(
            file_path=self._rel_path,
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

    def _source_hash(self, node) -> str:
        start = node.start_point[0]
        end = node.end_point[0] + 1
        snippet = "\n".join(self.lines[start:end])
        return hashlib.sha256(snippet.encode()).hexdigest()[:16]

    def _find_child_by_type(self, node, node_type: str):
        for child in node.children:
            if child.type == node_type:
                return child
        return None

    def _node_text(self, node) -> str:
        start = node.start_byte
        end = node.end_byte
        return self.content.encode("utf8")[start:end].decode("utf8")

    @staticmethod
    def _strip_quotes(s: str) -> str:
        """Remove surrounding single or double quotes from a string literal."""
        if len(s) >= 2 and s[0] in ('"', "'") and s[-1] in ('"', "'"):
            return s[1:-1]
        return s
