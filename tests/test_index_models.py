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


# ── New edge-case tests ───────────────────────────────────────────────────────

def test_symbol_model_dump_is_json_serializable():
    """Symbol.model_dump() should produce a JSON-serializable dict (no custom types)."""
    import json
    s = Symbol(
        symbol_id="py:src/a.py#Foo(class)",
        lang="python",
        kind=SymbolKind.CLASS,
        name="Foo",
        qualified_name="src.a.Foo",
        file_path="src/a.py",
        range=SourceRange(file_path="src/a.py", start_line=1, start_col=0, end_line=10, end_col=0),
        source_hash="abc123",
        visibility=Visibility.PUBLIC,
        export_status=ExportStatus.EXPORTED,
    )
    dumped = s.model_dump()
    # This should not raise
    serialized = json.dumps(dumped)
    assert isinstance(serialized, str)


def test_symbol_edge_with_both_to_symbol_and_to_unresolved_allowed():
    """SymbolEdge model allows having both to_symbol and to_unresolved set (no exclusivity validation)."""
    e = SymbolEdge(
        edge_type=EdgeType.CALLS,
        from_symbol="py:src/a.py#f(function)",
        to_symbol="py:src/b.py#g(function)",
        to_unresolved="some.external.func",
        confidence=Confidence.MEDIUM,
        resolver="ast",
    )
    assert e.to_symbol == "py:src/b.py#g(function)"
    assert e.to_unresolved == "some.external.func"


def test_all_symbol_kind_values_are_strings():
    """Every SymbolKind member should have a string value."""
    for kind in SymbolKind:
        assert isinstance(kind.value, str), f"SymbolKind.{kind.name}.value is not a string"


def test_visibility_unknown_is_default_for_symbol():
    """Visibility.UNKNOWN should be the default visibility for a new Symbol."""
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
    assert s.visibility == Visibility.UNKNOWN


def test_export_status_unknown_is_default_for_symbol():
    """ExportStatus.UNKNOWN should be the default export_status for a new Symbol."""
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
    assert s.export_status == ExportStatus.UNKNOWN


def test_import_statement_is_reexport_true():
    """ImportStatement supports is_reexport=True."""
    imp = ImportStatement(
        file_path="src/index.py",
        module_path="./auth",
        imported_names=["AuthService"],
        is_reexport=True,
        line=1,
    )
    assert imp.is_reexport is True


def test_source_range_equality():
    """Two SourceRange objects with same values should be equal."""
    r1 = SourceRange(file_path="src/a.py", start_line=5, start_col=0, end_line=10, end_col=4)
    r2 = SourceRange(file_path="src/a.py", start_line=5, start_col=0, end_line=10, end_col=4)
    r3 = SourceRange(file_path="src/b.py", start_line=5, start_col=0, end_line=10, end_col=4)
    assert r1 == r2
    assert r1 != r3
