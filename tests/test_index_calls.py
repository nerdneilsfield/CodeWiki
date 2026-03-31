# tests/test_index_calls.py
"""Tests for CALLS edge extraction in PythonIndexAdapter.

TDD cycle: write tests first (RED), implement to make them GREEN.
"""
import textwrap

import pytest

from codewiki.src.be.index.adapters.python_adapter import PythonIndexAdapter
from codewiki.src.be.index.import_graph import ImportGraph
from codewiki.src.be.index.models import Confidence, EdgeType, SymbolKind
from codewiki.src.be.index.symbol_table import SymbolTable


# ── Helpers ──────────────────────────────────────────────────────────────────


def _build_adapter(code: str, file_path: str, repo_path: str) -> PythonIndexAdapter:
    """Create a PythonIndexAdapter and run extract() so _tree and _symbols are populated."""
    code = textwrap.dedent(code)
    adapter = PythonIndexAdapter(
        file_path=str(repo_path / file_path),
        content=code,
        repo_path=str(repo_path),
    )
    adapter.extract()
    return adapter


def _make_products(adapters: list[PythonIndexAdapter]):
    """Build SymbolTable + ImportGraph from a list of already-extracted adapters."""
    all_symbols = []
    all_imports = []
    for adapter in adapters:
        all_symbols.extend(adapter._symbols)
        all_imports.extend(adapter._imports)
    return SymbolTable(all_symbols), ImportGraph(all_imports)


# ── Test: same-file function calls ──────────────────────────────────────────


def test_py_calls_same_file_function(tmp_path):
    """foo() calling bar() in the same file produces a HIGH-confidence CALLS edge."""
    code = """\
        def bar():
            pass

        def foo():
            bar()
    """
    (tmp_path / "main.py").write_text(textwrap.dedent(code))
    adapter = _build_adapter(code, "main.py", tmp_path)
    st, ig = _make_products([adapter])

    edges = adapter.extract_calls(st, ig)

    calls = [e for e in edges if e.edge_type == EdgeType.CALLS]
    assert len(calls) >= 1

    # Identify from/to symbols
    foo_sym = next(s for s in st.all_symbols() if s.name == "foo" and s.kind == SymbolKind.FUNCTION)
    bar_sym = next(s for s in st.all_symbols() if s.name == "bar" and s.kind == SymbolKind.FUNCTION)

    matching = [e for e in calls if e.from_symbol == foo_sym.symbol_id and e.to_symbol == bar_sym.symbol_id]
    assert len(matching) == 1, f"Expected foo->bar CALLS edge, got: {calls}"
    assert matching[0].confidence == Confidence.HIGH


# ── Test: imported function call ─────────────────────────────────────────────


def test_py_calls_imported_function(tmp_path):
    """Calling a function imported from another module resolves to the correct symbol."""
    utils_code = """\
        def helper():
            pass
    """
    main_code = """\
        from utils import helper

        def caller():
            helper()
    """
    (tmp_path / "utils.py").write_text(textwrap.dedent(utils_code))
    (tmp_path / "main.py").write_text(textwrap.dedent(main_code))

    utils_adapter = _build_adapter(utils_code, "utils.py", tmp_path)
    main_adapter = _build_adapter(main_code, "main.py", tmp_path)
    st, ig = _make_products([utils_adapter, main_adapter])

    edges = main_adapter.extract_calls(st, ig)

    calls = [e for e in edges if e.edge_type == EdgeType.CALLS]
    caller_sym = next(s for s in st.all_symbols() if s.name == "caller")
    helper_sym = next(s for s in st.all_symbols() if s.name == "helper")

    matching = [e for e in calls if e.from_symbol == caller_sym.symbol_id and e.to_symbol == helper_sym.symbol_id]
    assert len(matching) == 1, f"Expected caller->helper CALLS edge, got: {calls}"
    assert matching[0].confidence == Confidence.HIGH


# ── Test: self.method() call ─────────────────────────────────────────────────


def test_py_calls_self_method(tmp_path):
    """self.helper() inside a method produces a CALLS edge to the helper method."""
    code = """\
        class MyClass:
            def run(self):
                self.helper()

            def helper(self):
                pass
    """
    (tmp_path / "myclass.py").write_text(textwrap.dedent(code))
    adapter = _build_adapter(code, "myclass.py", tmp_path)
    st, ig = _make_products([adapter])

    edges = adapter.extract_calls(st, ig)

    calls = [e for e in edges if e.edge_type == EdgeType.CALLS]
    run_sym = next(s for s in st.all_symbols() if s.name == "run" and s.kind == SymbolKind.METHOD)
    helper_sym = next(s for s in st.all_symbols() if s.name == "helper" and s.kind == SymbolKind.METHOD)

    matching = [e for e in calls if e.from_symbol == run_sym.symbol_id and e.to_symbol == helper_sym.symbol_id]
    assert len(matching) == 1, f"Expected run->helper CALLS edge, got: {calls}"
    assert matching[0].confidence == Confidence.HIGH


# ── Test: unresolved external call ───────────────────────────────────────────


def test_py_calls_unresolved_external(tmp_path):
    """Calling os.path.join() produces a CALLS edge with to_unresolved and LOW confidence."""
    code = """\
        import os

        def build_path(name):
            return os.path.join("/tmp", name)
    """
    (tmp_path / "paths.py").write_text(textwrap.dedent(code))
    adapter = _build_adapter(code, "paths.py", tmp_path)
    st, ig = _make_products([adapter])

    edges = adapter.extract_calls(st, ig)

    calls = [e for e in edges if e.edge_type == EdgeType.CALLS]
    # os.path.join is external — to_symbol must be None, to_unresolved is set
    unresolved = [
        e for e in calls
        if e.to_symbol is None and e.to_unresolved is not None and "join" in e.to_unresolved
    ]
    assert len(unresolved) >= 1, f"Expected unresolved os.path.join edge, got: {calls}"
    assert all(e.confidence == Confidence.LOW for e in unresolved)


# ── Test: builtins are filtered ───────────────────────────────────────────────


def test_py_calls_builtins_filtered(tmp_path):
    """Calls to Python builtins (print, len) must NOT generate CALLS edges."""
    code = """\
        def process(items):
            print(len(items))
            return list(items)
    """
    (tmp_path / "proc.py").write_text(textwrap.dedent(code))
    adapter = _build_adapter(code, "proc.py", tmp_path)
    st, ig = _make_products([adapter])

    edges = adapter.extract_calls(st, ig)

    calls = [e for e in edges if e.edge_type == EdgeType.CALLS]
    builtin_names = {"print", "len", "list"}
    for edge in calls:
        assert edge.to_unresolved not in builtin_names, (
            f"Builtin call '{edge.to_unresolved}' should have been filtered: {edge}"
        )
        # If resolved, to_symbol points to a symbol whose name must not be a builtin
        if edge.to_symbol:
            sym = st.get(edge.to_symbol)
            if sym:
                assert sym.name not in builtin_names


# ── Test: evidence_refs line number ──────────────────────────────────────────


def test_py_calls_evidence_ref_correct(tmp_path):
    """evidence_refs[0].start_line must point to the call site line."""
    code = """\
        def bar():
            pass

        def foo():
            x = 1
            bar()
    """
    (tmp_path / "ev.py").write_text(textwrap.dedent(code))
    adapter = _build_adapter(code, "ev.py", tmp_path)
    st, ig = _make_products([adapter])

    edges = adapter.extract_calls(st, ig)

    bar_sym = next(s for s in st.all_symbols() if s.name == "bar" and s.kind == SymbolKind.FUNCTION)
    foo_sym = next(s for s in st.all_symbols() if s.name == "foo" and s.kind == SymbolKind.FUNCTION)

    matching = [e for e in edges if e.from_symbol == foo_sym.symbol_id and e.to_symbol == bar_sym.symbol_id]
    assert len(matching) == 1
    edge = matching[0]
    assert len(edge.evidence_refs) >= 1
    # bar() is called on line 6 (1-indexed) in the dedented source
    assert edge.evidence_refs[0].start_line == 6, (
        f"Expected call site at line 6, got {edge.evidence_refs[0].start_line}"
    )


# ── Test: nested calls ────────────────────────────────────────────────────────


def test_py_calls_nested_calls(tmp_path):
    """foo(bar()) must generate CALLS edges for BOTH foo and bar from the enclosing function."""
    code = """\
        def foo(x):
            pass

        def bar():
            return 42

        def outer():
            foo(bar())
    """
    (tmp_path / "nested.py").write_text(textwrap.dedent(code))
    adapter = _build_adapter(code, "nested.py", tmp_path)
    st, ig = _make_products([adapter])

    edges = adapter.extract_calls(st, ig)

    calls = [e for e in edges if e.edge_type == EdgeType.CALLS]
    outer_sym = next(s for s in st.all_symbols() if s.name == "outer")
    foo_sym = next(s for s in st.all_symbols() if s.name == "foo" and s.kind == SymbolKind.FUNCTION)
    bar_sym = next(s for s in st.all_symbols() if s.name == "bar" and s.kind == SymbolKind.FUNCTION)

    outer_to_foo = [e for e in calls if e.from_symbol == outer_sym.symbol_id and e.to_symbol == foo_sym.symbol_id]
    outer_to_bar = [e for e in calls if e.from_symbol == outer_sym.symbol_id and e.to_symbol == bar_sym.symbol_id]

    assert len(outer_to_foo) >= 1, f"Expected outer->foo edge, got: {calls}"
    assert len(outer_to_bar) >= 1, f"Expected outer->bar edge, got: {calls}"


# ── Test: class instantiation ─────────────────────────────────────────────────


def test_py_calls_class_instantiation(tmp_path):
    """MyClass() instantiation inside a function produces a CALLS edge to the class symbol."""
    code = """\
        class MyClass:
            def __init__(self):
                pass

        def make():
            obj = MyClass()
            return obj
    """
    (tmp_path / "inst.py").write_text(textwrap.dedent(code))
    adapter = _build_adapter(code, "inst.py", tmp_path)
    st, ig = _make_products([adapter])

    edges = adapter.extract_calls(st, ig)

    calls = [e for e in edges if e.edge_type == EdgeType.CALLS]
    make_sym = next(s for s in st.all_symbols() if s.name == "make" and s.kind == SymbolKind.FUNCTION)
    myclass_sym = next(s for s in st.all_symbols() if s.name == "MyClass" and s.kind == SymbolKind.CLASS)

    matching = [e for e in calls if e.from_symbol == make_sym.symbol_id and e.to_symbol == myclass_sym.symbol_id]
    assert len(matching) >= 1, f"Expected make->MyClass CALLS edge, got: {calls}"


# ── Test: two-pass IndexBuilder integration ───────────────────────────────────


def test_index_builder_two_pass_produces_calls_edges(tmp_path):
    """IndexBuilder.build() with two-pass must include CALLS edges in products.edges."""
    from codewiki.src.be.index.index_builder import IndexBuilder

    (tmp_path / "funcs.py").write_text(textwrap.dedent("""\
        def bar():
            pass

        def foo():
            bar()
    """))
    builder = IndexBuilder(repo_path=str(tmp_path))
    products = builder.build()

    calls = [e for e in products.edges if e.edge_type == EdgeType.CALLS]
    assert len(calls) >= 1, f"Expected CALLS edges in IndexBuilder output, got: {products.edges}"


# ── Test: resolver field ──────────────────────────────────────────────────────


def test_py_calls_resolver_is_ast(tmp_path):
    """All CALLS edges produced by PythonIndexAdapter must have resolver='ast'."""
    code = """\
        def bar():
            pass

        def foo():
            bar()
    """
    (tmp_path / "res.py").write_text(textwrap.dedent(code))
    adapter = _build_adapter(code, "res.py", tmp_path)
    st, ig = _make_products([adapter])

    edges = adapter.extract_calls(st, ig)

    calls = [e for e in edges if e.edge_type == EdgeType.CALLS]
    assert len(calls) >= 1
    for edge in calls:
        assert edge.resolver == "ast", f"Expected resolver='ast', got '{edge.resolver}'"


# ── Test: no tree, no crash ───────────────────────────────────────────────────


def test_py_calls_returns_empty_when_no_tree(tmp_path):
    """extract_calls() on a file with a syntax error returns an empty list (no crash)."""
    bad_code = "def foo(:\n    pass\n"
    (tmp_path / "bad.py").write_text(bad_code)
    adapter = PythonIndexAdapter(
        file_path=str(tmp_path / "bad.py"),
        content=bad_code,
        repo_path=str(tmp_path),
    )
    adapter.extract()  # SyntaxError is swallowed inside extract()
    st = SymbolTable([])
    ig = ImportGraph([])

    edges = adapter.extract_calls(st, ig)
    assert edges == []


# ── Test: evidence_refs file_path is relative ─────────────────────────────────


def test_py_calls_evidence_ref_file_path_is_relative(tmp_path):
    """evidence_refs[0].file_path must be repo-relative, not absolute."""
    code = """\
        def bar():
            pass

        def foo():
            bar()
    """
    (tmp_path / "rel.py").write_text(textwrap.dedent(code))
    adapter = _build_adapter(code, "rel.py", tmp_path)
    st, ig = _make_products([adapter])

    edges = adapter.extract_calls(st, ig)
    calls = [e for e in edges if e.edge_type == EdgeType.CALLS]
    assert len(calls) >= 1
    for edge in calls:
        for ref in edge.evidence_refs:
            assert not ref.file_path.startswith("/"), (
                f"evidence_refs file_path must be relative, got: {ref.file_path}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# TS/JS CALLS edge extraction tests (TSJSIndexAdapter.extract_calls)
# ═══════════════════════════════════════════════════════════════════════════════

import textwrap

from codewiki.src.be.index.adapters.ts_js_adapter import TSJSIndexAdapter
from codewiki.src.be.index.models import SymbolEdge


def _build_ts_adapter(code: str, file_name: str, tmp_path) -> TSJSIndexAdapter:
    """Create a TSJSIndexAdapter, run extract(), and return it."""
    code = textwrap.dedent(code)
    fpath = tmp_path / file_name
    fpath.write_text(code)
    adapter = TSJSIndexAdapter(
        file_path=str(fpath),
        content=code,
        repo_path=str(tmp_path),
        language="typescript",
    )
    adapter.extract()
    return adapter


def _make_ts_products(adapters):
    """Build SymbolTable + ImportGraph from a list of already-extracted adapters."""
    all_symbols = []
    all_imports = []
    for adapter in adapters:
        all_symbols.extend(adapter._symbols)
        all_imports.extend(adapter._imports)
    return SymbolTable(all_symbols), ImportGraph(all_imports)


# ── TS: simple function calling another function ──────────────────────────────


def test_ts_calls_function_call(tmp_path):
    """foo() calling bar(), both exported — produces a CALLS edge foo->bar."""
    code = """\
        export function bar(): void {}

        export function foo(): void {
            bar();
        }
    """
    adapter = _build_ts_adapter(code, "main.ts", tmp_path)
    st, ig = _make_ts_products([adapter])

    edges = adapter.extract_calls(st, ig)

    calls = [e for e in edges if e.edge_type == EdgeType.CALLS]
    assert len(calls) >= 1, f"Expected at least one CALLS edge, got: {edges}"

    foo_sym = next((s for s in st.all_symbols() if s.name == "foo"), None)
    bar_sym = next((s for s in st.all_symbols() if s.name == "bar"), None)
    assert foo_sym is not None, "foo symbol not found"
    assert bar_sym is not None, "bar symbol not found"

    matching = [
        e for e in calls
        if e.from_symbol == foo_sym.symbol_id and e.to_symbol == bar_sym.symbol_id
    ]
    assert len(matching) >= 1, (
        f"Expected foo->bar CALLS edge. from={foo_sym.symbol_id}, to={bar_sym.symbol_id}. Got: {calls}"
    )
    assert matching[0].confidence == Confidence.HIGH


# ── TS: this.method() call inside a class ────────────────────────────────────


def test_ts_calls_this_method(tmp_path):
    """Class method calling this.helper() produces a resolved CALLS edge."""
    code = """\
        export class MyService {
            run(): void {
                this.helper();
            }

            helper(): void {}
        }
    """
    adapter = _build_ts_adapter(code, "service.ts", tmp_path)
    st, ig = _make_ts_products([adapter])

    edges = adapter.extract_calls(st, ig)

    calls = [e for e in edges if e.edge_type == EdgeType.CALLS]

    run_sym = next((s for s in st.all_symbols() if s.name == "run"), None)
    helper_sym = next((s for s in st.all_symbols() if s.name == "helper"), None)
    assert run_sym is not None, "run symbol not found"
    assert helper_sym is not None, "helper symbol not found"

    matching = [
        e for e in calls
        if e.from_symbol == run_sym.symbol_id and e.to_symbol == helper_sym.symbol_id
    ]
    assert len(matching) >= 1, (
        f"Expected run->helper CALLS edge. Got: {calls}"
    )


# ── TS: calling an imported symbol ────────────────────────────────────────────


def test_ts_calls_imported_symbol(tmp_path):
    """import { helper } from './utils'; helper() → resolved edge to utils symbol."""
    utils_code = """\
        export function helper(): void {}
    """
    main_code = """\
        import { helper } from './utils';

        export function caller(): void {
            helper();
        }
    """
    utils_adapter = _build_ts_adapter(utils_code, "utils.ts", tmp_path)
    main_adapter = _build_ts_adapter(main_code, "main.ts", tmp_path)
    st, ig = _make_ts_products([utils_adapter, main_adapter])

    edges = main_adapter.extract_calls(st, ig)

    calls = [e for e in edges if e.edge_type == EdgeType.CALLS]

    caller_sym = next((s for s in st.all_symbols() if s.name == "caller"), None)
    helper_sym = next((s for s in st.all_symbols() if s.name == "helper"), None)
    assert caller_sym is not None, "caller symbol not found"
    assert helper_sym is not None, "helper symbol not found"

    matching = [
        e for e in calls
        if e.from_symbol == caller_sym.symbol_id and e.to_symbol == helper_sym.symbol_id
    ]
    assert len(matching) >= 1, (
        f"Expected caller->helper resolved CALLS edge. Got: {calls}"
    )
    assert matching[0].confidence == Confidence.HIGH


# ── TS: calling an unknown global → unresolved + LOW ─────────────────────────


def test_ts_calls_unresolved(tmp_path):
    """Calling unknownGlobal() → to_unresolved is set, confidence=LOW."""
    code = """\
        export function doWork(): void {
            unknownGlobal();
        }
    """
    adapter = _build_ts_adapter(code, "work.ts", tmp_path)
    st, ig = _make_ts_products([adapter])

    edges = adapter.extract_calls(st, ig)

    calls = [e for e in edges if e.edge_type == EdgeType.CALLS]

    unresolved = [
        e for e in calls
        if e.to_symbol is None
        and e.to_unresolved is not None
        and "unknownGlobal" in e.to_unresolved
    ]
    assert len(unresolved) >= 1, f"Expected unresolved edge for unknownGlobal. Got: {calls}"
    assert all(e.confidence == Confidence.LOW for e in unresolved)


# ── TS: JS builtins are filtered out ─────────────────────────────────────────


def test_ts_calls_builtins_filtered(tmp_path):
    """console.log, JSON.stringify, setTimeout must NOT generate CALLS edges."""
    code = """\
        export function logAndSerialise(obj: object): void {
            console.log(JSON.stringify(obj));
            setTimeout(() => {}, 0);
        }
    """
    adapter = _build_ts_adapter(code, "builtin.ts", tmp_path)
    st, ig = _make_ts_products([adapter])

    edges = adapter.extract_calls(st, ig)

    calls = [e for e in edges if e.edge_type == EdgeType.CALLS]
    filtered_names = {
        "console.log", "console.warn", "console.error",
        "setTimeout", "setInterval",
        "JSON.stringify", "JSON.parse",
        "parseInt", "parseFloat",
        "Array.isArray", "Object.keys", "Object.values",
        "Promise.resolve",
    }
    for edge in calls:
        assert edge.to_unresolved not in filtered_names, (
            f"Builtin '{edge.to_unresolved}' should have been filtered: {edge}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Generic adapter CALLS conversion tests (GenericIndexAdapter.convert_calls)
# ═══════════════════════════════════════════════════════════════════════════════

from codewiki.src.be.index.adapters.generic_adapter import GenericIndexAdapter
from codewiki.src.be.dependency_analyzer.models.core import CallRelationship
from codewiki.src.be.index.models import Symbol, SymbolKind, Visibility, ExportStatus, SourceRange


def _make_symbol(name: str, qualified_name: str, file_path: str = "mod.py",
                 kind: SymbolKind = SymbolKind.FUNCTION) -> Symbol:
    return Symbol(
        symbol_id=f"py:{file_path}#{name}(function)",
        lang="python",
        kind=kind,
        name=name,
        qualified_name=qualified_name,
        file_path=file_path,
        range=SourceRange(file_path=file_path, start_line=1, start_col=0, end_line=5, end_col=0),
        signature=f"def {name}()",
        visibility=Visibility.PUBLIC,
        export_status=ExportStatus.NOT_EXPORTED,
        source_hash="deadbeef12345678",
    )


# ── Generic: resolved CallRelationship → HIGH confidence edge ─────────────────


def test_generic_calls_resolved(tmp_path):
    """CallRelationship(is_resolved=True) with both symbols in table → HIGH confidence CALLS edge."""
    caller_sym = _make_symbol("caller_fn", "mod.caller_fn")
    callee_sym = _make_symbol("callee_fn", "mod.callee_fn")
    st = SymbolTable([caller_sym, callee_sym])

    rel = CallRelationship(
        caller="mod.caller_fn",
        callee="mod.callee_fn",
        call_line=10,
        is_resolved=True,
    )

    edges = GenericIndexAdapter.convert_calls([rel], st)

    assert len(edges) >= 1
    edge = edges[0]
    assert edge.edge_type == EdgeType.CALLS
    assert edge.from_symbol == caller_sym.symbol_id
    assert edge.to_symbol == callee_sym.symbol_id
    assert edge.to_unresolved is None
    assert edge.confidence == Confidence.HIGH


# ── Generic: unresolved CallRelationship → to_unresolved + LOW confidence ─────


def test_generic_calls_unresolved(tmp_path):
    """CallRelationship where callee is not in the symbol table → to_unresolved set, LOW confidence."""
    caller_sym = _make_symbol("do_work", "mymod.do_work")
    st = SymbolTable([caller_sym])

    rel = CallRelationship(
        caller="mymod.do_work",
        callee="external.lib.process",
        call_line=5,
        is_resolved=False,
    )

    edges = GenericIndexAdapter.convert_calls([rel], st)

    assert len(edges) >= 1
    edge = edges[0]
    assert edge.edge_type == EdgeType.CALLS
    assert edge.to_symbol is None
    assert edge.to_unresolved == "external.lib.process"
    assert edge.confidence == Confidence.LOW


# ── Generic: call_line maps to evidence_refs correctly ────────────────────────


def test_generic_calls_evidence_ref(tmp_path):
    """call_line on CallRelationship maps to evidence_refs[0].start_line."""
    caller_sym = _make_symbol("alpha", "pkg.alpha", file_path="pkg/alpha.py")
    callee_sym = _make_symbol("beta", "pkg.beta", file_path="pkg/beta.py")
    st = SymbolTable([caller_sym, callee_sym])

    rel = CallRelationship(
        caller="pkg.alpha",
        callee="pkg.beta",
        call_line=42,
        is_resolved=True,
    )

    edges = GenericIndexAdapter.convert_calls([rel], st)

    assert len(edges) >= 1
    edge = edges[0]
    assert len(edge.evidence_refs) >= 1
    assert edge.evidence_refs[0].start_line == 42, (
        f"Expected start_line=42, got {edge.evidence_refs[0].start_line}"
    )
    assert edge.evidence_refs[0].file_path == caller_sym.file_path


# ── Generic: empty list returns empty list ────────────────────────────────────


def test_generic_calls_empty_input():
    """convert_calls([]) must return an empty list without error."""
    st = SymbolTable([])
    result = GenericIndexAdapter.convert_calls([], st)
    assert result == []


# ── Generic: resolver field is 'call_graph_analyzer' ────────────────────────


def test_generic_calls_resolver_field():
    """All edges produced by convert_calls must have resolver='call_graph_analyzer'."""
    caller_sym = _make_symbol("fn_a", "mod.fn_a")
    st = SymbolTable([caller_sym])

    rel = CallRelationship(
        caller="mod.fn_a",
        callee="mod.fn_b",
        call_line=1,
        is_resolved=False,
    )
    edges = GenericIndexAdapter.convert_calls([rel], st)

    assert len(edges) >= 1
    for edge in edges:
        assert edge.resolver == "call_graph_analyzer", (
            f"Expected resolver='call_graph_analyzer', got '{edge.resolver}'"
        )


def test_generic_calls_unresolved_caller_still_has_evidence():
    """When caller is not in SymbolTable, edge must still have evidence_refs (data contract)."""
    st = SymbolTable([])  # empty — caller won't resolve

    rel = CallRelationship(
        caller="unknown.module.func",
        callee="other.func",
        call_line=99,
        is_resolved=False,
    )
    edges = GenericIndexAdapter.convert_calls([rel], st)

    assert len(edges) == 1
    edge = edges[0]
    assert edge.from_symbol.startswith("unresolved:")
    assert len(edge.evidence_refs) >= 1, "Evidence refs must not be empty even for unresolved caller"
    assert edge.evidence_refs[0].start_line == 99
