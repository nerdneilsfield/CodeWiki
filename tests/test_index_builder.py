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
