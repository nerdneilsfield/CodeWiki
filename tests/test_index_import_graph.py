# tests/test_index_import_graph.py
"""Tests for ImportGraph: file-level import edges and resolution."""
import pytest
from codewiki.src.be.index.models import ImportStatement, SymbolKind, SourceRange, Symbol
from codewiki.src.be.index.import_graph import ImportGraph
from codewiki.src.be.index.symbol_table import SymbolTable


def _imp(file_path, module_path, names=None, resolved=None, alias=None, line=1):
    return ImportStatement(
        file_path=file_path, module_path=module_path,
        imported_names=names or [], resolved_path=resolved,
        alias=alias, line=line,
    )


def _sym(sid, name, file_path="src/a.py", kind=SymbolKind.FUNCTION):
    return Symbol(
        symbol_id=sid, lang="python", kind=kind, name=name,
        qualified_name=f"src.a.{name}", file_path=file_path,
        range=SourceRange(file_path=file_path, start_line=1, start_col=0, end_line=1, end_col=0),
        source_hash="h",
    )


def test_imports_of():
    imp1 = _imp("src/main.py", "os.path", ["join"])
    imp2 = _imp("src/main.py", "sys")
    imp3 = _imp("src/other.py", "json")
    ig = ImportGraph([imp1, imp2, imp3])
    result = ig.imports_of("src/main.py")
    assert len(result) == 2


def test_imports_of_empty():
    ig = ImportGraph([])
    assert ig.imports_of("nonexistent.py") == []


def test_importers_of():
    imp1 = _imp("src/main.py", "./utils", resolved="src/utils.py")
    imp2 = _imp("src/api.py", "./utils", resolved="src/utils.py")
    ig = ImportGraph([imp1, imp2])
    result = ig.importers_of("src/utils.py")
    assert set(result) == {"src/main.py", "src/api.py"}


def test_importers_of_no_importers():
    ig = ImportGraph([_imp("src/main.py", "os")])
    assert ig.importers_of("src/standalone.py") == []


def test_file_dependency_graph():
    imp1 = _imp("src/a.py", "./b", resolved="src/b.py")
    imp2 = _imp("src/a.py", "./c", resolved="src/c.py")
    imp3 = _imp("src/b.py", "./c", resolved="src/c.py")
    ig = ImportGraph([imp1, imp2, imp3])
    graph = ig.file_dependency_graph()
    assert graph["src/a.py"] == {"src/b.py", "src/c.py"}
    assert graph["src/b.py"] == {"src/c.py"}


def test_file_dependency_graph_skips_unresolved():
    imp = _imp("src/a.py", "external_lib")  # no resolved_path
    ig = ImportGraph([imp])
    graph = ig.file_dependency_graph()
    assert graph.get("src/a.py", set()) == set()


def test_resolve_finds_symbol():
    imp = _imp("src/main.py", "./auth", ["LoginService"], resolved="src/auth.py")
    sym = _sym("py:src/auth.py#LoginService(class)", "LoginService", "src/auth.py", SymbolKind.CLASS)
    st = SymbolTable([sym])
    ig = ImportGraph([imp])
    result = ig.resolve("src/main.py", "LoginService", st)
    assert result is not None
    assert result.symbol_id == "py:src/auth.py#LoginService(class)"


def test_resolve_returns_none_for_unknown():
    ig = ImportGraph([])
    st = SymbolTable([])
    assert ig.resolve("src/main.py", "Unknown", st) is None


def test_all_imports():
    imps = [_imp("a.py", "x"), _imp("b.py", "y")]
    ig = ImportGraph(imps)
    assert len(ig.all_imports()) == 2
