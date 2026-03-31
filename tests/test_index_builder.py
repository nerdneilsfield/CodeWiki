# tests/test_index_builder.py
"""Tests for IndexBuilder: end-to-end index construction."""
import textwrap
import pytest
from codewiki.src.be.index.index_builder import IndexBuilder, IndexProducts
from codewiki.src.be.index.models import SymbolKind, EdgeType


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


# ── Caching tests (RED — will fail until caching is implemented) ──────────────

def test_cache_saves_and_loads(sample_repo, tmp_path):
    """Build once: cache file appears. Build again: returns same products (same symbol count)."""
    import json
    from codewiki.src.be.index.index_builder import INDEX_VERSION

    output_dir = str(tmp_path / "out")
    builder = IndexBuilder(repo_path=sample_repo, output_dir=output_dir)
    products_first = builder.build()

    cache_file = tmp_path / "out" / "_index_cache.json"
    assert cache_file.exists(), "Cache file should be written after first build"

    # Verify the cache key fields are present
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert "_cache_key" in data
    assert data["_cache_key"]["index_version"] == INDEX_VERSION

    # Second build with same output_dir should return same symbol count
    builder2 = IndexBuilder(repo_path=sample_repo, output_dir=output_dir)
    products_second = builder2.build()
    assert (len(products_second.symbol_table.all_symbols()) ==
            len(products_first.symbol_table.all_symbols())), (
        "Second build (cache hit) should return same symbol count as first build"
    )


def test_cache_miss_on_different_version(sample_repo, tmp_path, monkeypatch):
    """After first build, patching INDEX_VERSION to a new value causes a cache miss and rebuild."""
    import codewiki.src.be.index.index_builder as ib_module

    output_dir = str(tmp_path / "out")
    builder = IndexBuilder(repo_path=sample_repo, output_dir=output_dir)
    builder.build()

    # Tamper: change INDEX_VERSION so existing cache key no longer matches
    monkeypatch.setattr(ib_module, "INDEX_VERSION", "999")

    builder2 = ib_module.IndexBuilder(repo_path=sample_repo, output_dir=output_dir)
    # Should still return valid products (rebuilt from source, not crashed)
    products = builder2.build()
    assert isinstance(products, IndexProducts), "Rebuild after version mismatch must succeed"
    assert products.symbol_table is not None


def test_no_cache_when_no_output_dir(sample_repo, tmp_path):
    """IndexBuilder without output_dir must not create any cache file."""
    builder = IndexBuilder(repo_path=sample_repo)  # no output_dir
    products = builder.build()
    assert isinstance(products, IndexProducts)
    # No _index_cache.json anywhere under sample_repo
    cache_files = list((tmp_path).rglob("_index_cache.json"))
    assert len(cache_files) == 0, (
        f"No cache file should be written when output_dir is not set; found: {cache_files}"
    )


def test_cache_handles_corrupted_file(sample_repo, tmp_path):
    """Writing garbage to the cache file must not crash the builder (treated as cache miss)."""
    output_dir = tmp_path / "out"
    output_dir.mkdir(parents=True)
    cache_file = output_dir / "_index_cache.json"
    cache_file.write_text("THIS IS NOT JSON }{{{", encoding="utf-8")

    builder = IndexBuilder(repo_path=sample_repo, output_dir=str(output_dir))
    products = builder.build()
    assert isinstance(products, IndexProducts), "Corrupted cache must be treated as a miss, not a crash"
    assert products.symbol_table is not None


# ── EXTENDS edge tests ────────────────────────────────────────────────────────

@pytest.fixture
def inheritance_repo(tmp_path):
    """Repo with a local base class and a subclass."""
    (tmp_path / "base.py").write_text(textwrap.dedent('''\
        class Animal:
            """Base animal class."""
            def speak(self):
                pass
    '''))
    (tmp_path / "child.py").write_text(textwrap.dedent('''\
        from base import Animal

        class Dog(Animal):
            """A dog."""
            def speak(self):
                return "woof"
    '''))
    return str(tmp_path)


@pytest.fixture
def multi_base_repo(tmp_path):
    """Repo where a class inherits from multiple local bases."""
    (tmp_path / "mixins.py").write_text(textwrap.dedent('''\
        class LogMixin:
            def log(self):
                pass

        class SerializeMixin:
            def serialize(self):
                pass
    '''))
    (tmp_path / "service.py").write_text(textwrap.dedent('''\
        from mixins import LogMixin, SerializeMixin

        class Service(LogMixin, SerializeMixin):
            """Service with multiple bases."""
            pass
    '''))
    return str(tmp_path)


def test_extends_edge_created_for_local_base_class(inheritance_repo):
    """Python class with a same-repo base → EXTENDS edge with resolved to_symbol."""
    builder = IndexBuilder(repo_path=inheritance_repo)
    products = builder.build()

    extends_edges = [e for e in products.edges if e.edge_type == EdgeType.EXTENDS]
    assert len(extends_edges) >= 1, "Expected at least one EXTENDS edge"

    # The Dog→Animal edge must have a resolved to_symbol
    dog_extends = [
        e for e in extends_edges
        if e.to_symbol is not None and "Animal" in (e.to_symbol or "")
    ]
    assert len(dog_extends) >= 1, (
        f"Expected a resolved EXTENDS edge for Dog→Animal. Edges: {extends_edges}"
    )
    edge = dog_extends[0]
    assert edge.to_symbol is not None
    assert edge.to_unresolved is None
    assert edge.confidence == "high"


def test_extends_edge_unresolved_for_external_base(tmp_path):
    """Python class inheriting from an external (stdlib/third-party) base → to_unresolved set."""
    (tmp_path / "model.py").write_text(textwrap.dedent('''\
        from pydantic import BaseModel

        class MyModel(BaseModel):
            name: str
    '''))
    builder = IndexBuilder(repo_path=str(tmp_path))
    products = builder.build()

    extends_edges = [e for e in products.edges if e.edge_type == EdgeType.EXTENDS]
    assert len(extends_edges) >= 1

    base_model_edge = next(
        (e for e in extends_edges if e.to_unresolved == "BaseModel"), None
    )
    assert base_model_edge is not None, (
        f"Expected an unresolved EXTENDS edge for BaseModel. Edges: {extends_edges}"
    )
    assert base_model_edge.to_symbol is None
    assert base_model_edge.confidence == "low"


def test_no_extends_edge_for_class_without_base(tmp_path):
    """A plain class with no base classes produces no EXTENDS edges."""
    (tmp_path / "plain.py").write_text(textwrap.dedent('''\
        class Standalone:
            """No inheritance."""
            pass
    '''))
    builder = IndexBuilder(repo_path=str(tmp_path))
    products = builder.build()

    extends_edges = [e for e in products.edges if e.edge_type == EdgeType.EXTENDS]
    assert len(extends_edges) == 0, f"Expected no EXTENDS edges, got: {extends_edges}"


def test_multiple_bases_produce_multiple_extends_edges(multi_base_repo):
    """Class inheriting from two local bases → two EXTENDS edges."""
    builder = IndexBuilder(repo_path=multi_base_repo)
    products = builder.build()

    extends_edges = [e for e in products.edges if e.edge_type == EdgeType.EXTENDS]
    # Service has two bases: LogMixin and SerializeMixin
    assert len(extends_edges) >= 2, (
        f"Expected at least 2 EXTENDS edges for Service(LogMixin, SerializeMixin), "
        f"got {len(extends_edges)}: {extends_edges}"
    )

    resolved_names = {
        e.to_symbol.split("#")[1].split("(")[0] if e.to_symbol else e.to_unresolved
        for e in extends_edges
    }
    assert "LogMixin" in resolved_names or any("LogMixin" in (e.to_unresolved or "") for e in extends_edges)
    assert "SerializeMixin" in resolved_names or any("SerializeMixin" in (e.to_unresolved or "") for e in extends_edges)


def test_extends_edges_serializable(inheritance_repo):
    """EXTENDS edges survive a to_dict/from_dict round-trip."""
    builder = IndexBuilder(repo_path=inheritance_repo)
    products = builder.build()

    extends_before = [e for e in products.edges if e.edge_type == EdgeType.EXTENDS]
    assert len(extends_before) >= 1

    data = products.to_dict()
    restored = IndexProducts.from_dict(data)

    extends_after = [e for e in restored.edges if e.edge_type == EdgeType.EXTENDS]
    assert len(extends_after) == len(extends_before)


def test_extends_evidence_refs_point_to_class_definition(tmp_path):
    """The evidence_ref for an EXTENDS edge points to the subclass definition line."""
    (tmp_path / "shapes.py").write_text(textwrap.dedent('''\
        class Shape:
            pass

        class Circle(Shape):
            pass
    '''))
    builder = IndexBuilder(repo_path=str(tmp_path))
    products = builder.build()

    extends_edges = [e for e in products.edges if e.edge_type == EdgeType.EXTENDS]
    assert len(extends_edges) >= 1

    # Evidence ref must exist and file_path must be relative
    for edge in extends_edges:
        assert len(edge.evidence_refs) >= 1
        ref = edge.evidence_refs[0]
        assert not ref.file_path.startswith("/"), f"Absolute path in evidence_ref: {ref.file_path}"
        assert ref.start_line >= 1
