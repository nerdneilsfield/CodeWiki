# Index Layer v1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an offline index layer (SymbolTable + ImportGraph + ComponentCards) that sits between raw AST analysis and LLM-driven documentation generation.

**Architecture:** New `codewiki/src/be/index/` package with Pydantic models, adapters that re-parse Python/TS/JS files for method-level symbols and imports, and an IndexBuilder orchestrator. All paths are relative to repo root. Unresolved references are preserved with confidence levels. LLM JSON parsed with `json_repair`.

**Tech Stack:** Python 3.12, Pydantic v2, ast (Python), tree-sitter (TS/JS), json_repair, pytest

---

### Task 1: Add json_repair dependency

**Files:**
- Modify: `pyproject.toml` — add `json_repair` to dependencies

**Step 1: Add dependency**

In `pyproject.toml`, add to the `dependencies` list:
```
"json_repair>=0.30.0",
```

Note: `pyproject.toml` already uses auto-discovery (`[tool.setuptools.packages.find]`
with `include = ["codewiki*"]`), so new packages under `codewiki/` are automatically
found when they have `__init__.py` — no manual package registration needed.

**Step 2: Install**

Run: `pip install -e .`
Expected: Installs json_repair along with other deps

**Step 3: Verify import works**

Run: `python -c "import json_repair; print(json_repair.loads('{\"a\": 1,}'))"`
Expected: `{'a': 1}`

**Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat(deps): add json_repair for robust LLM JSON parsing"
```

---

### Task 2: Index layer models

**Files:**
- Create: `codewiki/src/be/index/__init__.py`
- Create: `codewiki/src/be/index/models.py`
- Create: `tests/test_index_models.py`

**Step 1: Write failing tests for all model classes**

```python
# tests/test_index_models.py
"""Tests for index layer data models."""
import pytest
from codewiki.src.be.index.models import (
    SourceRange, SymbolKind, Visibility, ExportStatus, EdgeType, Confidence,
    Symbol, ImportStatement, SymbolEdge, ComponentCard,
)


# ── SourceRange ──────────────────────────────────────────────────────────────

def test_source_range_basic():
    r = SourceRange(file_path="src/main.py", start_line=10, start_col=0, end_line=20, end_col=1)
    assert r.file_path == "src/main.py"
    assert r.start_line == 10
    assert r.end_col == 1


# ── SymbolKind enum ──────────────────────────────────────────────────────────

def test_symbol_kind_values():
    assert SymbolKind.CLASS.value == "class"
    assert SymbolKind.METHOD.value == "method"
    assert SymbolKind.FUNCTION.value == "function"
    assert SymbolKind.INTERFACE.value == "interface"


# ── Symbol ───────────────────────────────────────────────────────────────────

def test_symbol_minimal():
    s = Symbol(
        symbol_id="py:src/a.py#Foo(class)",
        lang="python",
        kind=SymbolKind.CLASS,
        name="Foo",
        qualified_name="src.a.Foo",
        file_path="src/a.py",
        range=SourceRange(file_path="src/a.py", start_line=1, start_col=0, end_line=10, end_col=0),
        source_hash="abc123",
    )
    assert s.symbol_id == "py:src/a.py#Foo(class)"
    assert s.visibility == Visibility.UNKNOWN
    assert s.export_status == ExportStatus.UNKNOWN
    assert s.parent_symbol_id is None
    assert s.children == []
    assert s.signature is None
    assert s.docstring is None


def test_symbol_with_parent_and_children():
    s = Symbol(
        symbol_id="py:src/a.py#Foo.bar(method)",
        lang="python",
        kind=SymbolKind.METHOD,
        name="bar",
        qualified_name="src.a.Foo.bar",
        file_path="src/a.py",
        range=SourceRange(file_path="src/a.py", start_line=5, start_col=4, end_line=8, end_col=0),
        source_hash="def456",
        parent_symbol_id="py:src/a.py#Foo(class)",
        visibility=Visibility.PUBLIC,
    )
    assert s.parent_symbol_id == "py:src/a.py#Foo(class)"
    assert s.kind == SymbolKind.METHOD


def test_symbol_file_path_is_relative():
    """All file paths must be relative to repo root, never absolute."""
    s = Symbol(
        symbol_id="py:src/a.py#f(function)",
        lang="python",
        kind=SymbolKind.FUNCTION,
        name="f",
        qualified_name="src.a.f",
        file_path="src/a.py",
        range=SourceRange(file_path="src/a.py", start_line=1, start_col=0, end_line=1, end_col=0),
        source_hash="x",
    )
    assert not s.file_path.startswith("/")


# ── ImportStatement ──────────────────────────────────────────────────────────

def test_import_statement_basic():
    imp = ImportStatement(
        file_path="src/main.py",
        module_path="os.path",
        imported_names=["join", "dirname"],
        line=3,
    )
    assert imp.module_path == "os.path"
    assert imp.imported_names == ["join", "dirname"]
    assert imp.alias is None
    assert imp.resolved_path is None
    assert imp.is_reexport is False


def test_import_statement_with_alias():
    imp = ImportStatement(
        file_path="src/main.py",
        module_path="numpy",
        imported_names=[],
        alias="np",
        line=1,
    )
    assert imp.alias == "np"


def test_import_statement_relative():
    imp = ImportStatement(
        file_path="src/auth/login.py",
        module_path="..utils",
        imported_names=["helper"],
        resolved_path="src/utils.py",
        line=2,
    )
    assert imp.resolved_path == "src/utils.py"


# ── SymbolEdge ───────────────────────────────────────────────────────────────

def test_symbol_edge_resolved():
    e = SymbolEdge(
        edge_type=EdgeType.CALLS,
        from_symbol="py:src/a.py#f(function)",
        to_symbol="py:src/b.py#g(function)",
        evidence_refs=[
            SourceRange(file_path="src/a.py", start_line=5, start_col=4, end_line=5, end_col=10)
        ],
        confidence=Confidence.HIGH,
        resolver="ast",
    )
    assert e.to_symbol is not None
    assert e.to_unresolved is None


def test_symbol_edge_unresolved():
    e = SymbolEdge(
        edge_type=EdgeType.CALLS,
        from_symbol="py:src/a.py#f(function)",
        to_unresolved="some_lib.unknown_func",
        evidence_refs=[
            SourceRange(file_path="src/a.py", start_line=10, start_col=0, end_line=10, end_col=25)
        ],
        confidence=Confidence.LOW,
        resolver="heuristic",
    )
    assert e.to_symbol is None
    assert e.to_unresolved == "some_lib.unknown_func"


# ── ComponentCard ────────────────────────────────────────────────────────────

def test_component_card_basic():
    card = ComponentCard(
        symbol_id="py:src/a.py#Foo(class)",
        signature="class Foo(Base)",
        docstring_summary="A service for handling auth.",
        kind=SymbolKind.CLASS,
        key_edges=["imports: src.db.Connection", "calls: src.cache.get"],
        file_context="src/a.py (lines 1-50)",
    )
    assert card.symbol_id == "py:src/a.py#Foo(class)"
    assert len(card.key_edges) == 2
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_index_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codewiki.src.be.index'`

**Step 3: Create the package and models**

```python
# codewiki/src/be/index/__init__.py
"""Index layer: symbol table, import graph, and component cards."""
```

```python
# codewiki/src/be/index/models.py
"""Data models for the index layer.

All file_path fields are relative to the repository root — never absolute.
This ensures index products are portable across machines.
"""
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class SymbolKind(str, Enum):
    PACKAGE = "package"
    MODULE = "module"
    FILE = "file"
    CLASS = "class"
    INTERFACE = "interface"
    FUNCTION = "function"
    METHOD = "method"
    TYPE = "type"
    VARIABLE = "variable"
    CONSTANT = "constant"
    ENUM = "enum"
    STRUCT = "struct"
    TRAIT = "trait"


class Visibility(str, Enum):
    PUBLIC = "public"
    PROTECTED = "protected"
    PRIVATE = "private"
    INTERNAL = "internal"
    UNKNOWN = "unknown"


class ExportStatus(str, Enum):
    EXPORTED = "exported"
    NOT_EXPORTED = "not_exported"
    UNKNOWN = "unknown"


class EdgeType(str, Enum):
    IMPORTS = "imports"
    CALLS = "calls"
    EXTENDS = "extends"
    IMPLEMENTS = "implements"
    REFERENCES = "references"
    TESTS = "tests"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SourceRange(BaseModel):
    """A span in a source file. file_path is relative to repo root."""
    file_path: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int


class Symbol(BaseModel):
    """A code symbol with full metadata and evidence position."""
    symbol_id: str
    lang: str
    kind: SymbolKind
    name: str
    qualified_name: str
    file_path: str
    range: SourceRange
    signature: Optional[str] = None
    visibility: Visibility = Visibility.UNKNOWN
    export_status: ExportStatus = ExportStatus.UNKNOWN
    docstring: Optional[str] = None
    parent_symbol_id: Optional[str] = None
    children: list[str] = []
    source_hash: str


class ImportStatement(BaseModel):
    """A single import/require/use statement in a file."""
    file_path: str
    module_path: str
    imported_names: list[str] = []
    alias: Optional[str] = None
    resolved_path: Optional[str] = None
    is_reexport: bool = False
    line: int


class SymbolEdge(BaseModel):
    """A directed relationship between symbols, with evidence."""
    edge_type: EdgeType
    from_symbol: str
    to_symbol: Optional[str] = None
    to_unresolved: Optional[str] = None
    evidence_refs: list[SourceRange] = []
    confidence: Confidence = Confidence.MEDIUM
    resolver: str = "unknown"


class ComponentCard(BaseModel):
    """Lightweight symbol summary for LLM context packs."""
    symbol_id: str
    signature: str
    docstring_summary: str
    kind: SymbolKind
    key_edges: list[str] = []
    file_context: str
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_index_models.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/index/__init__.py codewiki/src/be/index/models.py tests/test_index_models.py
git commit -m "feat(index): add index layer data models (Symbol, ImportStatement, SymbolEdge, ComponentCard)"
```

---

### Task 3: SymbolTable

**Files:**
- Create: `codewiki/src/be/index/symbol_table.py`
- Create: `tests/test_index_symbol_table.py`

**Step 1: Write failing tests**

```python
# tests/test_index_symbol_table.py
"""Tests for SymbolTable lookups and invariants."""
import pytest
from codewiki.src.be.index.models import (
    Symbol, SymbolKind, Visibility, ExportStatus, SourceRange,
)
from codewiki.src.be.index.symbol_table import SymbolTable


def _make_symbol(sid, name, kind=SymbolKind.FUNCTION, file_path="src/a.py",
                 parent=None, visibility=Visibility.PUBLIC,
                 export_status=ExportStatus.UNKNOWN, qname=None):
    return Symbol(
        symbol_id=sid, lang="python", kind=kind, name=name,
        qualified_name=qname or f"src.a.{name}",
        file_path=file_path,
        range=SourceRange(file_path=file_path, start_line=1, start_col=0, end_line=10, end_col=0),
        source_hash="h",
        parent_symbol_id=parent,
        visibility=visibility,
        export_status=export_status,
    )


def test_get_existing_symbol():
    st = SymbolTable([_make_symbol("s1", "foo")])
    assert st.get("s1").name == "foo"


def test_get_missing_returns_none():
    st = SymbolTable([])
    assert st.get("nonexistent") is None


def test_by_file():
    s1 = _make_symbol("s1", "foo", file_path="src/a.py")
    s2 = _make_symbol("s2", "bar", file_path="src/b.py")
    s3 = _make_symbol("s3", "baz", file_path="src/a.py")
    st = SymbolTable([s1, s2, s3])
    result = st.by_file("src/a.py")
    assert len(result) == 2
    assert {s.name for s in result} == {"foo", "baz"}


def test_by_file_empty():
    st = SymbolTable([_make_symbol("s1", "foo", file_path="src/a.py")])
    assert st.by_file("src/nonexistent.py") == []


def test_by_qualified_name():
    s = _make_symbol("s1", "Foo", qname="src.auth.login.Foo")
    st = SymbolTable([s])
    assert st.by_qualified_name("src.auth.login.Foo").symbol_id == "s1"


def test_by_qualified_name_missing():
    st = SymbolTable([_make_symbol("s1", "Foo")])
    assert st.by_qualified_name("nonexistent") is None


def test_children_of():
    parent = _make_symbol("c1", "MyClass", kind=SymbolKind.CLASS)
    parent.children = ["m1", "m2"]
    m1 = _make_symbol("m1", "method_a", kind=SymbolKind.METHOD, parent="c1")
    m2 = _make_symbol("m2", "method_b", kind=SymbolKind.METHOD, parent="c1")
    st = SymbolTable([parent, m1, m2])
    children = st.children_of("c1")
    assert len(children) == 2
    assert {c.name for c in children} == {"method_a", "method_b"}


def test_children_of_no_children():
    s = _make_symbol("s1", "standalone")
    st = SymbolTable([s])
    assert st.children_of("s1") == []


def test_public_api():
    s1 = _make_symbol("s1", "pub", export_status=ExportStatus.EXPORTED)
    s2 = _make_symbol("s2", "priv", export_status=ExportStatus.NOT_EXPORTED)
    s3 = _make_symbol("s3", "unk", export_status=ExportStatus.UNKNOWN, visibility=Visibility.PUBLIC)
    st = SymbolTable([s1, s2, s3])
    api = st.public_api()
    ids = {s.symbol_id for s in api}
    assert "s1" in ids
    assert "s2" not in ids


def test_search_by_name():
    s1 = _make_symbol("s1", "FooService")
    s2 = _make_symbol("s2", "BarService")
    st = SymbolTable([s1, s2])
    results = st.search("Foo")
    assert any(s.name == "FooService" for s in results)
    assert not any(s.name == "BarService" for s in results)


def test_all_symbols():
    symbols = [_make_symbol(f"s{i}", f"sym{i}") for i in range(5)]
    st = SymbolTable(symbols)
    assert len(st.all_symbols()) == 5


def test_all_files():
    s1 = _make_symbol("s1", "a", file_path="src/a.py")
    s2 = _make_symbol("s2", "b", file_path="src/b.py")
    s3 = _make_symbol("s3", "c", file_path="src/a.py")
    st = SymbolTable([s1, s2, s3])
    assert st.all_files() == {"src/a.py", "src/b.py"}
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_index_symbol_table.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement SymbolTable**

```python
# codewiki/src/be/index/symbol_table.py
"""SymbolTable: indexed collection of Symbols with fast lookups."""
from collections import defaultdict
from typing import Optional

from codewiki.src.be.index.models import Symbol, ExportStatus


class SymbolTable:
    """Holds all symbols and provides O(1) lookups by various keys."""

    def __init__(self, symbols: list[Symbol]):
        self._by_id: dict[str, Symbol] = {}
        self._by_file: dict[str, list[Symbol]] = defaultdict(list)
        self._by_qname: dict[str, Symbol] = {}

        for s in symbols:
            self._by_id[s.symbol_id] = s
            self._by_file[s.file_path].append(s)
            self._by_qname[s.qualified_name] = s

    def get(self, symbol_id: str) -> Optional[Symbol]:
        return self._by_id.get(symbol_id)

    def by_file(self, file_path: str) -> list[Symbol]:
        return self._by_file.get(file_path, [])

    def by_qualified_name(self, qname: str) -> Optional[Symbol]:
        return self._by_qname.get(qname)

    def children_of(self, symbol_id: str) -> list[Symbol]:
        parent = self._by_id.get(symbol_id)
        if not parent:
            return []
        return [self._by_id[cid] for cid in parent.children if cid in self._by_id]

    def public_api(self) -> list[Symbol]:
        return [s for s in self._by_id.values() if s.export_status == ExportStatus.EXPORTED]

    def search(self, name: str) -> list[Symbol]:
        lower = name.lower()
        return [s for s in self._by_id.values() if lower in s.name.lower()]

    def all_symbols(self) -> list[Symbol]:
        return list(self._by_id.values())

    def all_files(self) -> set[str]:
        return set(self._by_file.keys())
```

**Step 4: Run tests**

Run: `pytest tests/test_index_symbol_table.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/index/symbol_table.py tests/test_index_symbol_table.py
git commit -m "feat(index): add SymbolTable with indexed lookups"
```

---

### Task 4: ImportGraph

**Files:**
- Create: `codewiki/src/be/index/import_graph.py`
- Create: `tests/test_index_import_graph.py`

**Step 1: Write failing tests**

```python
# tests/test_index_import_graph.py
"""Tests for ImportGraph: file-level import edges and resolution."""
import pytest
from codewiki.src.be.index.models import ImportStatement, SymbolKind, SourceRange, Symbol
from codewiki.src.be.index.import_graph import ImportGraph
from codewiki.src.be.index.symbol_table import SymbolTable


def _imp(file_path, module_path, names=None, resolved=None, alias=None, line=1):
    return ImportStatement(
        file_path=file_path, module_path=module_path,
        imported_names=names or [], resolved_path=resolved,
        alias=alias, line=line,
    )


def _sym(sid, name, file_path="src/a.py", kind=SymbolKind.FUNCTION):
    return Symbol(
        symbol_id=sid, lang="python", kind=kind, name=name,
        qualified_name=f"src.a.{name}", file_path=file_path,
        range=SourceRange(file_path=file_path, start_line=1, start_col=0, end_line=1, end_col=0),
        source_hash="h",
    )


def test_imports_of():
    imp1 = _imp("src/main.py", "os.path", ["join"])
    imp2 = _imp("src/main.py", "sys")
    imp3 = _imp("src/other.py", "json")
    ig = ImportGraph([imp1, imp2, imp3])
    result = ig.imports_of("src/main.py")
    assert len(result) == 2


def test_imports_of_empty():
    ig = ImportGraph([])
    assert ig.imports_of("nonexistent.py") == []


def test_importers_of():
    imp1 = _imp("src/main.py", "./utils", resolved="src/utils.py")
    imp2 = _imp("src/api.py", "./utils", resolved="src/utils.py")
    ig = ImportGraph([imp1, imp2])
    result = ig.importers_of("src/utils.py")
    assert set(result) == {"src/main.py", "src/api.py"}


def test_importers_of_no_importers():
    ig = ImportGraph([_imp("src/main.py", "os")])
    assert ig.importers_of("src/standalone.py") == []


def test_file_dependency_graph():
    imp1 = _imp("src/a.py", "./b", resolved="src/b.py")
    imp2 = _imp("src/a.py", "./c", resolved="src/c.py")
    imp3 = _imp("src/b.py", "./c", resolved="src/c.py")
    ig = ImportGraph([imp1, imp2, imp3])
    graph = ig.file_dependency_graph()
    assert graph["src/a.py"] == {"src/b.py", "src/c.py"}
    assert graph["src/b.py"] == {"src/c.py"}


def test_file_dependency_graph_skips_unresolved():
    imp = _imp("src/a.py", "external_lib")  # no resolved_path
    ig = ImportGraph([imp])
    graph = ig.file_dependency_graph()
    assert graph.get("src/a.py", set()) == set()


def test_resolve_finds_symbol():
    imp = _imp("src/main.py", "./auth", ["LoginService"], resolved="src/auth.py")
    sym = _sym("py:src/auth.py#LoginService(class)", "LoginService", "src/auth.py", SymbolKind.CLASS)
    st = SymbolTable([sym])
    ig = ImportGraph([imp])
    result = ig.resolve("src/main.py", "LoginService", st)
    assert result is not None
    assert result.symbol_id == "py:src/auth.py#LoginService(class)"


def test_resolve_returns_none_for_unknown():
    ig = ImportGraph([])
    st = SymbolTable([])
    assert ig.resolve("src/main.py", "Unknown", st) is None


def test_all_imports():
    imps = [_imp("a.py", "x"), _imp("b.py", "y")]
    ig = ImportGraph(imps)
    assert len(ig.all_imports()) == 2
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_index_import_graph.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement ImportGraph**

```python
# codewiki/src/be/index/import_graph.py
"""ImportGraph: file-level import relationships and symbol resolution."""
from collections import defaultdict
from typing import Optional

from codewiki.src.be.index.models import ImportStatement, Symbol
from codewiki.src.be.index.symbol_table import SymbolTable


class ImportGraph:
    """Tracks import statements across the codebase and resolves imported symbols."""

    def __init__(self, imports: list[ImportStatement]):
        self._by_file: dict[str, list[ImportStatement]] = defaultdict(list)
        self._by_resolved: dict[str, list[str]] = defaultdict(list)  # resolved_path → [importing files]

        for imp in imports:
            self._by_file[imp.file_path].append(imp)
            if imp.resolved_path:
                self._by_resolved[imp.resolved_path].append(imp.file_path)

    def imports_of(self, file_path: str) -> list[ImportStatement]:
        return self._by_file.get(file_path, [])

    def importers_of(self, file_path: str) -> list[str]:
        return list(set(self._by_resolved.get(file_path, [])))

    def file_dependency_graph(self) -> dict[str, set[str]]:
        graph: dict[str, set[str]] = defaultdict(set)
        for file_path, imps in self._by_file.items():
            for imp in imps:
                if imp.resolved_path:
                    graph[file_path].add(imp.resolved_path)
        return dict(graph)

    def resolve(self, file_path: str, name: str, symbol_table: SymbolTable) -> Optional[Symbol]:
        """Resolve an imported name to a Symbol via the import chain."""
        for imp in self._by_file.get(file_path, []):
            if name in imp.imported_names and imp.resolved_path:
                # Look up the symbol in the resolved file
                for sym in symbol_table.by_file(imp.resolved_path):
                    if sym.name == name:
                        return sym
        return None

    def all_imports(self) -> list[ImportStatement]:
        result = []
        for imps in self._by_file.values():
            result.extend(imps)
        return result
```

**Step 4: Run tests**

Run: `pytest tests/test_index_import_graph.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/index/import_graph.py tests/test_index_import_graph.py
git commit -m "feat(index): add ImportGraph with file-level dependency tracking"
```

---

### Task 5: Python adapter — method extraction and imports

**Files:**
- Create: `codewiki/src/be/index/adapters/__init__.py`
- Create: `codewiki/src/be/index/adapters/python_adapter.py`
- Create: `tests/test_index_python_adapter.py`

**Step 1: Write failing tests**

```python
# tests/test_index_python_adapter.py
"""Tests for Python adapter: method extraction, import extraction, visibility."""
import textwrap
import pytest
from codewiki.src.be.index.adapters.python_adapter import PythonIndexAdapter
from codewiki.src.be.index.models import SymbolKind, Visibility, ExportStatus


def _adapt(code: str, file_path="src/example.py", repo_path="/repo"):
    code = textwrap.dedent(code)
    adapter = PythonIndexAdapter(file_path=file_path, content=code, repo_path=repo_path)
    return adapter.extract()


# ── Class + method extraction ────────────────────────────────────────────────

def test_extracts_class():
    symbols, imports = _adapt('''
        class Foo:
            """A foo class."""
            pass
    ''')
    classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
    assert len(classes) == 1
    assert classes[0].name == "Foo"
    assert classes[0].docstring == "A foo class."


def test_extracts_methods_as_children():
    symbols, imports = _adapt('''
        class Foo:
            def bar(self, x: int) -> str:
                """Do bar."""
                return str(x)

            def baz(self):
                pass
    ''')
    classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
    methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
    assert len(classes) == 1
    assert len(methods) == 2
    # Methods are children of the class
    assert set(classes[0].children) == {m.symbol_id for m in methods}
    # Methods have parent_symbol_id
    for m in methods:
        assert m.parent_symbol_id == classes[0].symbol_id


def test_method_signature():
    symbols, _ = _adapt('''
        class Foo:
            def bar(self, x: int, y: str = "hi") -> bool:
                pass
    ''')
    methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
    assert len(methods) == 1
    assert "x: int" in methods[0].signature
    assert "-> bool" in methods[0].signature


def test_extracts_top_level_function():
    symbols, _ = _adapt('''
        def standalone(a, b):
            """A standalone function."""
            return a + b
    ''')
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
    assert len(funcs) == 1
    assert funcs[0].name == "standalone"
    assert funcs[0].parent_symbol_id is None


def test_async_method():
    symbols, _ = _adapt('''
        class Service:
            async def fetch(self, url: str) -> bytes:
                pass
    ''')
    methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
    assert len(methods) == 1
    assert methods[0].name == "fetch"


def test_static_method():
    symbols, _ = _adapt('''
        class Util:
            @staticmethod
            def helper(x):
                pass
    ''')
    methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
    assert len(methods) == 1


# ── Import extraction ────────────────────────────────────────────────────────

def test_import_plain():
    _, imports = _adapt('''
        import os
        import sys
    ''')
    assert len(imports) == 2
    names = {i.module_path for i in imports}
    assert "os" in names
    assert "sys" in names


def test_from_import():
    _, imports = _adapt('''
        from os.path import join, dirname
    ''')
    assert len(imports) == 1
    assert imports[0].module_path == "os.path"
    assert imports[0].imported_names == ["join", "dirname"]


def test_import_alias():
    _, imports = _adapt('''
        import numpy as np
    ''')
    assert imports[0].alias == "np"


def test_relative_import():
    _, imports = _adapt('''
        from ..utils import helper
    ''')
    assert imports[0].module_path == "..utils"
    assert imports[0].imported_names == ["helper"]


def test_star_import():
    _, imports = _adapt('''
        from os.path import *
    ''')
    assert imports[0].imported_names == ["*"]


# ── Visibility ───────────────────────────────────────────────────────────────

def test_private_function():
    symbols, _ = _adapt('''
        def _internal():
            pass
    ''')
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
    assert funcs[0].visibility == Visibility.PRIVATE


def test_dunder_private():
    symbols, _ = _adapt('''
        class Foo:
            def __secret(self):
                pass
    ''')
    methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
    assert methods[0].visibility == Visibility.PRIVATE


def test_public_by_default():
    symbols, _ = _adapt('''
        def public_func():
            pass
    ''')
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
    assert funcs[0].visibility == Visibility.PUBLIC


def test_export_from_all():
    symbols, _ = _adapt('''
        __all__ = ["exported_func"]

        def exported_func():
            pass

        def not_exported():
            pass
    ''')
    exported = [s for s in symbols if s.export_status == ExportStatus.EXPORTED]
    not_exported = [s for s in symbols if s.export_status == ExportStatus.NOT_EXPORTED]
    assert len(exported) == 1
    assert exported[0].name == "exported_func"
    assert len(not_exported) >= 1


# ── File path is relative ────────────────────────────────────────────────────

def test_file_paths_are_relative():
    symbols, imports = _adapt('''
        import os

        class Foo:
            def bar(self):
                pass
    ''', file_path="/repo/src/example.py", repo_path="/repo")
    for s in symbols:
        assert not s.file_path.startswith("/"), f"Symbol {s.symbol_id} has absolute path: {s.file_path}"
    for i in imports:
        assert not i.file_path.startswith("/"), f"Import has absolute path: {i.file_path}"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_index_python_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement Python adapter**

```python
# codewiki/src/be/index/adapters/__init__.py
"""Index layer adapters: convert language-specific AST output to index models."""
```

```python
# codewiki/src/be/index/adapters/python_adapter.py
"""Enhanced Python adapter: extracts methods, imports, visibility, and signatures."""
import ast
import hashlib
import os
import warnings
from typing import Optional

from codewiki.src.be.index.models import (
    Symbol, ImportStatement, SymbolKind, Visibility, ExportStatus, SourceRange,
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
            return [], []

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

    def _make_range(self, node: ast.AST) -> SourceRange:
        return SourceRange(
            file_path=self._rel_path,
            start_line=node.lineno,
            start_col=node.col_offset,
            end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
            end_col=getattr(node, "end_col_offset", 0) or 0,
        )

    def _source_hash(self, node: ast.AST) -> str:
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
            signature=f"class {node.name}" + (f"({', '.join(ast.unparse(b) for b in node.bases)})" if node.bases else ""),
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
            export_status=self._export_status_for(node.name) if not parent_class else ExportStatus.UNKNOWN,
            docstring=ast.get_docstring(node),
            parent_symbol_id=parent_sid,
            source_hash=self._source_hash(node),
        )
        self._symbols.append(sym)
        return sym

    def _visit_import(self, node: ast.Import):
        for alias in node.names:
            self._imports.append(ImportStatement(
                file_path=self._rel_path,
                module_path=alias.name,
                imported_names=[],
                alias=alias.asname,
                line=node.lineno,
            ))

    def _visit_import_from(self, node: ast.ImportFrom):
        module = node.module or ""
        # Encode relative imports: level dots + module
        prefix = "." * (node.level or 0)
        module_path = prefix + module

        names = []
        for alias in (node.names or []):
            if alias.name == "*":
                names = ["*"]
                break
            names.append(alias.name)

        # Single alias for `from X import Y as Z` (only when single name)
        alias = None
        if len(node.names) == 1 and node.names[0].asname:
            alias = node.names[0].asname

        self._imports.append(ImportStatement(
            file_path=self._rel_path,
            module_path=module_path,
            imported_names=names,
            alias=alias,
            is_reexport=False,
            line=node.lineno,
        ))
```

**Step 4: Run tests**

Run: `pytest tests/test_index_python_adapter.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/index/adapters/ tests/test_index_python_adapter.py
git commit -m "feat(index): add Python adapter with method extraction, imports, and visibility"
```

---

### Task 6: TS/JS adapter — method extraction and imports

**Files:**
- Create: `codewiki/src/be/index/adapters/ts_js_adapter.py`
- Create: `tests/test_index_ts_js_adapter.py`

**Step 1: Write failing tests**

```python
# tests/test_index_ts_js_adapter.py
"""Tests for TS/JS adapter: method extraction, import/export, visibility."""
import textwrap
import pytest
from codewiki.src.be.index.adapters.ts_js_adapter import TSJSIndexAdapter
from codewiki.src.be.index.models import SymbolKind, Visibility, ExportStatus


def _adapt(code: str, file_path="src/example.ts", repo_path="/repo", lang="typescript"):
    code = textwrap.dedent(code)
    adapter = TSJSIndexAdapter(
        file_path=file_path, content=code, repo_path=repo_path, language=lang,
    )
    return adapter.extract()


# ── Class + method extraction ────────────────────────────────────────────────

def test_extracts_class_ts():
    symbols, _ = _adapt('''
        class Foo {
            bar(x: number): string {
                return String(x);
            }
        }
    ''')
    classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
    methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
    assert len(classes) == 1
    assert classes[0].name == "Foo"
    assert len(methods) == 1
    assert methods[0].name == "bar"
    assert methods[0].parent_symbol_id == classes[0].symbol_id


def test_extracts_exported_class():
    symbols, _ = _adapt('''
        export class AuthService {
            login() {}
        }
    ''')
    classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
    assert classes[0].export_status == ExportStatus.EXPORTED


def test_extracts_function_ts():
    symbols, _ = _adapt('''
        function greet(name: string): void {
            console.log(name);
        }
    ''')
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
    assert len(funcs) == 1
    assert funcs[0].name == "greet"


def test_exported_function():
    symbols, _ = _adapt('''
        export function helper() {}
    ''')
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
    assert funcs[0].export_status == ExportStatus.EXPORTED


def test_non_exported_function():
    symbols, _ = _adapt('''
        function internal() {}
    ''')
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
    assert funcs[0].export_status == ExportStatus.NOT_EXPORTED


# ── Import extraction ────────────────────────────────────────────────────────

def test_named_import():
    _, imports = _adapt('''
        import { Foo, Bar } from './module';
    ''')
    assert len(imports) >= 1
    imp = imports[0]
    assert "./module" in imp.module_path
    assert "Foo" in imp.imported_names


def test_default_import():
    _, imports = _adapt('''
        import React from 'react';
    ''')
    assert len(imports) >= 1
    assert imports[0].module_path == "react"


def test_namespace_import():
    _, imports = _adapt('''
        import * as path from 'path';
    ''')
    assert len(imports) >= 1
    assert imports[0].module_path == "path"
    assert imports[0].alias == "path"


# ── JS files work too ────────────────────────────────────────────────────────

def test_js_class():
    symbols, _ = _adapt('''
        class App {
            render() {
                return null;
            }
        }
    ''', file_path="src/app.js", lang="javascript")
    classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
    assert len(classes) == 1
    assert classes[0].lang == "javascript"


# ── Paths are relative ───────────────────────────────────────────────────────

def test_paths_are_relative():
    symbols, imports = _adapt('''
        import { X } from './x';
        export class Foo {
            bar() {}
        }
    ''', file_path="/repo/src/example.ts", repo_path="/repo")
    for s in symbols:
        assert not s.file_path.startswith("/")
    for i in imports:
        assert not i.file_path.startswith("/")
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_index_ts_js_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement TS/JS adapter**

This adapter uses tree-sitter (already a project dependency) to parse TS/JS and extract classes, methods, functions, and imports. Implementation should reuse the existing thread-local parser pattern from `codewiki/src/be/dependency_analyzer/analyzers/typescript.py`.

Key tree-sitter node types to handle:
- `class_declaration` / `class` → Class symbol
- `method_definition` → Method symbol (child of class)
- `function_declaration` → Function symbol
- `import_statement` → ImportStatement
- `export_statement` → marks declaration as exported

The adapter file will be ~200-250 lines. Write it following the same pattern as `python_adapter.py`: a class with `extract()` → `(list[Symbol], list[ImportStatement])`.

**Important implementation notes:**
- The adapter handles BOTH TypeScript and JavaScript via a `language` parameter.
- **Two separate parsers are needed**: `language="typescript"` uses `tree_sitter_typescript.language_typescript()` (see `analyzers/typescript.py`), while `language="javascript"` uses `tree_sitter_javascript.language()` (see `analyzers/javascript.py`). Reuse the thread-local singleton pattern from both files.
- The existing TS/JS analyzers do NOT handle `import_statement` nodes — this is new work. Use tree-sitter queries or manual node walking to extract import source paths and imported names from the AST.
- For `export_statement` detection: the existing `TreeSitterTSAnalyzer._extract_all_entities()` already checks for `export_statement` parent nodes (see `typescript.py` line ~97). Reuse this pattern.

**Step 4: Run tests**

Run: `pytest tests/test_index_ts_js_adapter.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/index/adapters/ts_js_adapter.py tests/test_index_ts_js_adapter.py
git commit -m "feat(index): add TS/JS adapter with method extraction and import/export tracking"
```

---

### Task 7: Generic adapter (Node → Symbol fallback)

**Files:**
- Create: `codewiki/src/be/index/adapters/generic_adapter.py`
- Create: `tests/test_index_generic_adapter.py`

**Step 1: Write failing tests**

```python
# tests/test_index_generic_adapter.py
"""Tests for generic adapter: Node → Symbol 1:1 conversion."""
import pytest
from codewiki.src.be.dependency_analyzer.models.core import Node
from codewiki.src.be.index.adapters.generic_adapter import GenericIndexAdapter
from codewiki.src.be.index.models import SymbolKind, Visibility, ExportStatus


def _node(cid="src.main.Foo", name="Foo", ctype="class", rel_path="src/main.py",
          docstring="", start=1, end=10):
    return Node(
        id=cid, name=name, component_type=ctype, file_path=f"/repo/{rel_path}",
        relative_path=rel_path, start_line=start, end_line=end,
        has_docstring=bool(docstring), docstring=docstring,
    )


def test_converts_class_node():
    n = _node(ctype="class")
    adapter = GenericIndexAdapter(lang="go")
    symbols = adapter.convert([n])
    assert len(symbols) == 1
    assert symbols[0].kind == SymbolKind.CLASS
    assert symbols[0].lang == "go"
    assert symbols[0].name == "Foo"


def test_converts_function_node():
    n = _node(cid="src.main.bar", name="bar", ctype="function")
    adapter = GenericIndexAdapter(lang="rust")
    symbols = adapter.convert([n])
    assert symbols[0].kind == SymbolKind.FUNCTION


def test_converts_struct_node():
    n = _node(ctype="struct")
    adapter = GenericIndexAdapter(lang="go")
    symbols = adapter.convert([n])
    assert symbols[0].kind == SymbolKind.STRUCT


def test_unknown_type_becomes_function():
    n = _node(ctype="weird_thing")
    adapter = GenericIndexAdapter(lang="c")
    symbols = adapter.convert([n])
    assert symbols[0].kind == SymbolKind.FUNCTION


def test_visibility_is_unknown():
    n = _node()
    adapter = GenericIndexAdapter(lang="java")
    symbols = adapter.convert([n])
    assert symbols[0].visibility == Visibility.UNKNOWN
    assert symbols[0].export_status == ExportStatus.UNKNOWN


def test_file_path_uses_relative():
    n = _node(rel_path="pkg/handler.go")
    adapter = GenericIndexAdapter(lang="go")
    symbols = adapter.convert([n])
    assert symbols[0].file_path == "pkg/handler.go"
    assert not symbols[0].file_path.startswith("/")


def test_preserves_docstring():
    n = _node(docstring="Does stuff")
    adapter = GenericIndexAdapter(lang="java")
    symbols = adapter.convert([n])
    assert symbols[0].docstring == "Does stuff"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_index_generic_adapter.py -v`
Expected: FAIL

**Step 3: Implement generic adapter**

```python
# codewiki/src/be/index/adapters/generic_adapter.py
"""Generic adapter: converts existing Node objects to Symbol (1:1 fallback)."""
import hashlib

from codewiki.src.be.dependency_analyzer.models.core import Node
from codewiki.src.be.index.models import (
    Symbol, SymbolKind, Visibility, ExportStatus, SourceRange,
)

_KIND_MAP = {
    "class": SymbolKind.CLASS,
    "interface": SymbolKind.INTERFACE,
    "struct": SymbolKind.STRUCT,
    "enum": SymbolKind.ENUM,
    "trait": SymbolKind.TRAIT,
    "method": SymbolKind.METHOD,
    "function": SymbolKind.FUNCTION,
    "variable": SymbolKind.VARIABLE,
    "constant": SymbolKind.CONSTANT,
    "type": SymbolKind.TYPE,
}


class GenericIndexAdapter:
    """Converts existing Node objects to Symbol with minimal metadata."""

    def __init__(self, lang: str):
        self.lang = lang

    def convert(self, nodes: list[Node]) -> list[Symbol]:
        return [self._convert_one(n) for n in nodes]

    def _convert_one(self, node: Node) -> Symbol:
        kind = _KIND_MAP.get(node.component_type, SymbolKind.FUNCTION)
        rel_path = node.relative_path.replace("\\", "/")
        source_hash = hashlib.sha256(
            (node.source_code or node.id).encode()
        ).hexdigest()[:16]

        return Symbol(
            symbol_id=f"{self.lang}:{rel_path}#{node.name}({kind.value})",
            lang=self.lang,
            kind=kind,
            name=node.name,
            qualified_name=node.id,
            file_path=rel_path,
            range=SourceRange(
                file_path=rel_path,
                start_line=node.start_line,
                start_col=0,
                end_line=node.end_line,
                end_col=0,
            ),
            signature=node.display_name,
            visibility=Visibility.UNKNOWN,
            export_status=ExportStatus.UNKNOWN,
            docstring=node.docstring or None,
            source_hash=source_hash,
        )
```

**Step 4: Run tests**

Run: `pytest tests/test_index_generic_adapter.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/index/adapters/generic_adapter.py tests/test_index_generic_adapter.py
git commit -m "feat(index): add generic adapter for Node-to-Symbol fallback conversion"
```

---

### Task 8: ComponentCard builder

**Files:**
- Create: `codewiki/src/be/index/component_card.py`
- Create: `tests/test_index_component_card.py`

**Step 1: Write failing tests**

```python
# tests/test_index_component_card.py
"""Tests for ComponentCard builder."""
import pytest
from codewiki.src.be.index.models import (
    Symbol, SymbolKind, SourceRange, SymbolEdge, EdgeType, Confidence, ComponentCard,
)
from codewiki.src.be.index.component_card import CardBuilder


def _sym(sid="py:src/a.py#Foo(class)", name="Foo", kind=SymbolKind.CLASS,
         sig="class Foo(Base)", doc="Handles authentication.\nWith multiple lines of detail.",
         file_path="src/a.py", start=1, end=50):
    return Symbol(
        symbol_id=sid, lang="python", kind=kind, name=name,
        qualified_name=f"src.a.{name}", file_path=file_path,
        range=SourceRange(file_path=file_path, start_line=start, start_col=0, end_line=end, end_col=0),
        signature=sig, docstring=doc, source_hash="h",
    )


def _edge(from_s, to_s, etype=EdgeType.CALLS):
    return SymbolEdge(
        edge_type=etype, from_symbol=from_s, to_symbol=to_s,
        confidence=Confidence.HIGH, resolver="ast",
    )


def test_builds_card():
    sym = _sym()
    builder = CardBuilder(max_edges=5)
    card = builder.build_card(sym, [])
    assert card.symbol_id == sym.symbol_id
    assert card.signature == "class Foo(Base)"
    assert "Handles authentication." in card.docstring_summary
    assert card.kind == SymbolKind.CLASS


def test_docstring_truncated_to_two_sentences():
    sym = _sym(doc="First sentence. Second sentence. Third sentence. Fourth.")
    builder = CardBuilder(max_edges=5)
    card = builder.build_card(sym, [])
    # Should have at most 2 sentences
    assert card.docstring_summary.count(".") <= 3  # 2 sentences + possible trailing


def test_no_docstring():
    sym = _sym(doc=None)
    builder = CardBuilder(max_edges=5)
    card = builder.build_card(sym, [])
    assert card.docstring_summary == ""


def test_key_edges_from_outgoing():
    sym = _sym(sid="s1")
    edges = [
        _edge("s1", "s2", EdgeType.CALLS),
        _edge("s1", "s3", EdgeType.IMPORTS),
    ]
    builder = CardBuilder(max_edges=5)
    card = builder.build_card(sym, edges)
    assert len(card.key_edges) == 2


def test_key_edges_capped():
    sym = _sym(sid="s1")
    edges = [_edge("s1", f"s{i}") for i in range(20)]
    builder = CardBuilder(max_edges=3)
    card = builder.build_card(sym, edges)
    assert len(card.key_edges) == 3


def test_file_context():
    sym = _sym(file_path="src/auth/login.py", start=10, end=42)
    builder = CardBuilder(max_edges=5)
    card = builder.build_card(sym, [])
    assert card.file_context == "src/auth/login.py (lines 10-42)"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_index_component_card.py -v`
Expected: FAIL

**Step 3: Implement CardBuilder**

```python
# codewiki/src/be/index/component_card.py
"""ComponentCard builder: creates LLM-facing symbol summaries."""
import re

from codewiki.src.be.index.models import Symbol, SymbolEdge, ComponentCard


class CardBuilder:
    """Builds ComponentCard from Symbol + its outgoing edges."""

    def __init__(self, max_edges: int = 5):
        self.max_edges = max_edges

    def build_card(self, symbol: Symbol, edges: list[SymbolEdge]) -> ComponentCard:
        outgoing = [e for e in edges if e.from_symbol == symbol.symbol_id]
        key_edges = [
            f"{e.edge_type.value}: {e.to_symbol or e.to_unresolved}"
            for e in outgoing[: self.max_edges]
        ]
        return ComponentCard(
            symbol_id=symbol.symbol_id,
            signature=symbol.signature or symbol.name,
            docstring_summary=self._truncate_docstring(symbol.docstring),
            kind=symbol.kind,
            key_edges=key_edges,
            file_context=f"{symbol.file_path} (lines {symbol.range.start_line}-{symbol.range.end_line})",
        )

    @staticmethod
    def _truncate_docstring(doc: str | None, max_sentences: int = 2) -> str:
        if not doc:
            return ""
        # Split on sentence boundaries (period + space or end)
        sentences = re.split(r'(?<=[.!?])\s+', doc.strip())
        return " ".join(sentences[:max_sentences]).strip()
```

**Step 4: Run tests**

Run: `pytest tests/test_index_component_card.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/index/component_card.py tests/test_index_component_card.py
git commit -m "feat(index): add ComponentCard builder for LLM-facing symbol summaries"
```

---

### Task 9: IndexBuilder orchestrator

**Files:**
- Create: `codewiki/src/be/index/index_builder.py`
- Create: `tests/test_index_builder.py`

**Step 1: Write failing tests**

```python
# tests/test_index_builder.py
"""Tests for IndexBuilder: end-to-end index construction."""
import os
import textwrap
import tempfile
import pytest
from codewiki.src.be.index.index_builder import IndexBuilder, IndexProducts
from codewiki.src.be.index.models import SymbolKind


@pytest.fixture
def sample_repo(tmp_path):
    """Create a minimal Python repo for testing."""
    src = tmp_path / "src"
    src.mkdir()

    (src / "__init__.py").write_text("")
    (src / "main.py").write_text(textwrap.dedent('''
        from .utils import helper

        class App:
            """Main application."""
            def run(self):
                helper()
    '''))
    (src / "utils.py").write_text(textwrap.dedent('''
        def helper():
            """A helper function."""
            pass

        def _internal():
            pass
    '''))
    return str(tmp_path)


def test_index_builder_produces_products(sample_repo):
    builder = IndexBuilder(repo_path=sample_repo)
    products = builder.build()
    assert isinstance(products, IndexProducts)
    assert products.symbol_table is not None
    assert products.import_graph is not None
    assert len(products.cards) > 0


def test_symbols_include_classes_and_methods(sample_repo):
    builder = IndexBuilder(repo_path=sample_repo)
    products = builder.build()
    st = products.symbol_table
    kinds = {s.kind for s in st.all_symbols()}
    assert SymbolKind.CLASS in kinds
    assert SymbolKind.METHOD in kinds
    assert SymbolKind.FUNCTION in kinds


def test_import_graph_has_entries(sample_repo):
    builder = IndexBuilder(repo_path=sample_repo)
    products = builder.build()
    ig = products.import_graph
    all_imps = ig.all_imports()
    # main.py imports from utils
    assert any("utils" in imp.module_path for imp in all_imps)


def test_all_paths_are_relative(sample_repo):
    builder = IndexBuilder(repo_path=sample_repo)
    products = builder.build()
    for sym in products.symbol_table.all_symbols():
        assert not sym.file_path.startswith("/"), f"Absolute path: {sym.file_path}"
    for imp in products.import_graph.all_imports():
        assert not imp.file_path.startswith("/"), f"Absolute path: {imp.file_path}"


def test_products_serializable(sample_repo):
    builder = IndexBuilder(repo_path=sample_repo)
    products = builder.build()
    data = products.to_dict()
    assert "symbols" in data
    assert "imports" in data
    assert "edges" in data
    assert "cards" in data

    # Round-trip
    restored = IndexProducts.from_dict(data)
    assert len(restored.symbol_table.all_symbols()) == len(products.symbol_table.all_symbols())
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_index_builder.py -v`
Expected: FAIL

**Step 3: Implement IndexBuilder**

```python
# codewiki/src/be/index/index_builder.py
"""IndexBuilder: orchestrates index construction from source files."""
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from codewiki.src.be.index.models import (
    Symbol, ImportStatement, SymbolEdge, ComponentCard, EdgeType, Confidence, SourceRange,
)
from codewiki.src.be.index.symbol_table import SymbolTable
from codewiki.src.be.index.import_graph import ImportGraph
from codewiki.src.be.index.component_card import CardBuilder
from codewiki.src.be.index.adapters.python_adapter import PythonIndexAdapter
from codewiki.src.be.index.adapters.generic_adapter import GenericIndexAdapter
from codewiki.src.be.dependency_analyzer.utils.patterns import CODE_EXTENSIONS
from codewiki.src.be.dependency_analyzer.utils.security import safe_open_text

logger = logging.getLogger(__name__)

# Languages that get enhanced (method + import) extraction
_ENHANCED_LANGS = {"python", "typescript", "javascript"}

_LANG_FROM_EXT = {v: v for v in CODE_EXTENSIONS.values()}


@dataclass
class IndexProducts:
    """All outputs of the index building process."""
    symbol_table: SymbolTable
    import_graph: ImportGraph
    edges: list[SymbolEdge]
    cards: list[ComponentCard]

    def to_dict(self) -> dict:
        return {
            "symbols": [s.model_dump() for s in self.symbol_table.all_symbols()],
            "imports": [i.model_dump() for i in self.import_graph.all_imports()],
            "edges": [e.model_dump() for e in self.edges],
            "cards": [c.model_dump() for c in self.cards],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "IndexProducts":
        symbols = [Symbol.model_validate(d) for d in data["symbols"]]
        imports = [ImportStatement.model_validate(d) for d in data["imports"]]
        edges = [SymbolEdge.model_validate(d) for d in data["edges"]]
        cards = [ComponentCard.model_validate(d) for d in data["cards"]]
        return cls(
            symbol_table=SymbolTable(symbols),
            import_graph=ImportGraph(imports),
            edges=edges,
            cards=cards,
        )


class IndexBuilder:
    """Builds index products from a repository's source files."""

    def __init__(self, repo_path: str, include_patterns: list[str] | None = None,
                 exclude_patterns: list[str] | None = None):
        self.repo_path = os.path.abspath(repo_path)
        self.include_patterns = include_patterns
        self.exclude_patterns = exclude_patterns

    def build(self) -> IndexProducts:
        all_symbols: list[Symbol] = []
        all_imports: list[ImportStatement] = []

        # Walk source files
        for file_path, lang in self._discover_files():
            abs_path = os.path.join(self.repo_path, file_path)
            try:
                content = safe_open_text(Path(self.repo_path), Path(abs_path))
            except Exception as e:
                logger.warning(f"Cannot read {file_path}: {e}")
                continue

            if lang == "python":
                adapter = PythonIndexAdapter(abs_path, content, self.repo_path)
                symbols, imports = adapter.extract()
                all_symbols.extend(symbols)
                all_imports.extend(imports)
            elif lang in ("typescript", "javascript"):
                try:
                    from codewiki.src.be.index.adapters.ts_js_adapter import TSJSIndexAdapter
                    adapter = TSJSIndexAdapter(abs_path, content, self.repo_path, language=lang)
                    symbols, imports = adapter.extract()
                    all_symbols.extend(symbols)
                    all_imports.extend(imports)
                except ImportError:
                    logger.debug(f"TS/JS adapter not available, using generic for {file_path}")
                    self._generic_fallback(abs_path, content, lang, all_symbols)
            else:
                self._generic_fallback(abs_path, content, lang, all_symbols)

        # Build products
        symbol_table = SymbolTable(all_symbols)
        import_graph = ImportGraph(all_imports)
        edges = self._build_edges(all_symbols, all_imports, symbol_table)
        card_builder = CardBuilder()
        cards = [card_builder.build_card(s, edges) for s in all_symbols if s.parent_symbol_id is None]

        logger.info(
            f"Index built: {len(all_symbols)} symbols, {len(all_imports)} imports, "
            f"{len(edges)} edges, {len(cards)} cards"
        )
        return IndexProducts(symbol_table, import_graph, edges, cards)

    def _generic_fallback(self, abs_path: str, content: str, lang: str,
                          all_symbols: list[Symbol]):
        """Parse with existing analyzer and convert via generic adapter."""
        # Import lazily to avoid circular deps
        from codewiki.src.be.dependency_analyzer.models.core import Node
        from codewiki.src.be.index.adapters.generic_adapter import GenericIndexAdapter

        nodes = self._analyze_with_existing(abs_path, content, lang)
        if nodes:
            adapter = GenericIndexAdapter(lang=lang)
            all_symbols.extend(adapter.convert(nodes))

    def _analyze_with_existing(self, abs_path: str, content: str, lang: str) -> list:
        """Use existing language analyzers to get Node objects."""
        try:
            from codewiki.src.be.dependency_analyzer.analysis.call_graph_analyzer import CallGraphAnalyzer
            analyzer = CallGraphAnalyzer()
            file_info = {"path": os.path.relpath(abs_path, self.repo_path), "language": lang}
            funcs, _ = analyzer._analyze_code_file(self.repo_path, file_info)
            return list(funcs.values())
        except Exception as e:
            logger.debug(f"Existing analyzer failed for {abs_path}: {e}")
            return []

    def _discover_files(self) -> list[tuple[str, str]]:
        """Walk repo and yield (relative_path, language) for source files."""
        results = []
        for root, dirs, files in os.walk(self.repo_path):
            # Skip common non-source directories
            dirs[:] = [d for d in dirs if d not in {
                ".git", "node_modules", "__pycache__", ".venv", "venv",
                ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
            }]
            for fname in files:
                abs_path = os.path.join(root, fname)
                rel_path = os.path.relpath(abs_path, self.repo_path).replace("\\", "/")
                ext = os.path.splitext(fname)[1].lower()
                lang = CODE_EXTENSIONS.get(ext)
                if lang:
                    results.append((rel_path, lang))
        return results

    def _build_edges(self, symbols: list[Symbol], imports: list[ImportStatement],
                     symbol_table: SymbolTable) -> list[SymbolEdge]:
        """Build SymbolEdge list from imports and parent-child relationships."""
        edges = []
        # Import edges: file-level
        for imp in imports:
            if imp.resolved_path:
                for name in imp.imported_names:
                    from_syms = symbol_table.by_file(imp.file_path)
                    to_sym = None
                    for s in symbol_table.by_file(imp.resolved_path):
                        if s.name == name:
                            to_sym = s
                            break
                    if from_syms:
                        edges.append(SymbolEdge(
                            edge_type=EdgeType.IMPORTS,
                            from_symbol=from_syms[0].symbol_id,
                            to_symbol=to_sym.symbol_id if to_sym else None,
                            to_unresolved=name if not to_sym else None,
                            evidence_refs=[SourceRange(
                                file_path=imp.file_path,
                                start_line=imp.line, start_col=0,
                                end_line=imp.line, end_col=0,
                            )],
                            confidence=Confidence.HIGH if to_sym else Confidence.LOW,
                            resolver="ast",
                        ))
        return edges
```

**Step 4: Run tests**

Run: `pytest tests/test_index_builder.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/index/index_builder.py tests/test_index_builder.py
git commit -m "feat(index): add IndexBuilder orchestrator with caching and serialization"
```

---

### Task 10: Integration — wire IndexBuilder into DocumentationGenerator

**Files:**
- Modify: `codewiki/src/be/documentation_generator.py` — call IndexBuilder after DependencyParser
- Create: `tests/test_index_integration.py`

**Step 1: Write integration test**

```python
# tests/test_index_integration.py
"""Integration test: IndexBuilder works with the existing pipeline."""
import textwrap
import pytest
from codewiki.src.be.index.index_builder import IndexBuilder


@pytest.fixture
def python_repo(tmp_path):
    pkg = tmp_path / "mypackage"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "service.py").write_text(textwrap.dedent('''
        from .models import User

        class AuthService:
            """Handles user authentication."""

            def login(self, username: str, password: str) -> bool:
                """Authenticate a user."""
                user = User.find(username)
                return user.check_password(password)

            def logout(self, session_id: str) -> None:
                pass

            def _validate_token(self, token: str) -> bool:
                return len(token) > 0
    '''))
    (pkg / "models.py").write_text(textwrap.dedent('''
        class User:
            """A user model."""

            def __init__(self, name: str):
                self.name = name

            def check_password(self, password: str) -> bool:
                return True

            @staticmethod
            def find(username: str) -> "User":
                return User(username)
    '''))
    return str(tmp_path)


def test_full_index_of_python_package(python_repo):
    builder = IndexBuilder(repo_path=python_repo)
    products = builder.build()
    st = products.symbol_table

    # Should find both classes
    class_names = {s.name for s in st.all_symbols() if s.kind.value == "class"}
    assert "AuthService" in class_names
    assert "User" in class_names

    # Should find methods
    method_names = {s.name for s in st.all_symbols() if s.kind.value == "method"}
    assert "login" in method_names
    assert "logout" in method_names
    assert "check_password" in method_names
    assert "_validate_token" in method_names  # private but still extracted

    # Private method should have private visibility
    validate = [s for s in st.all_symbols() if s.name == "_validate_token"]
    assert validate[0].visibility.value == "private"

    # AuthService should have children
    auth = [s for s in st.all_symbols() if s.name == "AuthService"]
    assert len(auth[0].children) >= 3  # login, logout, _validate_token

    # Import graph should show service.py imports models
    ig = products.import_graph
    imps = ig.imports_of("mypackage/service.py")
    assert any("models" in imp.module_path for imp in imps)

    # Cards should exist for top-level symbols
    assert len(products.cards) > 0

    # All paths relative
    for s in st.all_symbols():
        assert not s.file_path.startswith("/")


def test_serialization_roundtrip(python_repo):
    builder = IndexBuilder(repo_path=python_repo)
    products = builder.build()
    data = products.to_dict()

    from codewiki.src.be.index.index_builder import IndexProducts
    restored = IndexProducts.from_dict(data)
    assert len(restored.symbol_table.all_symbols()) == len(products.symbol_table.all_symbols())
    assert len(restored.import_graph.all_imports()) == len(products.import_graph.all_imports())
```

**Step 2: Run integration test**

Run: `pytest tests/test_index_integration.py -v`
Expected: All PASS (since all components are built)

**Step 3: Wire into DocumentationGenerator**

In `codewiki/src/be/documentation_generator.py`, add index building after the dependency parsing step. This is a non-breaking addition — the existing flow continues to work, and the index products are stored on `self` for future use by downstream phases.

Add after the line `components, leaf_nodes = self.graph_builder.build_dependency_graph()`:

```python
# Build v3 index (symbol table, import graph, component cards)
from codewiki.src.be.index.index_builder import IndexBuilder
index_builder = IndexBuilder(
    repo_path=self.config.repo_path,
    include_patterns=self.config.include_patterns,
    exclude_patterns=self.config.exclude_patterns,
)
self.index_products = index_builder.build()
```

Note: `self.config.include_patterns` and `self.config.exclude_patterns` are safe
properties that handle `agent_instructions` being `None`. Do NOT use
`self.config.agent_instructions.get(...)` directly as it will raise `AttributeError`
when `agent_instructions` is `None`.

**Step 4: Run full test suite**

Run: `pytest tests/ -v`
Expected: All existing + new tests PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/documentation_generator.py tests/test_index_integration.py
git commit -m "feat(index): wire IndexBuilder into DocumentationGenerator pipeline"
```

---

### Task 11: Run full test suite and verify no regressions

**Step 1: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests pass, no regressions in existing tests

**Step 2: Run on a real repo (manual smoke test)**

Run: `cd /tmp && git clone --depth 1 https://github.com/some-small-python-repo && codewiki generate /tmp/repo -o /tmp/wiki`
(The CLI entry point is `codewiki` per `pyproject.toml [project.scripts]`, not `python -m codewiki.cli.main`)

Verify: Index log lines appear (e.g. "Index built: N symbols, M imports, K edges, L cards")

**Step 3: Final commit if any fixups needed**

```bash
git add -A
git commit -m "fix(index): address test/integration issues from smoke testing"
```

---

## Audit Notes (Known Gaps & Limitations)

**Accepted for v1:**
- `_discover_files()` uses `CODE_EXTENSIONS` only — `.cfg` (Vitis), `CMakeLists.txt`, and `Makefile` files detected by name/content in the existing `CallGraphAnalyzer` are NOT covered by the index layer. These use specialized detection logic that would be over-engineered to duplicate here. They will be picked up by the generic fallback if the existing analyzer pipeline runs first.
- Task 6 (TS/JS adapter) does not provide full implementation code unlike other tasks. This is intentional — it requires fresh tree-sitter work for `import_statement` nodes that cannot be copy-pasted from existing analyzers. The test cases define the expected behavior clearly.
- `IndexBuilder._discover_files()` has a hardcoded ignore list (`.git`, `node_modules`, etc.) that partially overlaps with `DEFAULT_IGNORE_PATTERNS` in `utils/patterns.py`. A future cleanup could unify these.
- The `ImportStatement.resolved_path` field is only populated by the Python adapter (via relative import dot-counting). TS/JS import resolution (e.g., `./foo` → `src/foo.ts`) requires filesystem probing and is left as a follow-up enhancement.
