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

    def test_build_glossary_private_only_excluded(self):
        """Symbols with Visibility.PRIVATE are excluded from glossary."""
        from codewiki.src.be.generation.glossary import build_glossary

        sym = _make_symbol("Hidden", export_status=ExportStatus.EXPORTED, docstring="Secret.")
        # Override visibility to PRIVATE
        sym = sym.model_copy(update={"visibility": Visibility.PRIVATE})
        index_products = _FakeIndexProducts([sym])

        result = build_glossary(index_products)

        assert result == {}

    def test_build_glossary_not_exported_symbols_excluded(self):
        """Symbols with NOT_EXPORTED export_status are excluded from glossary."""
        from codewiki.src.be.generation.glossary import build_glossary

        sym = _make_symbol(
            "Internal", export_status=ExportStatus.NOT_EXPORTED, docstring="Private."
        )
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
        entry = result["Nodoc"]
        assert entry.kind == "class"
        assert entry.file_path == "src/core.py"

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

        entry = result["Verbose"]
        assert "Short summary." in entry.definition
        assert "Extra detail here" not in entry.definition

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

        entry = result["MyFunc"]
        assert entry.kind == "function"
        assert entry.file_path == "src/utils.py"

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
                "_doc_filename": "src_auth.md",
                "components": ["AuthService"],
                "children": {},
            },
            "User Module": {
                "path": "src/user",
                "_doc_filename": "src_user.md",
                "components": ["UserService"],
                "children": {},
            },
        }

        result = build_link_map(tree)

        assert len(result) == 2
        assert "Auth Module" in result
        assert "User Module" in result

    def test_build_link_map_nested(self):
        """Nested children are included in the link map."""
        from codewiki.src.be.generation.glossary import build_link_map

        tree = {
            "Auth Module": {
                "path": "src/auth",
                "_doc_filename": "src_auth.md",
                "components": ["AuthService"],
                "children": {
                    "Sub Module": {
                        "path": "src/auth/sub",
                        "_doc_filename": "src_auth-sub_module.md",
                        "components": ["SubService"],
                        "children": {},
                    }
                },
            }
        }

        result = build_link_map(tree)

        assert "Auth Module" in result
        assert "Auth Module/Sub Module" in result

    def test_build_link_map_empty_tree(self):
        """Empty module tree returns empty dict."""
        from codewiki.src.be.generation.glossary import build_link_map

        result = build_link_map({})

        assert result == {}

    def test_build_link_map_uses_title_path_as_key(self):
        """Keys in link_map come from title path, values from frozen doc filename."""
        from codewiki.src.be.generation.glossary import build_link_map

        tree = {
            "Some Long Human Title": {
                "path": "mod/stable_path",
                "_doc_filename": "mod_stable_path.md",
                "components": [],
                "children": {},
            }
        }

        result = build_link_map(tree)

        assert "Some Long Human Title" in result
        assert result["Some Long Human Title"] == "mod_stable_path.md"

    def test_build_link_map_sorted(self):
        """Link map entries are sorted by key (title path)."""
        from codewiki.src.be.generation.glossary import build_link_map

        tree = {
            "Z Module": {
                "path": "src/z",
                "_doc_filename": "src_z.md",
                "components": [],
                "children": {},
            },
            "A Module": {
                "path": "src/a",
                "_doc_filename": "src_a.md",
                "components": [],
                "children": {},
            },
            "M Module": {
                "path": "src/m",
                "_doc_filename": "src_m.md",
                "components": [],
                "children": {},
            },
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
                "_doc_filename": "src_auth.md",
                "components": [],
                "children": {},
            }
        }

        result = build_link_map(tree)

        filename = result["Auth Module"]
        assert filename.endswith(".md")

    def test_build_link_map_falls_back_without_doc_filename(self):
        """Nodes without _doc_filename fall back to module_doc_filename()."""
        from codewiki.src.be.generation.glossary import build_link_map
        from codewiki.src.utils import module_doc_filename

        tree = {
            "Pathless": {"components": ["X"], "children": {}},
            "HasPath": {"path": "src/valid", "components": [], "children": {}},
        }

        result = build_link_map(tree)

        assert result["Pathless"] == module_doc_filename(["Pathless"])
        assert result["HasPath"] == module_doc_filename(["src/valid"])

    def test_build_link_map_reads_frozen_doc_filename(self):
        """Frozen _doc_filename wins over recomputation."""
        from codewiki.src.be.generation.glossary import build_link_map

        tree = {
            "Auth Module": {
                "path": "src/auth",
                "_doc_filename": "cli.md",
                "components": [],
                "children": {
                    "Sub Module": {
                        "path": "src/auth/sub",
                        "_doc_filename": "cli-sub_module.md",
                        "components": [],
                        "children": {},
                    }
                },
            }
        }

        result = build_link_map(tree)

        assert result["Auth Module"] == "cli.md"
        assert result["Auth Module/Sub Module"] == "cli-sub_module.md"

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

        assert list(result.keys()) == ["ValidModule"]

    def test_build_link_map_nested_keys_use_full_title_path(self):
        from codewiki.src.be.generation.glossary import build_link_map

        tree = {
            "Parent": {
                "path": "src/parent",
                "_doc_filename": "parent.md",
                "components": [],
                "children": {
                    "Child": {
                        "path": "",
                        "_doc_filename": "parent-child.md",
                        "components": [],
                        "children": {},
                    }
                },
            }
        }

        result = build_link_map(tree)

        assert set(result) == {"Parent", "Parent/Child"}
