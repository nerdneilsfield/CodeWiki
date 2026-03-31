"""Tests for codewiki/src/be/generation/glossary.py.

Follows TDD workflow: tests written before implementation.
All external dependencies (IndexProducts, SymbolTable) are constructed
from real models — no mocking needed because they are pure data structures.
"""
import pytest

from codewiki.src.be.index.models import (
    Symbol,
    SymbolKind,
    ExportStatus,
    Visibility,
    SourceRange,
)
from codewiki.src.be.index.symbol_table import SymbolTable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_range() -> SourceRange:
    return SourceRange(
        file_path="src/mod.py",
        start_line=1,
        start_col=0,
        end_line=10,
        end_col=0,
    )


def _make_symbol(
    name: str,
    kind: SymbolKind = SymbolKind.CLASS,
    export_status: ExportStatus = ExportStatus.EXPORTED,
    docstring: str | None = None,
    file_path: str = "src/mod.py",
) -> Symbol:
    return Symbol(
        symbol_id=f"sym-{name}",
        lang="python",
        kind=kind,
        name=name,
        qualified_name=f"mod.{name}",
        file_path=file_path,
        range=_make_range(),
        export_status=export_status,
        docstring=docstring,
        source_hash="abc123",
    )


class _FakeIndexProducts:
    """Minimal stand-in for IndexProducts that only exposes symbol_table."""

    def __init__(self, symbols: list[Symbol]):
        self.symbol_table = SymbolTable(symbols)


# ---------------------------------------------------------------------------
# build_glossary — unit tests
# ---------------------------------------------------------------------------

class TestBuildGlossary:

    def test_build_glossary_from_public_api(self):
        """Two EXPORTED symbols with docstrings produce two glossary entries."""
        from codewiki.src.be.generation.glossary import build_glossary

        sym_a = _make_symbol("Alpha", docstring="Does alpha things. Extra detail.")
        sym_b = _make_symbol("Beta", kind=SymbolKind.FUNCTION, docstring="Does beta things.")
        index_products = _FakeIndexProducts([sym_a, sym_b])

        result = build_glossary(index_products)

        assert len(result) == 2
        assert "Alpha" in result
        assert "Beta" in result

    def test_build_glossary_empty_index(self):
        """None index_products returns empty dict."""
        from codewiki.src.be.generation.glossary import build_glossary

        result = build_glossary(None)

        assert result == {}

    def test_build_glossary_no_exported_symbols(self):
        """Symbols with UNKNOWN export_status are excluded from glossary."""
        from codewiki.src.be.generation.glossary import build_glossary

        sym = _make_symbol("Hidden", export_status=ExportStatus.UNKNOWN, docstring="Secret.")
        index_products = _FakeIndexProducts([sym])

        result = build_glossary(index_products)

        assert result == {}

    def test_build_glossary_not_exported_symbols_excluded(self):
        """Symbols with NOT_EXPORTED export_status are excluded from glossary."""
        from codewiki.src.be.generation.glossary import build_glossary

        sym = _make_symbol("Internal", export_status=ExportStatus.NOT_EXPORTED, docstring="Private.")
        index_products = _FakeIndexProducts([sym])

        result = build_glossary(index_products)

        assert result == {}

    def test_build_glossary_no_docstring(self):
        """EXPORTED symbol without docstring still produces a glossary entry with kind+file."""
        from codewiki.src.be.generation.glossary import build_glossary

        sym = _make_symbol("Nodoc", docstring=None, file_path="src/core.py")
        index_products = _FakeIndexProducts([sym])

        result = build_glossary(index_products)

        assert "Nodoc" in result
        definition = result["Nodoc"]
        assert "class" in definition
        assert "src/core.py" in definition

    def test_build_glossary_sorted(self):
        """Glossary entries are returned in alphabetical order by term."""
        from codewiki.src.be.generation.glossary import build_glossary

        syms = [
            _make_symbol("Zebra", docstring="Last."),
            _make_symbol("Apple", docstring="First."),
            _make_symbol("Mango", docstring="Middle."),
        ]
        index_products = _FakeIndexProducts(syms)

        result = build_glossary(index_products)

        keys = list(result.keys())
        assert keys == sorted(keys)

    def test_build_glossary_first_sentence_only(self):
        """Multi-sentence docstring: only the first sentence is used in the definition."""
        from codewiki.src.be.generation.glossary import build_glossary

        sym = _make_symbol(
            "Verbose",
            docstring="Short summary. Extra detail here. Even more detail.",
        )
        index_products = _FakeIndexProducts([sym])

        result = build_glossary(index_products)

        definition = result["Verbose"]
        assert "Short summary." in definition
        assert "Extra detail here" not in definition

    def test_build_glossary_definition_contains_kind_and_file(self):
        """Definition string includes the symbol kind and file path."""
        from codewiki.src.be.generation.glossary import build_glossary

        sym = _make_symbol(
            "MyFunc",
            kind=SymbolKind.FUNCTION,
            docstring="Computes something.",
            file_path="src/utils.py",
        )
        index_products = _FakeIndexProducts([sym])

        result = build_glossary(index_products)

        definition = result["MyFunc"]
        assert "function" in definition
        assert "src/utils.py" in definition

    def test_build_glossary_missing_symbol_table_attr(self):
        """Object without symbol_table attribute returns empty dict (defensive)."""
        from codewiki.src.be.generation.glossary import build_glossary

        class BadProducts:
            pass

        result = build_glossary(BadProducts())

        assert result == {}


# ---------------------------------------------------------------------------
# build_link_map — unit tests
# ---------------------------------------------------------------------------

class TestBuildLinkMap:

    def test_build_link_map_simple(self):
        """module_tree with two top-level modules produces two entries."""
        from codewiki.src.be.generation.glossary import build_link_map

        tree = {
            "Auth Module": {
                "path": "src/auth",
                "components": ["AuthService"],
                "children": {},
            },
            "User Module": {
                "path": "src/user",
                "components": ["UserService"],
                "children": {},
            },
        }

        result = build_link_map(tree)

        assert len(result) == 2
        assert "src/auth" in result
        assert "src/user" in result

    def test_build_link_map_nested(self):
        """Nested children are included in the link map."""
        from codewiki.src.be.generation.glossary import build_link_map

        tree = {
            "Auth Module": {
                "path": "src/auth",
                "components": ["AuthService"],
                "children": {
                    "Sub Module": {
                        "path": "src/auth/sub",
                        "components": ["SubService"],
                        "children": {},
                    }
                },
            }
        }

        result = build_link_map(tree)

        assert "src/auth" in result
        assert "src/auth/sub" in result

    def test_build_link_map_empty_tree(self):
        """Empty module tree returns empty dict."""
        from codewiki.src.be.generation.glossary import build_link_map

        result = build_link_map({})

        assert result == {}

    def test_build_link_map_uses_path_as_key(self):
        """Keys in link_map come from the 'path' field, not the title."""
        from codewiki.src.be.generation.glossary import build_link_map

        tree = {
            "Some Long Human Title": {
                "path": "mod/stable_path",
                "components": [],
                "children": {},
            }
        }

        result = build_link_map(tree)

        assert "mod/stable_path" in result
        assert "Some Long Human Title" not in result

    def test_build_link_map_sorted(self):
        """Link map entries are sorted by key (module path)."""
        from codewiki.src.be.generation.glossary import build_link_map

        tree = {
            "Z Module": {"path": "src/z", "components": [], "children": {}},
            "A Module": {"path": "src/a", "components": [], "children": {}},
            "M Module": {"path": "src/m", "components": [], "children": {}},
        }

        result = build_link_map(tree)

        keys = list(result.keys())
        assert keys == sorted(keys)

    def test_build_link_map_values_are_md_filenames(self):
        """Values in link_map are .md filenames."""
        from codewiki.src.be.generation.glossary import build_link_map

        tree = {
            "Auth Module": {
                "path": "src/auth",
                "components": [],
                "children": {},
            }
        }

        result = build_link_map(tree)

        filename = result["src/auth"]
        assert filename.endswith(".md")

    def test_build_link_map_skips_entries_without_path(self):
        """Tree entries without a 'path' field are not added to link_map."""
        from codewiki.src.be.generation.glossary import build_link_map

        tree = {
            "Pathless": {
                "components": ["X"],
                "children": {},
                # no "path" key
            },
            "HasPath": {
                "path": "src/valid",
                "components": [],
                "children": {},
            },
        }

        result = build_link_map(tree)

        assert "src/valid" in result
        assert len(result) == 1

    def test_build_link_map_uses_module_doc_filename(self):
        """Values match what module_doc_filename would produce for the module path."""
        from codewiki.src.be.generation.glossary import build_link_map
        from codewiki.src.utils import module_doc_filename

        tree = {
            "Auth Module": {
                "path": "src/auth",
                "components": [],
                "children": {
                    "Sub Module": {
                        "path": "src/auth/sub",
                        "components": [],
                        "children": {},
                    }
                },
            }
        }

        result = build_link_map(tree)

        # Top-level: module_doc_filename(["Auth Module"])
        expected_top = module_doc_filename(["Auth Module"])
        assert result["src/auth"] == expected_top

        # Nested: module_doc_filename(["Auth Module", "Sub Module"])
        expected_nested = module_doc_filename(["Auth Module", "Sub Module"])
        assert result["src/auth/sub"] == expected_nested

    def test_build_link_map_non_dict_values_skipped(self):
        """Non-dict values in module_tree are skipped without error."""
        from codewiki.src.be.generation.glossary import build_link_map

        tree = {
            "StringValue": "not a dict",
            "NumberValue": 42,
            "ValidModule": {
                "path": "src/valid",
                "components": [],
                "children": {},
            },
        }

        result = build_link_map(tree)

        assert list(result.keys()) == ["src/valid"]
