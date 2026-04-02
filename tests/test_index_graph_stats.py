"""Tests for GraphStats computation."""

import pytest

from codewiki.src.be.index.models import (
    Symbol,
    SymbolEdge,
    SymbolKind,
    EdgeType,
    Confidence,
    SourceRange,
    Visibility,
    ExportStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_symbol(symbol_id: str, name: str = "Sym") -> Symbol:
    return Symbol(
        symbol_id=symbol_id,
        lang="python",
        kind=SymbolKind.FUNCTION,
        name=name,
        qualified_name=f"pkg.{name}",
        file_path="src/a.py",
        range=SourceRange(file_path="src/a.py", start_line=1, start_col=0, end_line=5, end_col=0),
        source_hash="abc123",
    )


def make_edge(
    from_sym: str,
    edge_type: EdgeType,
    to_sym: str | None = "target",
    to_unresolved: str | None = None,
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
# Test 1: edge_counts_by_type
# ---------------------------------------------------------------------------


def test_edge_counts_by_type():
    """3 IMPORTS + 2 CALLS + 1 EXTENDS → edge_counts maps each type correctly."""
    from codewiki.src.be.index.graph_stats import GraphStats

    edges = (
        [make_edge("A", EdgeType.IMPORTS) for _ in range(3)]
        + [make_edge("B", EdgeType.CALLS) for _ in range(2)]
        + [make_edge("C", EdgeType.EXTENDS)]
    )
    symbols = [make_symbol("A"), make_symbol("B"), make_symbol("C")]

    stats = GraphStats.compute(symbols, edges)

    assert stats.edge_counts["imports"] == 3
    assert stats.edge_counts["calls"] == 2
    assert stats.edge_counts["extends"] == 1
    # Edge types with zero edges should not appear in the dict.
    assert "implements" not in stats.edge_counts
    assert "references" not in stats.edge_counts


# ---------------------------------------------------------------------------
# Test 2: unresolved_ratio
# ---------------------------------------------------------------------------


def test_unresolved_ratio():
    """3 CALLS edges, 1 with to_unresolved set → unresolved_ratios['calls'] ≈ 0.333."""
    from codewiki.src.be.index.graph_stats import GraphStats

    edges = [
        make_edge("A", EdgeType.CALLS, to_sym="target1"),
        make_edge("B", EdgeType.CALLS, to_sym="target2"),
        make_edge("C", EdgeType.CALLS, to_sym=None, to_unresolved="unknown_fn"),
    ]
    symbols = [make_symbol("A"), make_symbol("B"), make_symbol("C")]

    stats = GraphStats.compute(symbols, edges)

    assert stats.unresolved_ratios["calls"] == pytest.approx(1 / 3, rel=1e-5)
    assert stats.unresolved_counts["calls"] == 1


# ---------------------------------------------------------------------------
# Test 3: empty_stats — no ZeroDivisionError, all zeros
# ---------------------------------------------------------------------------


def test_empty_stats():
    """0 symbols, 0 edges → all zeros, no ZeroDivisionError, ratios default to 0.0."""
    from codewiki.src.be.index.graph_stats import GraphStats

    stats = GraphStats.compute([], [])

    assert stats.total_symbols == 0
    assert stats.total_edges == 0
    assert stats.edge_counts == {}
    assert stats.unresolved_counts == {}
    assert stats.unresolved_ratios == {}


# ---------------------------------------------------------------------------
# Test 4: total_counts
# ---------------------------------------------------------------------------


def test_total_counts():
    """total_symbols and total_edges are counted correctly."""
    from codewiki.src.be.index.graph_stats import GraphStats

    symbols = [make_symbol(f"sym{i}") for i in range(5)]
    edges = [make_edge("A", EdgeType.IMPORTS) for _ in range(4)] + [
        make_edge("B", EdgeType.CALLS) for _ in range(3)
    ]

    stats = GraphStats.compute(symbols, edges)

    assert stats.total_symbols == 5
    assert stats.total_edges == 7


# ---------------------------------------------------------------------------
# Test 5: stats_serializable — round-trip through model_dump / model_validate
# ---------------------------------------------------------------------------


def test_stats_serializable():
    """model_dump() produces a plain dict; model_validate() round-trips back correctly."""
    from codewiki.src.be.index.graph_stats import GraphStats

    edges = [make_edge("A", EdgeType.IMPORTS) for _ in range(2)] + [
        make_edge("B", EdgeType.CALLS, to_sym=None, to_unresolved="fn")
    ]
    symbols = [make_symbol("A"), make_symbol("B")]

    original = GraphStats.compute(symbols, edges)
    raw = original.model_dump()

    # raw must be a plain dict with only JSON-serialisable leaf types.
    assert isinstance(raw, dict)
    assert isinstance(raw["edge_counts"], dict)
    assert isinstance(raw["unresolved_ratios"], dict)
    assert isinstance(raw["total_symbols"], int)
    assert isinstance(raw["total_edges"], int)

    # Round-trip.
    restored = GraphStats.model_validate(raw)
    assert restored.edge_counts == original.edge_counts
    assert restored.unresolved_counts == original.unresolved_counts
    assert restored.unresolved_ratios == original.unresolved_ratios
    assert restored.total_symbols == original.total_symbols
    assert restored.total_edges == original.total_edges


# ---------------------------------------------------------------------------
# Additional edge cases
# ---------------------------------------------------------------------------


def test_multiple_unresolved_same_type():
    """All edges unresolved → ratio == 1.0."""
    from codewiki.src.be.index.graph_stats import GraphStats

    edges = [
        make_edge("A", EdgeType.IMPORTS, to_sym=None, to_unresolved="missing_mod"),
        make_edge("B", EdgeType.IMPORTS, to_sym=None, to_unresolved="also_missing"),
    ]
    stats = GraphStats.compute([], edges)

    assert stats.unresolved_ratios["imports"] == pytest.approx(1.0)
    assert stats.unresolved_counts["imports"] == 2


def test_no_unresolved_edges():
    """All edges fully resolved → ratio == 0.0 for each type."""
    from codewiki.src.be.index.graph_stats import GraphStats

    edges = [
        make_edge("A", EdgeType.CALLS, to_sym="B"),
        make_edge("B", EdgeType.CALLS, to_sym="C"),
    ]
    stats = GraphStats.compute([], edges)

    assert stats.unresolved_ratios["calls"] == pytest.approx(0.0)
    assert stats.unresolved_counts.get("calls", 0) == 0


def test_mixed_edge_types_with_partial_unresolved():
    """Mixed types: IMPORTS fully resolved, CALLS partially unresolved."""
    from codewiki.src.be.index.graph_stats import GraphStats

    edges = [
        make_edge("A", EdgeType.IMPORTS, to_sym="X"),
        make_edge("B", EdgeType.IMPORTS, to_sym="Y"),
        make_edge("C", EdgeType.CALLS, to_sym="D"),
        make_edge("D", EdgeType.CALLS, to_sym=None, to_unresolved="ghost"),
    ]
    stats = GraphStats.compute([], edges)

    assert stats.edge_counts["imports"] == 2
    assert stats.edge_counts["calls"] == 2
    assert stats.unresolved_ratios["imports"] == pytest.approx(0.0)
    assert stats.unresolved_ratios["calls"] == pytest.approx(0.5)
