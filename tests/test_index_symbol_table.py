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
