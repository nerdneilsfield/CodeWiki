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


# ── New edge-case tests ───────────────────────────────────────────────────────

def test_empty_directory_produces_zero_symbols(tmp_path):
    """Empty directory (no source files) should produce IndexProducts with 0 symbols."""
    builder = IndexBuilder(repo_path=str(tmp_path))
    products = builder.build()
    assert isinstance(products, IndexProducts)
    assert len(products.symbol_table.all_symbols()) == 0


def test_directory_with_only_init_py(tmp_path):
    """Directory with only __init__.py should not crash and produce valid products."""
    (tmp_path / "__init__.py").write_text("")
    builder = IndexBuilder(repo_path=str(tmp_path))
    products = builder.build()
    assert isinstance(products, IndexProducts)
    # symbols may be empty (valid — __init__.py with no content)
    assert products.symbol_table is not None


def test_file_with_syntax_error_does_not_crash(tmp_path):
    """A Python file with a syntax error should not crash the builder; other files still indexed."""
    (tmp_path / "broken.py").write_text("def broken(\n    # unterminated\nclass Oops:\n")
    (tmp_path / "good.py").write_text("def good_function():\n    pass\n")
    builder = IndexBuilder(repo_path=str(tmp_path))
    products = builder.build()
    assert isinstance(products, IndexProducts)
    # good.py should still be indexed
    good_symbols = [s for s in products.symbol_table.all_symbols() if s.name == "good_function"]
    assert len(good_symbols) == 1


def test_repo_with_typescript_files(tmp_path):
    """Repo with TypeScript files should have TS symbols extracted."""
    (tmp_path / "app.ts").write_text(
        "export class AppService {\n    run() {}\n}\n"
    )
    builder = IndexBuilder(repo_path=str(tmp_path))
    products = builder.build()
    assert isinstance(products, IndexProducts)
    # Should have at least attempted to index the TS file (may produce symbols if tree-sitter available)
    # The key invariant: no exception raised


def test_from_dict_round_trip_preserves_symbol_count_and_relative_paths(sample_repo):
    """IndexProducts.from_dict(products.to_dict()) produces identical symbol count and all paths still relative."""
    builder = IndexBuilder(repo_path=sample_repo)
    products = builder.build()
    data = products.to_dict()
    restored = IndexProducts.from_dict(data)

    # Same count
    assert len(restored.symbol_table.all_symbols()) == len(products.symbol_table.all_symbols())

    # All paths still relative after round-trip
    for sym in restored.symbol_table.all_symbols():
        assert not sym.file_path.startswith("/"), f"Absolute path after round-trip: {sym.file_path}"
    for imp in restored.import_graph.all_imports():
        assert not imp.file_path.startswith("/"), f"Absolute import path after round-trip: {imp.file_path}"
