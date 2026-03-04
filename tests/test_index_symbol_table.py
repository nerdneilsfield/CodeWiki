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


# ── New edge-case tests ───────────────────────────────────────────────────────

def test_search_is_case_insensitive():
    """search('foo') should find symbols with 'FooService', 'foobar', etc."""
    s1 = _make_symbol("s1", "FooService")
    s2 = _make_symbol("s2", "BarService")
    s3 = _make_symbol("s3", "foobar")
    st = SymbolTable([s1, s2, s3])
    results = st.search("foo")
    result_names = {s.name for s in results}
    assert "FooService" in result_names
    assert "foobar" in result_names
    assert "BarService" not in result_names


def test_search_returns_empty_list_when_no_match():
    """search() with no matching symbols returns empty list."""
    s1 = _make_symbol("s1", "Alpha")
    s2 = _make_symbol("s2", "Beta")
    st = SymbolTable([s1, s2])
    results = st.search("Gamma")
    assert results == []


def test_public_api_excludes_unknown_export_status():
    """Symbols with ExportStatus.UNKNOWN are NOT in public_api (only EXPORTED are)."""
    s_exported = _make_symbol("s1", "pub", export_status=ExportStatus.EXPORTED)
    s_unknown = _make_symbol("s2", "unk", export_status=ExportStatus.UNKNOWN, visibility=Visibility.PUBLIC)
    s_not_exported = _make_symbol("s3", "priv", export_status=ExportStatus.NOT_EXPORTED)
    st = SymbolTable([s_exported, s_unknown, s_not_exported])
    api = st.public_api()
    api_ids = {s.symbol_id for s in api}
    assert "s1" in api_ids
    assert "s2" not in api_ids  # UNKNOWN is NOT in public_api
    assert "s3" not in api_ids


def test_children_of_skips_missing_child_ids():
    """children_of() gracefully skips child IDs that don't exist in the table."""
    parent = _make_symbol("p1", "Parent", kind=SymbolKind.CLASS)
    parent.children = ["m1", "m2", "nonexistent_id"]
    m1 = _make_symbol("m1", "method_a", kind=SymbolKind.METHOD, parent="p1")
    # m2 is deliberately omitted from the table
    st = SymbolTable([parent, m1])
    children = st.children_of("p1")
    # Should only return m1, not crash on nonexistent_id or missing m2
    assert len(children) == 1
    assert children[0].name == "method_a"


def test_duplicate_symbol_ids_later_wins():
    """When two symbols share the same symbol_id, the later one overwrites the earlier."""
    s1 = _make_symbol("dup_id", "First")
    s2 = _make_symbol("dup_id", "Second")
    st = SymbolTable([s1, s2])
    result = st.get("dup_id")
    # The later one (s2) should win
    assert result is not None
    assert result.name == "Second"


def test_all_files_empty_table():
    """all_files() returns empty set for empty SymbolTable."""
    st = SymbolTable([])
    assert st.all_files() == set()
