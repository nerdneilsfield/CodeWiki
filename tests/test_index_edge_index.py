"""Tests for EdgeIndex query API."""

import pytest

from codewiki.src.be.index.models import SymbolEdge, EdgeType, Confidence
from codewiki.src.be.index.edge_index import EdgeIndex


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_edge(
    from_sym: str, to_sym: str | None, edge_type: EdgeType, to_unresolved: str | None = None
) -> SymbolEdge:
    return SymbolEdge(
        edge_type=edge_type,
        from_symbol=from_sym,
        to_symbol=to_sym,
        to_unresolved=to_unresolved,
        confidence=Confidence.HIGH if to_sym else Confidence.LOW,
        resolver="test",
    )


# ---------------------------------------------------------------------------
# Test 1: callers_of
# ---------------------------------------------------------------------------


def test_callers_of():
    """callers_of('B') returns edges from A→B and C→B."""
    e_ab = make_edge("A", "B", EdgeType.CALLS)
    e_cb = make_edge("C", "B", EdgeType.CALLS)
    e_ac = make_edge("A", "C", EdgeType.CALLS)  # not a caller of B

    idx = EdgeIndex([e_ab, e_cb, e_ac])
    callers = idx.callers_of("B")

    assert len(callers) == 2
    assert e_ab in callers
    assert e_cb in callers
    assert e_ac not in callers


# ---------------------------------------------------------------------------
# Test 2: callees_of
# ---------------------------------------------------------------------------


def test_callees_of():
    """callees_of('A') returns A→B (CALLS) and A→C (IMPORTS)."""
    e_ab = make_edge("A", "B", EdgeType.CALLS)
    e_ac = make_edge("A", "C", EdgeType.IMPORTS)
    e_cb = make_edge("C", "B", EdgeType.CALLS)  # not a callee of A

    idx = EdgeIndex([e_ab, e_ac, e_cb])
    callees = idx.callees_of("A")

    assert len(callees) == 2
    assert e_ab in callees
    assert e_ac in callees
    assert e_cb not in callees


# ---------------------------------------------------------------------------
# Test 3: edges_of — all types (no filter)
# ---------------------------------------------------------------------------


def test_edges_of_all_types():
    """edges_of returns all edges involving a symbol as source or target."""
    e_ax = make_edge("A", "X", EdgeType.IMPORTS)
    e_ay = make_edge("A", "Y", EdgeType.CALLS)
    e_az = make_edge("A", "Z", EdgeType.EXTENDS)
    e_ba = make_edge("B", "A", EdgeType.CALLS)  # A as target
    e_bc = make_edge("B", "C", EdgeType.IMPORTS)  # A not involved

    idx = EdgeIndex([e_ax, e_ay, e_az, e_ba, e_bc])
    edges = idx.edges_of("A")

    # Should include outgoing (A→X, A→Y, A→Z) and incoming (B→A)
    assert len(edges) == 4
    assert e_ax in edges
    assert e_ay in edges
    assert e_az in edges
    assert e_ba in edges
    assert e_bc not in edges


# ---------------------------------------------------------------------------
# Test 4: edges_of — filtered by type
# ---------------------------------------------------------------------------


def test_edges_of_filtered_by_type():
    """edges_of(sym, edge_type=CALLS) returns only CALLS edges."""
    e_ax = make_edge("A", "X", EdgeType.IMPORTS)
    e_ay = make_edge("A", "Y", EdgeType.CALLS)
    e_ba = make_edge("B", "A", EdgeType.CALLS)  # A as target (CALLS)
    e_ca = make_edge("C", "A", EdgeType.EXTENDS)  # A as target (EXTENDS)

    idx = EdgeIndex([e_ax, e_ay, e_ba, e_ca])
    calls_only = idx.edges_of("A", edge_type=EdgeType.CALLS)

    assert len(calls_only) == 2
    assert e_ay in calls_only
    assert e_ba in calls_only
    assert e_ax not in calls_only
    assert e_ca not in calls_only


# ---------------------------------------------------------------------------
# Test 5: dependency_subgraph
# ---------------------------------------------------------------------------


def test_dependency_subgraph():
    """subgraph({A, B}) returns only A→B, not A→D or B→C."""
    e_ab = make_edge("A", "B", EdgeType.CALLS)
    e_bc = make_edge("B", "C", EdgeType.CALLS)  # C not in set
    e_ad = make_edge("A", "D", EdgeType.IMPORTS)  # D not in set

    idx = EdgeIndex([e_ab, e_bc, e_ad])
    subgraph = idx.dependency_subgraph({"A", "B"})

    assert len(subgraph) == 1
    assert e_ab in subgraph
    assert e_bc not in subgraph
    assert e_ad not in subgraph


# ---------------------------------------------------------------------------
# Test 6: empty EdgeIndex
# ---------------------------------------------------------------------------


def test_empty_edge_index():
    """EdgeIndex([]) returns empty lists for all queries."""
    idx = EdgeIndex([])

    assert idx.callers_of("X") == []
    assert idx.callees_of("X") == []
    assert idx.edges_of("X") == []
    assert idx.edges_of("X", edge_type=EdgeType.CALLS) == []
    assert idx.dependency_subgraph({"X", "Y"}) == []


# ---------------------------------------------------------------------------
# Test 7: callers_of unknown symbol
# ---------------------------------------------------------------------------


def test_callers_of_unknown_symbol():
    """callers_of for a symbol with no incoming edges returns []."""
    e_ab = make_edge("A", "B", EdgeType.CALLS)
    idx = EdgeIndex([e_ab])

    assert idx.callers_of("nonexistent") == []


# ---------------------------------------------------------------------------
# Test 8: unresolved edges in callees — not indexed in _by_to
# ---------------------------------------------------------------------------


def test_unresolved_edges_in_callees():
    """Edge with to_symbol=None, to_unresolved='foo':
    - callees_of(from_symbol) returns the edge (forward lookup works)
    - callers_of('foo') does NOT return it (unresolved not in _by_to index)
    """
    unresolved_edge = make_edge("A", None, EdgeType.CALLS, to_unresolved="foo")
    resolved_edge = make_edge("A", "B", EdgeType.CALLS)

    idx = EdgeIndex([unresolved_edge, resolved_edge])

    callees = idx.callees_of("A")
    assert len(callees) == 2
    assert unresolved_edge in callees
    assert resolved_edge in callees

    # "foo" is only in to_unresolved, not to_symbol — must NOT appear via callers_of
    callers_of_foo = idx.callers_of("foo")
    assert callers_of_foo == []


# ---------------------------------------------------------------------------
# Test 9: IndexProducts has edge_index after construction
# ---------------------------------------------------------------------------


def test_index_products_has_edge_index():
    """IndexProducts auto-creates edge_index via __post_init__."""
    from codewiki.src.be.index.index_builder import IndexProducts
    from codewiki.src.be.index.symbol_table import SymbolTable
    from codewiki.src.be.index.import_graph import ImportGraph

    edge = make_edge("A", "B", EdgeType.CALLS)
    products = IndexProducts(
        symbol_table=SymbolTable([]),
        import_graph=ImportGraph([]),
        edges=[edge],
        cards=[],
    )

    assert isinstance(products.edge_index, EdgeIndex)
    callers = products.edge_index.callers_of("B")
    assert len(callers) == 1
    assert callers[0] is edge


# ---------------------------------------------------------------------------
# Test 10: IndexProducts from_dict round-trip has working edge_index
# ---------------------------------------------------------------------------


def test_index_products_from_dict_has_edge_index():
    """After to_dict/from_dict round-trip, edge_index is rebuilt and functional."""
    from codewiki.src.be.index.index_builder import IndexProducts
    from codewiki.src.be.index.symbol_table import SymbolTable
    from codewiki.src.be.index.import_graph import ImportGraph

    edge = make_edge("X", "Y", EdgeType.IMPORTS)
    products = IndexProducts(
        symbol_table=SymbolTable([]),
        import_graph=ImportGraph([]),
        edges=[edge],
        cards=[],
    )

    data = products.to_dict()
    # edge_index must NOT be serialized
    assert "edge_index" not in data

    restored = IndexProducts.from_dict(data)
    assert isinstance(restored.edge_index, EdgeIndex)

    callees = restored.edge_index.callees_of("X")
    assert len(callees) == 1
    assert callees[0].from_symbol == "X"
    assert callees[0].to_symbol == "Y"
    assert callees[0].edge_type == EdgeType.IMPORTS


# ---------------------------------------------------------------------------
# Test 11: Self-loop edge should not be duplicated in edges_of
# ---------------------------------------------------------------------------


def test_edges_of_self_loop_not_duplicated():
    """A→A (self-loop / recursive call) must appear exactly once in edges_of('A')."""
    self_edge = make_edge("A", "A", EdgeType.CALLS)
    other_edge = make_edge("A", "B", EdgeType.CALLS)

    idx = EdgeIndex([self_edge, other_edge])

    result = idx.edges_of("A")
    # self_edge appears in both _by_from["A"] and _by_to["A"] but must be deduped
    assert result.count(self_edge) == 1
    assert other_edge in result
    # A→A (deduped) + A→B = 2 edges total
    assert len(result) == 2

    # With type filter
    calls = idx.edges_of("A", edge_type=EdgeType.CALLS)
    assert calls.count(self_edge) == 1
    assert len(calls) == 2
