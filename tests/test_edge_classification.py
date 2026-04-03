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
