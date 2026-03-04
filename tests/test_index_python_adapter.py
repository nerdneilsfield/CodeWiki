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
