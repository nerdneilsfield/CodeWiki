from unittest.mock import MagicMock

from codewiki.src.be.index.edge_index import EdgeIndex
from codewiki.src.be.index.models import EdgeType, SymbolEdge


def test_classify_edges_uses_edge_index_api():
    """Verify _classify_edges calls the EdgeIndex API instead of scanning all edges."""
    from codewiki.src.be.generation.context_pack import _classify_edges

    edges = [
        SymbolEdge(from_symbol="A", to_symbol="B", edge_type=EdgeType.CALLS),
        SymbolEdge(from_symbol="B", to_symbol="C", edge_type=EdgeType.CALLS),
        SymbolEdge(from_symbol="X", to_symbol="Y", edge_type=EdgeType.CALLS),
    ]
    edge_index = EdgeIndex(edges)

    original_callees = edge_index.callees_of
    original_callers = edge_index.callers_of
    callees_calls: list[str] = []
    callers_calls: list[str] = []

    def spy_callees(symbol_id: str):
        callees_calls.append(symbol_id)
        return original_callees(symbol_id)

    def spy_callers(symbol_id: str):
        callers_calls.append(symbol_id)
        return original_callers(symbol_id)

    edge_index.callees_of = spy_callees  # type: ignore[method-assign]
    edge_index.callers_of = spy_callers  # type: ignore[method-assign]

    index_products = MagicMock()
    index_products.edge_index = edge_index
    index_products.edges = edges

    boundary, internal = _classify_edges({"A", "B"}, index_products)

    assert callees_calls, "_classify_edges did not call edge_index.callees_of()"
    assert callers_calls, "_classify_edges did not call edge_index.callers_of()"
    assert any("A" in edge and "B" in edge for edge in internal)
    assert any("B" in edge and "C" in edge for edge in boundary)
    assert not any("X" in edge for edge in boundary + internal)


class _FlippingSymbolSet(set[str]):
    """A set-like object whose iteration order alternates between calls."""

    def __init__(self, values):
        super().__init__(values)
        self._forward = True

    def __iter__(self):
        items = sorted(super().__iter__())
        if not self._forward:
            items.reverse()
        self._forward = not self._forward
        return iter(items)


def test_classify_edges_order_and_truncation_are_deterministic():
    """Boundary/internal ordering must be stable even if module_sym_ids iteration flips."""
    from codewiki.src.be.generation.context_pack import _classify_edges

    edges = [
        SymbolEdge(
            from_symbol=f"S{i:02d}",
            to_symbol=f"T{i:02d}",
            edge_type=EdgeType.CALLS,
        )
        for i in range(20)
    ]
    edge_index = EdgeIndex(edges)
    index_products = MagicMock()
    index_products.edge_index = edge_index
    index_products.edges = edges

    module_syms = _FlippingSymbolSet({f"S{i:02d}" for i in range(20)})

    boundary_first, internal_first = _classify_edges(module_syms, index_products)
    boundary_second, internal_second = _classify_edges(module_syms, index_products)

    expected = [f"S{i:02d} --calls--> T{i:02d} [medium]" for i in range(15)]
    assert internal_first == []
    assert internal_second == []
    assert boundary_first == expected
    assert boundary_second == expected
