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
