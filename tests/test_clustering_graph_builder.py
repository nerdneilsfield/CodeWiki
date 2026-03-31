"""Tests for clustering weighted graph builder.

TDD: tests written before implementation.
"""
import pytest
import networkx as nx

from codewiki.src.be.index.models import SymbolEdge, EdgeType, Confidence
from codewiki.src.be.clustering.graph_builder import (
    build_clustering_graph,
    _extract_file_from_symbol,
    WEIGHT_MAP,
    CO_LOCATION_WEIGHT,
    MAX_EDGE_WEIGHT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _edge(
    from_sym: str,
    to_sym: str | None,
    edge_type: EdgeType = EdgeType.IMPORTS,
    confidence: Confidence = Confidence.HIGH,
) -> SymbolEdge:
    """Convenience factory for SymbolEdge test fixtures."""
    return SymbolEdge(
        edge_type=edge_type,
        from_symbol=from_sym,
        to_symbol=to_sym,
        confidence=confidence,
    )


# Standard two-component fixture shared by several tests
_COMP_A = "src/a.py::Foo"
_COMP_B = "src/b.py::Bar"

_FILE_MAP_AB: dict[str, str] = {
    _COMP_A: "src/a.py",
    _COMP_B: "src/b.py",
}

_SYMBOL_A = "py:src/a.py#Foo(class)"
_SYMBOL_B = "py:src/b.py#Bar(class)"


# ---------------------------------------------------------------------------
# _extract_file_from_symbol unit tests
# ---------------------------------------------------------------------------

class TestExtractFileFromSymbol:
    def test_lang_prefixed_format(self):
        assert _extract_file_from_symbol("py:src/auth/login.py#LoginService(class)") == "src/auth/login.py"

    def test_ts_format(self):
        assert _extract_file_from_symbol("ts:src/app.ts#AppService(class)") == "src/app.ts"

    def test_file_prefix_format(self):
        assert _extract_file_from_symbol("file:src/main.py") == "src/main.py"

    def test_unknown_format_returns_empty(self):
        assert _extract_file_from_symbol("some_random_id") == ""

    def test_empty_string_returns_empty(self):
        assert _extract_file_from_symbol("") == ""


# ---------------------------------------------------------------------------
# Weight constants sanity
# ---------------------------------------------------------------------------

class TestWeightConstants:
    def test_imports_high_weight(self):
        assert WEIGHT_MAP[(EdgeType.IMPORTS, Confidence.HIGH)] == 1.0

    def test_imports_medium_weight(self):
        assert WEIGHT_MAP[(EdgeType.IMPORTS, Confidence.MEDIUM)] == 1.0

    def test_imports_low_weight(self):
        assert WEIGHT_MAP[(EdgeType.IMPORTS, Confidence.LOW)] == 0.7

    def test_extends_high_weight(self):
        assert WEIGHT_MAP[(EdgeType.EXTENDS, Confidence.HIGH)] == 1.0

    def test_extends_medium_weight(self):
        assert WEIGHT_MAP[(EdgeType.EXTENDS, Confidence.MEDIUM)] == 1.0

    def test_extends_low_weight(self):
        assert WEIGHT_MAP[(EdgeType.EXTENDS, Confidence.LOW)] == 0.7

    def test_calls_high_weight(self):
        assert WEIGHT_MAP[(EdgeType.CALLS, Confidence.HIGH)] == 0.5

    def test_calls_medium_weight(self):
        assert WEIGHT_MAP[(EdgeType.CALLS, Confidence.MEDIUM)] == 0.3

    def test_calls_low_weight(self):
        assert WEIGHT_MAP[(EdgeType.CALLS, Confidence.LOW)] == 0.2

    def test_co_location_weight_value(self):
        assert CO_LOCATION_WEIGHT == 0.3

    def test_max_edge_weight_value(self):
        assert MAX_EDGE_WEIGHT == 3.0


# ---------------------------------------------------------------------------
# build_clustering_graph — individual edge type / confidence tests
# ---------------------------------------------------------------------------

class TestImportsEdgeWeight:
    """Test 1: IMPORTS HIGH → weight 1.0 on graph edge."""

    def test_imports_high_produces_weight_1(self):
        edges = [_edge(_SYMBOL_A, _SYMBOL_B, EdgeType.IMPORTS, Confidence.HIGH)]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        assert g.has_edge(_COMP_A, _COMP_B)
        w = g[_COMP_A][_COMP_B]["weight"]
        # IMPORTS HIGH weight (1.0) + co-location is not applied (different files)
        assert w == pytest.approx(1.0)

    def test_imports_medium_produces_weight_1(self):
        edges = [_edge(_SYMBOL_A, _SYMBOL_B, EdgeType.IMPORTS, Confidence.MEDIUM)]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        assert g[_COMP_A][_COMP_B]["weight"] == pytest.approx(1.0)

    def test_imports_low_produces_weight_0_7(self):
        edges = [_edge(_SYMBOL_A, _SYMBOL_B, EdgeType.IMPORTS, Confidence.LOW)]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        assert g[_COMP_A][_COMP_B]["weight"] == pytest.approx(0.7)


class TestCallsEdgeWeightByConfidence:
    """Test 2: CALLS HIGH=0.5, MEDIUM=0.3, LOW=0.2."""

    def test_calls_high(self):
        edges = [_edge(_SYMBOL_A, _SYMBOL_B, EdgeType.CALLS, Confidence.HIGH)]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        assert g[_COMP_A][_COMP_B]["weight"] == pytest.approx(0.5)

    def test_calls_medium(self):
        edges = [_edge(_SYMBOL_A, _SYMBOL_B, EdgeType.CALLS, Confidence.MEDIUM)]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        assert g[_COMP_A][_COMP_B]["weight"] == pytest.approx(0.3)

    def test_calls_low(self):
        edges = [_edge(_SYMBOL_A, _SYMBOL_B, EdgeType.CALLS, Confidence.LOW)]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        assert g[_COMP_A][_COMP_B]["weight"] == pytest.approx(0.2)


class TestExtendsEdgeWeight:
    """Test 3: EXTENDS → weight 1.0 regardless of confidence (HIGH/MEDIUM), 0.7 for LOW."""

    def test_extends_high(self):
        edges = [_edge(_SYMBOL_A, _SYMBOL_B, EdgeType.EXTENDS, Confidence.HIGH)]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        assert g[_COMP_A][_COMP_B]["weight"] == pytest.approx(1.0)

    def test_extends_medium(self):
        edges = [_edge(_SYMBOL_A, _SYMBOL_B, EdgeType.EXTENDS, Confidence.MEDIUM)]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        assert g[_COMP_A][_COMP_B]["weight"] == pytest.approx(1.0)

    def test_extends_low(self):
        edges = [_edge(_SYMBOL_A, _SYMBOL_B, EdgeType.EXTENDS, Confidence.LOW)]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        assert g[_COMP_A][_COMP_B]["weight"] == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# Weight accumulation
# ---------------------------------------------------------------------------

class TestWeightAccumulation:
    """Test 4: 2 IMPORTS edges between same pair → accumulated weight 2.0."""

    def test_two_imports_accumulate(self):
        symbol_a2 = "py:src/a.py#Baz(function)"
        symbol_b2 = "py:src/b.py#Qux(function)"
        edges = [
            _edge(_SYMBOL_A, _SYMBOL_B, EdgeType.IMPORTS, Confidence.HIGH),
            _edge(symbol_a2, symbol_b2, EdgeType.IMPORTS, Confidence.HIGH),
        ]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        # Each IMPORTS HIGH = 1.0 → total 2.0
        assert g[_COMP_A][_COMP_B]["weight"] == pytest.approx(2.0)

    def test_mixed_types_accumulate(self):
        symbol_a2 = "py:src/a.py#Baz(function)"
        symbol_b2 = "py:src/b.py#Qux(function)"
        edges = [
            _edge(_SYMBOL_A, _SYMBOL_B, EdgeType.IMPORTS, Confidence.HIGH),   # 1.0
            _edge(symbol_a2, symbol_b2, EdgeType.CALLS, Confidence.HIGH),      # 0.5
        ]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        assert g[_COMP_A][_COMP_B]["weight"] == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# Weight cap
# ---------------------------------------------------------------------------

class TestWeightCap:
    """Test 5: many edges between same pair → capped at MAX_EDGE_WEIGHT (3.0)."""

    def test_cap_at_max(self):
        syms_a = [f"py:src/a.py#Sym{i}(function)" for i in range(10)]
        syms_b = [f"py:src/b.py#Sym{i}(function)" for i in range(10)]
        edges = [
            _edge(syms_a[i], syms_b[i], EdgeType.IMPORTS, Confidence.HIGH)
            for i in range(10)
        ]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        w = g[_COMP_A][_COMP_B]["weight"]
        assert w == pytest.approx(MAX_EDGE_WEIGHT)

    def test_cap_is_exact_max(self):
        """Weight of exactly MAX_EDGE_WEIGHT from 3 IMPORTS HIGH edges passes uncapped."""
        syms_a = [f"py:src/a.py#Sym{i}(function)" for i in range(3)]
        syms_b = [f"py:src/b.py#Sym{i}(function)" for i in range(3)]
        edges = [
            _edge(syms_a[i], syms_b[i], EdgeType.IMPORTS, Confidence.HIGH)
            for i in range(3)
        ]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        assert g[_COMP_A][_COMP_B]["weight"] == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Co-location edges
# ---------------------------------------------------------------------------

class TestCoLocationEdges:
    """Test 6: 2 components in same file → edge with CO_LOCATION_WEIGHT."""

    def test_same_file_adds_co_location_edge(self):
        comp_a1 = "src/shared.py::Alpha"
        comp_a2 = "src/shared.py::Beta"
        file_map = {comp_a1: "src/shared.py", comp_a2: "src/shared.py"}
        g = build_clustering_graph([], {comp_a1, comp_a2}, file_map)
        assert g.has_edge(comp_a1, comp_a2)
        assert g[comp_a1][comp_a2]["weight"] == pytest.approx(CO_LOCATION_WEIGHT)

    def test_three_same_file_components_all_connected(self):
        comps = ["src/shared.py::A", "src/shared.py::B", "src/shared.py::C"]
        file_map = {c: "src/shared.py" for c in comps}
        g = build_clustering_graph([], set(comps), file_map)
        assert g.has_edge(comps[0], comps[1])
        assert g.has_edge(comps[0], comps[2])
        assert g.has_edge(comps[1], comps[2])

    def test_co_location_accumulates_with_symbol_edges(self):
        """Same-file co-location weight accumulates on top of symbol edge weight."""
        comp_a1 = "src/shared.py::Alpha"
        comp_a2 = "src/shared.py::Beta"
        sym_a1 = "py:src/shared.py#Alpha(class)"
        sym_a2 = "py:src/shared.py#Beta(class)"
        file_map = {comp_a1: "src/shared.py", comp_a2: "src/shared.py"}
        edges = [_edge(sym_a1, sym_a2, EdgeType.CALLS, Confidence.HIGH)]  # 0.5
        g = build_clustering_graph(edges, {comp_a1, comp_a2}, file_map)
        # 0.5 (CALLS HIGH) + 0.3 (co-location) = 0.8
        assert g[comp_a1][comp_a2]["weight"] == pytest.approx(0.8)

    def test_different_files_no_co_location(self):
        g = build_clustering_graph([], {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        assert not g.has_edge(_COMP_A, _COMP_B)


# ---------------------------------------------------------------------------
# Isolated nodes
# ---------------------------------------------------------------------------

class TestIsolatedNodesPresent:
    """Test 7: component with no edges still in graph."""

    def test_isolated_node_in_graph(self):
        comp_isolated = "src/isolated.py::Loner"
        file_map = {**_FILE_MAP_AB, comp_isolated: "src/isolated.py"}
        edges = [_edge(_SYMBOL_A, _SYMBOL_B, EdgeType.IMPORTS, Confidence.HIGH)]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B, comp_isolated}, file_map)
        assert comp_isolated in g.nodes

    def test_isolated_node_has_no_edges(self):
        comp_isolated = "src/isolated.py::Loner"
        file_map = {**_FILE_MAP_AB, comp_isolated: "src/isolated.py"}
        edges = [_edge(_SYMBOL_A, _SYMBOL_B, EdgeType.IMPORTS, Confidence.HIGH)]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B, comp_isolated}, file_map)
        assert g.degree(comp_isolated) == 0

    def test_all_component_ids_are_nodes(self):
        comp_ids = {_COMP_A, _COMP_B, "src/c.py::Gamma", "src/d.py::Delta"}
        file_map = {c: c.split("::")[0] for c in comp_ids}
        g = build_clustering_graph([], comp_ids, file_map)
        assert set(g.nodes) == comp_ids


# ---------------------------------------------------------------------------
# Unresolved edges excluded
# ---------------------------------------------------------------------------

class TestUnresolvedEdgesExcluded:
    """Test 8: edge with to_symbol=None → no graph edge."""

    def test_unresolved_to_symbol_skipped(self):
        edges = [
            SymbolEdge(
                edge_type=EdgeType.IMPORTS,
                from_symbol=_SYMBOL_A,
                to_symbol=None,
                to_unresolved="some_external_lib",
                confidence=Confidence.HIGH,
            )
        ]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        assert not g.has_edge(_COMP_A, _COMP_B)

    def test_mix_resolved_and_unresolved(self):
        edges = [
            SymbolEdge(
                edge_type=EdgeType.IMPORTS,
                from_symbol=_SYMBOL_A,
                to_symbol=None,
                to_unresolved="external",
                confidence=Confidence.LOW,
            ),
            _edge(_SYMBOL_A, _SYMBOL_B, EdgeType.CALLS, Confidence.MEDIUM),
        ]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        # Only the resolved CALLS MEDIUM edge (0.3) should be present
        assert g.has_edge(_COMP_A, _COMP_B)
        assert g[_COMP_A][_COMP_B]["weight"] == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# No self-loops
# ---------------------------------------------------------------------------

class TestNoSelfLoops:
    """Test 9: edge from component to itself → not in graph."""

    def test_intra_component_edge_skipped(self):
        sym_a1 = "py:src/a.py#FooInit(method)"
        sym_a2 = "py:src/a.py#FooCall(method)"
        edges = [_edge(sym_a1, sym_a2, EdgeType.CALLS, Confidence.HIGH)]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        # Both symbols belong to the same file/component — no self-loop
        assert not g.has_edge(_COMP_A, _COMP_A)
        assert nx.number_of_selfloops(g) == 0

    def test_graph_has_no_self_loops_after_build(self):
        """General check: build_clustering_graph never produces self-loops."""
        symbols_a = [f"py:src/a.py#Sym{i}(function)" for i in range(5)]
        symbols_b = [f"py:src/b.py#Sym{i}(function)" for i in range(5)]
        all_edges = (
            [_edge(symbols_a[i], symbols_a[(i + 1) % 5], EdgeType.CALLS, Confidence.HIGH) for i in range(5)]
            + [_edge(symbols_a[i], symbols_b[i], EdgeType.IMPORTS, Confidence.MEDIUM) for i in range(5)]
        )
        g = build_clustering_graph(all_edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        assert nx.number_of_selfloops(g) == 0


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------

class TestEmptyInput:
    """Test 10: no edges, no components → empty graph."""

    def test_empty_edges_and_components(self):
        g = build_clustering_graph([], set(), {})
        assert isinstance(g, nx.Graph)
        assert g.number_of_nodes() == 0
        assert g.number_of_edges() == 0

    def test_empty_edges_with_components(self):
        g = build_clustering_graph([], {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        assert _COMP_A in g.nodes
        assert _COMP_B in g.nodes
        assert g.number_of_edges() == 0

    def test_edges_with_empty_components(self):
        """Edges referencing components not in component_ids are ignored."""
        edges = [_edge(_SYMBOL_A, _SYMBOL_B, EdgeType.IMPORTS, Confidence.HIGH)]
        g = build_clustering_graph(edges, set(), {})
        assert g.number_of_nodes() == 0
        assert g.number_of_edges() == 0


# ---------------------------------------------------------------------------
# Edge cases / boundary conditions
# ---------------------------------------------------------------------------

class TestEdgeCasesAndBoundary:
    def test_unknown_edge_type_uses_default_weight(self):
        """IMPLEMENTS / REFERENCES not in WEIGHT_MAP fall back to default 0.2."""
        edges = [_edge(_SYMBOL_A, _SYMBOL_B, EdgeType.IMPLEMENTS, Confidence.HIGH)]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        # IMPLEMENTS HIGH → default weight 0.2
        assert g.has_edge(_COMP_A, _COMP_B)
        assert g[_COMP_A][_COMP_B]["weight"] == pytest.approx(0.2)

    def test_symbol_not_in_any_component_file_is_skipped(self):
        """Symbols whose file doesn't map to any component produce no edge."""
        foreign_sym = "py:external/lib.py#SomeClass(class)"
        edges = [_edge(foreign_sym, _SYMBOL_B, EdgeType.IMPORTS, Confidence.HIGH)]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        assert not g.has_edge(_COMP_A, _COMP_B)

    def test_graph_is_undirected(self):
        edges = [_edge(_SYMBOL_A, _SYMBOL_B, EdgeType.IMPORTS, Confidence.HIGH)]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        assert isinstance(g, nx.Graph)
        assert not isinstance(g, nx.DiGraph)

    def test_node_order_is_deterministic(self):
        """Node insertion order should not affect presence of nodes."""
        comp_ids_1 = {_COMP_A, _COMP_B}
        comp_ids_2 = {_COMP_B, _COMP_A}
        g1 = build_clustering_graph([], comp_ids_1, _FILE_MAP_AB)
        g2 = build_clustering_graph([], comp_ids_2, _FILE_MAP_AB)
        assert set(g1.nodes) == set(g2.nodes)

    def test_component_without_file_map_entry_still_added_as_node(self):
        """A component_id with no entry in component_file_map becomes an isolated node."""
        orphan = "src/orphan.py::Orphan"
        g = build_clustering_graph([], {orphan}, {})  # no file_map entry
        assert orphan in g.nodes

    def test_weight_edge_both_directions(self):
        """Undirected: edge accessible from both sides."""
        edges = [_edge(_SYMBOL_A, _SYMBOL_B, EdgeType.IMPORTS, Confidence.HIGH)]
        g = build_clustering_graph(edges, {_COMP_A, _COMP_B}, _FILE_MAP_AB)
        assert g.has_edge(_COMP_A, _COMP_B)
        assert g.has_edge(_COMP_B, _COMP_A)
        assert g[_COMP_B][_COMP_A]["weight"] == pytest.approx(1.0)

    def test_large_component_set_performance(self):
        """Build graph with 500 components and 1000 edges within a reasonable time."""
        import time
        n = 500
        comp_ids = {f"src/mod_{i}.py::Class_{i}" for i in range(n)}
        file_map = {f"src/mod_{i}.py::Class_{i}": f"src/mod_{i}.py" for i in range(n)}
        edges = [
            _edge(
                f"py:src/mod_{i % n}.py#Class_{i % n}(class)",
                f"py:src/mod_{(i + 1) % n}.py#Class_{(i + 1) % n}(class)",
                EdgeType.CALLS,
                Confidence.MEDIUM,
            )
            for i in range(1000)
        ]
        start = time.monotonic()
        g = build_clustering_graph(edges, comp_ids, file_map)
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"build took {elapsed:.2f}s — too slow"
        assert g.number_of_nodes() == n

    def test_file_symbol_with_no_hash_returns_empty(self):
        """Symbol IDs with 'lang:path' but no '#' delimiter return empty string."""
        assert _extract_file_from_symbol("py:src/no_hash_here") == ""

    def test_single_component_no_edges_no_self_loop(self):
        comp = "src/solo.py::Solo"
        g = build_clustering_graph([], {comp}, {comp: "src/solo.py"})
        assert comp in g.nodes
        assert g.number_of_edges() == 0
        assert nx.number_of_selfloops(g) == 0


# ---------------------------------------------------------------------------
# Real analyzer component_id format (dot-separated)
# ---------------------------------------------------------------------------


class TestRealAnalyzerComponentIds:
    """Verify graph_builder works with real analyzer component_id format."""

    def test_dot_separated_component_ids(self):
        """Real Python analyzer produces 'module.path.ClassName' IDs."""
        comp_a = "codewiki.src.be.auth.handler.AuthHandler"
        comp_b = "codewiki.src.be.api.router.Router"
        file_map = {
            comp_a: "codewiki/src/be/auth/handler.py",
            comp_b: "codewiki/src/be/api/router.py",
        }
        # Symbol from auth/handler.py referencing api/router.py
        edge = _edge(
            "py:codewiki/src/be/auth/handler.py#AuthHandler(class)",
            "py:codewiki/src/be/api/router.py#Router(class)",
            EdgeType.IMPORTS, Confidence.HIGH,
        )
        g = build_clustering_graph([edge], {comp_a, comp_b}, file_map)
        assert g.has_edge(comp_a, comp_b)
        assert g[comp_a][comp_b]["weight"] == pytest.approx(1.0)

    def test_dot_separated_precise_mapping(self):
        """Two classes in same file: edge should map to the correct component."""
        comp_a = "module.path.ClassA"
        comp_b = "module.path.ClassB"
        comp_c = "module.other.ClassC"
        file_map = {
            comp_a: "module/path.py",
            comp_b: "module/path.py",
            comp_c: "module/other.py",
        }
        # Edge from ClassA to ClassC — should NOT create edge for ClassB
        edge = _edge(
            "py:module/path.py#ClassA(class)",
            "py:module/other.py#ClassC(class)",
            EdgeType.CALLS, Confidence.HIGH,
        )
        g = build_clustering_graph([edge], {comp_a, comp_b, comp_c}, file_map)
        assert g.has_edge(comp_a, comp_c)
        # ClassB should NOT have an edge to ClassC (precise mapping)
        assert not g.has_edge(comp_b, comp_c)

    def test_method_level_component_id(self):
        """JS analyzer produces 'module.path.ClassName.method_name' IDs for methods.
        _extract_component_name should take the last segment ('method_name'),
        matching the symbol name from the index.
        """
        # Method component from JS analyzer
        comp_method = "src.app.AppService.handleRequest"
        # Class component in same file
        comp_class = "src.app.AppService"
        comp_other = "src.other.OtherClass"
        file_map = {
            comp_method: "src/app.ts",
            comp_class: "src/app.ts",
            comp_other: "src/other.ts",
        }
        # Edge from method symbol to other class
        edge = _edge(
            "ts:src/app.ts#handleRequest(method)",
            "ts:src/other.ts#OtherClass(class)",
            EdgeType.CALLS, Confidence.HIGH,
        )
        g = build_clustering_graph([edge], {comp_method, comp_class, comp_other}, file_map)
        # Method component should have an edge to other
        assert g.has_edge(comp_method, comp_other)
        # Class component should NOT (precise mapping: handleRequest ≠ AppService)
        assert not g.has_edge(comp_class, comp_other)

    def test_method_component_name_extraction(self):
        """Verify extract_component_name handles method-level dot-separated IDs."""
        from codewiki.src.be.clustering.graph_builder import extract_component_name
        # Last segment for dot-separated
        assert extract_component_name("module.path.ClassName.method_name") == "method_name"
        assert extract_component_name("module.path.ClassName") == "ClassName"
        # Test fixture format
        assert extract_component_name("src/a.py::Foo") == "Foo"
