"""Enhanced Python adapter: extracts methods, imports, visibility, and signatures."""

import ast
import hashlib
import os
import warnings
from typing import TYPE_CHECKING, Optional

from codewiki.src.be.index.models import (
    Symbol,
    ImportStatement,
    SymbolKind,
    Visibility,
    ExportStatus,
    SourceRange,
    SymbolEdge,
    EdgeType,
    Confidence,
)

if TYPE_CHECKING:
    from codewiki.src.be.index.import_graph import ImportGraph
    from codewiki.src.be.index.symbol_table import SymbolTable

# Builtins that should never appear as CALLS edges.
_PYTHON_BUILTINS: frozenset[str] = frozenset(
    {
        "print",
        "len",
        "range",
        "type",
        "isinstance",
        "issubclass",
        "int",
        "str",
        "float",
        "bool",
        "list",
        "dict",
        "set",
        "tuple",
        "bytes",
        "bytearray",
        "memoryview",
        "object",
        "super",
        "getattr",
        "setattr",
        "hasattr",
        "delattr",
        "property",
        "staticmethod",
        "classmethod",
        "abs",
        "max",
        "min",
        "sum",
        "sorted",
        "reversed",
        "enumerate",
        "zip",
        "map",
        "filter",
        "any",
        "all",
        "id",
        "hash",
        "repr",
        "format",
        "open",
        "input",
        "round",
        "pow",
        "divmod",
        "chr",
        "ord",
        "hex",
        "oct",
        "bin",
        "iter",
        "next",
        "callable",
        "vars",
        "dir",
        "ValueError",
        "TypeError",
        "KeyError",
        "IndexError",
        "AttributeError",
        "RuntimeError",
        "StopIteration",
        "Exception",
        "NotImplementedError",
    }
)


class PythonIndexAdapter:
    """Parses a Python file and produces Symbol + ImportStatement objects."""

    def __init__(self, file_path: str, content: str, repo_path: str):
        self.file_path = file_path
        self.content = content
        self.repo_path = repo_path
        self.lines = content.splitlines()

        # Compute relative path once
        self._rel_path = os.path.relpath(file_path, repo_path).replace("\\", "/")
        self._module_path = self._rel_path
        for ext in (".py", ".pyx"):
            if self._module_path.endswith(ext):
                self._module_path = self._module_path[: -len(ext)]
                break
        self._module_path = self._module_path.replace("/", ".")

        self._symbols: list[Symbol] = []
        self._imports: list[ImportStatement] = []
        self._all_names: set[str] | None = None  # from __all__

    def extract(self) -> tuple[list[Symbol], list[ImportStatement]]:
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=SyntaxWarning)
                tree = ast.parse(self.content)
        except SyntaxError:
            self._tree = None
            return [], []

        # Save the AST for reuse in extract_calls()
        self._tree: ast.Module | None = tree

        # First pass: find __all__
        self._all_names = self._find_dunder_all(tree)

        # Walk top-level nodes
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                self._visit_class(node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._visit_function(node, parent_class=None)
            elif isinstance(node, ast.Import):
                self._visit_import(node)
            elif isinstance(node, ast.ImportFrom):
                self._visit_import_from(node)

        return self._symbols, self._imports

    def _find_dunder_all(self, tree: ast.Module) -> set[str] | None:
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        if isinstance(node.value, (ast.List, ast.Tuple)):
                            names = set()
                            for elt in node.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    names.add(elt.value)
                            return names
        return None

    def _make_symbol_id(self, name: str, kind: SymbolKind, class_name: str | None = None) -> str:
        if class_name:
            return f"py:{self._rel_path}#{class_name}.{name}({kind.value})"
        return f"py:{self._rel_path}#{name}({kind.value})"

    def _make_range(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    ) -> SourceRange:
        return SourceRange(
            file_path=self._rel_path,
            start_line=node.lineno,
            start_col=node.col_offset,
            end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
            end_col=getattr(node, "end_col_offset", 0) or 0,
        )

    def _source_hash(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
        start = node.lineno - 1
        end = getattr(node, "end_lineno", node.lineno) or node.lineno
        snippet = "\n".join(self.lines[start:end])
        return hashlib.sha256(snippet.encode()).hexdigest()[:16]

    def _visibility_for(self, name: str) -> Visibility:
        if name.startswith("__") and not name.endswith("__"):
            return Visibility.PRIVATE
        if name.startswith("_"):
            return Visibility.PRIVATE
        return Visibility.PUBLIC

    def _export_status_for(self, name: str) -> ExportStatus:
        if self._all_names is None:
            return ExportStatus.UNKNOWN
        if name in self._all_names:
            return ExportStatus.EXPORTED
        return ExportStatus.NOT_EXPORTED

    def _extract_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        parts = []
        args = node.args

        # Regular args
        all_args = args.args
        defaults_offset = len(all_args) - len(args.defaults)

        for i, arg in enumerate(all_args):
            if arg.arg == "self" or arg.arg == "cls":
                continue
            p = arg.arg
            if arg.annotation:
                p += f": {ast.unparse(arg.annotation)}"
            default_idx = i - defaults_offset
            if default_idx >= 0 and default_idx < len(args.defaults):
                p += f" = {ast.unparse(args.defaults[default_idx])}"
            parts.append(p)

        # *args
        if args.vararg:
            p = f"*{args.vararg.arg}"
            if args.vararg.annotation:
                p += f": {ast.unparse(args.vararg.annotation)}"
            parts.append(p)

        # Keyword-only args (after * or *args)
        if args.kwonlyargs:
            # If there is no *args but there are kwonlyargs, emit a bare * separator
            if not args.vararg:
                parts.append("*")
            for i, arg in enumerate(args.kwonlyargs):
                p = arg.arg
                if arg.annotation:
                    p += f": {ast.unparse(arg.annotation)}"
                default_value = args.kw_defaults[i] if i < len(args.kw_defaults) else None
                if default_value is not None:
                    p += f" = {ast.unparse(default_value)}"
                parts.append(p)

        # **kwargs
        if args.kwarg:
            p = f"**{args.kwarg.arg}"
            if args.kwarg.annotation:
                p += f": {ast.unparse(args.kwarg.annotation)}"
            parts.append(p)

        sig = f"{node.name}({', '.join(parts)})"
        if node.returns:
            sig += f" -> {ast.unparse(node.returns)}"
        return sig

    def _visit_class(self, node: ast.ClassDef):
        sid = self._make_symbol_id(node.name, SymbolKind.CLASS)
        qname = f"{self._module_path}.{node.name}"

        child_ids = []
        # Extract methods
        for item in ast.iter_child_nodes(node):
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                child_sym = self._visit_function(item, parent_class=node.name)
                if child_sym:
                    child_ids.append(child_sym.symbol_id)

        sym = Symbol(
            symbol_id=sid,
            lang="python",
            kind=SymbolKind.CLASS,
            name=node.name,
            qualified_name=qname,
            file_path=self._rel_path,
            range=self._make_range(node),
            signature=f"class {node.name}"
            + (f"({', '.join(ast.unparse(b) for b in node.bases)})" if node.bases else ""),
            visibility=self._visibility_for(node.name),
            export_status=self._export_status_for(node.name),
            docstring=ast.get_docstring(node),
            children=child_ids,
            source_hash=self._source_hash(node),
        )
        self._symbols.append(sym)

    def _visit_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, parent_class: str | None
    ) -> Optional[Symbol]:
        if parent_class:
            kind = SymbolKind.METHOD
            sid = self._make_symbol_id(node.name, kind, class_name=parent_class)
            qname = f"{self._module_path}.{parent_class}.{node.name}"
            parent_sid = self._make_symbol_id(parent_class, SymbolKind.CLASS)
        else:
            kind = SymbolKind.FUNCTION
            sid = self._make_symbol_id(node.name, kind)
            qname = f"{self._module_path}.{node.name}"
            parent_sid = None

        sym = Symbol(
            symbol_id=sid,
            lang="python",
            kind=kind,
            name=node.name,
            qualified_name=qname,
            file_path=self._rel_path,
            range=self._make_range(node),
            signature=self._extract_signature(node),
            visibility=self._visibility_for(node.name),
            export_status=self._export_status_for(node.name)
            if not parent_class
            else ExportStatus.UNKNOWN,
            docstring=ast.get_docstring(node),
            parent_symbol_id=parent_sid,
            source_hash=self._source_hash(node),
        )
        self._symbols.append(sym)
        return sym

    # ── CALLS edge extraction ────────────────────────────────────────────────

    def extract_calls(self, symbol_table, import_graph) -> list[SymbolEdge]:
        """Walk the stored AST and emit CALLS edges for every call site.

        Must be called after extract() has been run so that self._tree and
        self._symbols are populated.

        Args:
            symbol_table: SymbolTable built from all files in the repo.
            import_graph: ImportGraph built from all files in the repo.

        Returns:
            A list of SymbolEdge objects with edge_type=CALLS.
        """
        if not getattr(self, "_tree", None):
            return []

        edges: list[SymbolEdge] = []

        # Build (name → Symbol) map restricted to this file for same-file resolution.
        file_symbols_by_name: dict[str, Symbol] = {
            s.name: s for s in symbol_table.by_file(self._rel_path)
        }
        assert self._tree is not None

        for func_node in ast.walk(self._tree):
            if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            from_sym = self._resolve_enclosing_symbol(func_node)
            if from_sym is None:
                continue

            # Determine parent class name if method lives inside a class.
            parent_class_name = self._parent_class_name(func_node)

            for call_node in self._iter_calls_shallow(func_node):
                # call_node is an ast.Call not nested inside a child function

                callee_name, is_self_call = self._extract_callee_name(call_node)
                if callee_name is None:
                    continue

                # Filter Python builtins
                simple_name = callee_name.split(".")[-1]
                if callee_name in _PYTHON_BUILTINS or simple_name in _PYTHON_BUILTINS:
                    continue

                evidence = SourceRange(
                    file_path=self._rel_path,
                    start_line=call_node.lineno,
                    start_col=call_node.col_offset,
                    end_line=getattr(call_node, "end_lineno", call_node.lineno) or call_node.lineno,
                    end_col=getattr(call_node, "end_col_offset", 0) or 0,
                )

                to_sym, confidence = self._resolve_callee(
                    callee_name=callee_name,
                    is_self_call=is_self_call,
                    parent_class_name=parent_class_name,
                    file_symbols_by_name=file_symbols_by_name,
                    symbol_table=symbol_table,
                    import_graph=import_graph,
                )

                edges.append(
                    SymbolEdge(
                        edge_type=EdgeType.CALLS,
                        from_symbol=from_sym.symbol_id,
                        to_symbol=to_sym.symbol_id if to_sym else None,
                        to_unresolved=callee_name if not to_sym else None,
                        evidence_refs=[evidence],
                        confidence=confidence,
                        resolver="ast",
                    )
                )

        return edges

    def _iter_calls_shallow(self, func_node: ast.FunctionDef | ast.AsyncFunctionDef):
        """Yield all ast.Call nodes directly inside func_node's body.

        Unlike ast.walk, this does NOT descend into nested function/class
        definitions, so each call site is attributed only to the immediately
        enclosing function.
        """
        # We do a manual BFS/DFS, skipping nested FunctionDef/AsyncFunctionDef.
        stack = list(ast.iter_child_nodes(func_node))
        while stack:
            node = stack.pop()
            if isinstance(node, ast.Call):
                yield node
                # Still descend into the Call's sub-expressions (args, func)
                # so that foo(bar()) yields both foo and bar calls.
                stack.extend(ast.iter_child_nodes(node))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # Stop descent into nested scopes — they will be handled as
                # separate func_node iterations at the outer loop.
                continue
            else:
                stack.extend(ast.iter_child_nodes(node))

    def _resolve_enclosing_symbol(
        self, func_node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> Optional["Symbol"]:
        """Match an AST function node back to its Symbol in self._symbols."""
        for sym in self._symbols:
            if sym.kind not in (SymbolKind.FUNCTION, SymbolKind.METHOD):
                continue
            if sym.name == func_node.name and sym.range.start_line == func_node.lineno:
                return sym
        return None

    def _parent_class_name(
        self, func_node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> Optional[str]:
        """Return the class name that directly contains func_node, or None."""
        assert self._tree is not None
        for class_node in ast.walk(self._tree):
            if not isinstance(class_node, ast.ClassDef):
                continue
            for item in ast.iter_child_nodes(class_node):
                if item is func_node:
                    return class_node.name
        return None

    def _extract_callee_name(self, call_node: ast.Call) -> tuple[Optional[str], bool]:
        """Return (callee_name, is_self_call) for a Call node.

        is_self_call is True when the callee is `self.<method>`, which enables
        same-class resolution.
        """
        func = call_node.func
        if isinstance(func, ast.Name):
            return func.id, False
        if isinstance(func, ast.Attribute):
            parts = self._unpack_attribute(func)
            if parts and parts[0] == "self":
                # self.method() → return just the method name for same-class lookup
                return ".".join(parts), True
            if parts:
                return ".".join(parts), False
        return None, False

    def _unpack_attribute(self, node: ast.Attribute) -> list[str]:
        """Unpack a.b.c into ['a', 'b', 'c']."""
        parts: list[str] = []
        current: ast.expr = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        else:
            # Complex expression — cannot extract a reliable name
            return []
        parts.reverse()
        return parts

    def _resolve_callee(
        self,
        callee_name: str,
        is_self_call: bool,
        parent_class_name: Optional[str],
        file_symbols_by_name: dict[str, "Symbol"],
        symbol_table: "SymbolTable",
        import_graph: "ImportGraph",
    ) -> tuple[Optional["Symbol"], Confidence]:
        """Resolve a callee name to a Symbol and return (symbol_or_None, confidence)."""
        # --- self.method() resolution: look for same-class sibling method ---
        if is_self_call and parent_class_name:
            # callee_name is like "self.helper" — extract the method name
            method_name = callee_name.split(".")[-1]
            for sym in symbol_table.by_file(self._rel_path):
                if sym.kind == SymbolKind.METHOD and sym.name == method_name:
                    # Verify it belongs to the same class via parent_symbol_id
                    parent = (
                        symbol_table.get(sym.parent_symbol_id) if sym.parent_symbol_id else None
                    )
                    if parent and parent.name == parent_class_name:
                        return sym, Confidence.HIGH

        # --- Simple name: same-file lookup ---
        simple_name = callee_name.split(".")[-1] if "." in callee_name else callee_name
        if not is_self_call:
            # Try exact name match first (functions, classes in same file)
            sym = file_symbols_by_name.get(simple_name)
            if sym is not None:
                return sym, Confidence.HIGH

        # --- Imported symbol resolution ---
        if not is_self_call:
            # For dotted names like "module.func", use the last part as the imported name
            resolved = import_graph.resolve(self._rel_path, simple_name, symbol_table)
            if resolved is not None:
                return resolved, Confidence.HIGH

        # --- Unresolved ---
        return None, Confidence.LOW

    def _resolve_import_path(self, module_path: str, level: int) -> Optional[str]:
        """Resolve a Python import to a repo-relative file path, or None if external.

        Args:
            module_path: The dotted module name (e.g. "pkg.utils" or "utils").
            level: Number of leading dots for relative imports (0 = absolute).

        Returns:
            A forward-slash repo-relative path (e.g. "src/pkg/utils.py") if the
            target file exists on disk, otherwise None.
        """
        if level > 0:
            # Relative import: walk up `level` directories from the current file's dir.
            # level=1 means same package (stay in current dir),
            # level=2 means parent package (go up one), etc.
            base_dir = os.path.dirname(self.file_path)
            for _ in range(level - 1):
                base_dir = os.path.dirname(base_dir)

            if module_path:
                candidate_base = os.path.join(base_dir, module_path.replace(".", os.sep))
            else:
                # `from . import foo` — no module component; base_dir is the package dir
                candidate_base = base_dir
        else:
            # Absolute import: resolve from repo root
            if not module_path:
                return None
            candidate_base = os.path.join(self.repo_path, module_path.replace(".", os.sep))

        # Try <base>.py first, then <base>/__init__.py (package)
        for candidate in (candidate_base + ".py", os.path.join(candidate_base, "__init__.py")):
            if os.path.isfile(candidate):
                rel = os.path.relpath(candidate, self.repo_path)
                return rel.replace("\\", "/")

        return None

    def _visit_import(self, node: ast.Import):
        for alias in node.names:
            resolved = self._resolve_import_path(alias.name, level=0)
            self._imports.append(
                ImportStatement(
                    file_path=self._rel_path,
                    module_path=alias.name,
                    imported_names=[],
                    alias=alias.asname,
                    resolved_path=resolved,
                    line=node.lineno,
                )
            )

    def _visit_import_from(self, node: ast.ImportFrom):
        module = node.module or ""
        level = node.level or 0
        # Encode relative imports: level dots + module
        prefix = "." * level
        module_path = prefix + module

        names = []
        for alias in node.names or []:
            if alias.name == "*":
                names = ["*"]
                break
            names.append(alias.name)

        # Single alias for `from X import Y as Z` (only when single name)
        alias = None
        if len(node.names) == 1 and node.names[0].asname:
            alias = node.names[0].asname

        resolved = self._resolve_import_path(module, level=level)

        self._imports.append(
            ImportStatement(
                file_path=self._rel_path,
                module_path=module_path,
                imported_names=names,
                alias=alias,
                resolved_path=resolved,
                is_reexport=False,
                line=node.lineno,
            )
        )
