# tests/test_index_component_card.py
"""Tests for ComponentCard builder."""
import pytest
from codewiki.src.be.index.models import (
    Symbol, SymbolKind, SourceRange, SymbolEdge, EdgeType, Confidence, ComponentCard,
)
from codewiki.src.be.index.component_card import CardBuilder


def _sym(sid="py:src/a.py#Foo(class)", name="Foo", kind=SymbolKind.CLASS,
         sig="class Foo(Base)", doc="Handles authentication.\nWith multiple lines of detail.",
         file_path="src/a.py", start=1, end=50):
    return Symbol(
        symbol_id=sid, lang="python", kind=kind, name=name,
        qualified_name=f"src.a.{name}", file_path=file_path,
        range=SourceRange(file_path=file_path, start_line=start, start_col=0, end_line=end, end_col=0),
        signature=sig, docstring=doc, source_hash="h",
    )


def _edge(from_s, to_s, etype=EdgeType.CALLS):
    return SymbolEdge(
        edge_type=etype, from_symbol=from_s, to_symbol=to_s,
        confidence=Confidence.HIGH, resolver="ast",
    )


def test_builds_card():
    sym = _sym()
    builder = CardBuilder(max_edges=5)
    card = builder.build_card(sym, [])
    assert card.symbol_id == sym.symbol_id
    assert card.signature == "class Foo(Base)"
    assert "Handles authentication." in card.docstring_summary
    assert card.kind == SymbolKind.CLASS


def test_docstring_truncated_to_two_sentences():
    sym = _sym(doc="First sentence. Second sentence. Third sentence. Fourth.")
    builder = CardBuilder(max_edges=5)
    card = builder.build_card(sym, [])
    # Should have at most 2 sentences
    assert card.docstring_summary.count(".") <= 3  # 2 sentences + possible trailing


def test_no_docstring():
    sym = _sym(doc=None)
    builder = CardBuilder(max_edges=5)
    card = builder.build_card(sym, [])
    assert card.docstring_summary == ""


def test_key_edges_from_outgoing():
    sym = _sym(sid="s1")
    edges = [
        _edge("s1", "s2", EdgeType.CALLS),
        _edge("s1", "s3", EdgeType.IMPORTS),
    ]
    builder = CardBuilder(max_edges=5)
    card = builder.build_card(sym, edges)
    assert len(card.key_edges) == 2


def test_key_edges_capped():
    sym = _sym(sid="s1")
    edges = [_edge("s1", f"s{i}") for i in range(20)]
    builder = CardBuilder(max_edges=3)
    card = builder.build_card(sym, edges)
    assert len(card.key_edges) == 3


def test_file_context():
    sym = _sym(file_path="src/auth/login.py", start=10, end=42)
    builder = CardBuilder(max_edges=5)
    card = builder.build_card(sym, [])
    assert card.file_context == "src/auth/login.py (lines 10-42)"


# ── New edge-case tests ───────────────────────────────────────────────────────

def test_no_signature_falls_back_to_name():
    """When symbol has no signature, card.signature should fall back to symbol.name."""
    sym = _sym(sig=None)
    builder = CardBuilder(max_edges=5)
    card = builder.build_card(sym, [])
    assert card.signature == sym.name


def test_single_sentence_docstring_not_truncated():
    """Single-sentence docstring should not be modified (no extra content added)."""
    sym = _sym(doc="Handles authentication.")
    builder = CardBuilder(max_edges=5)
    card = builder.build_card(sym, [])
    assert card.docstring_summary == "Handles authentication."


def test_edge_with_to_unresolved_included_in_key_edges():
    """Edges where to_symbol is None but to_unresolved is set should appear in key_edges."""
    from codewiki.src.be.index.models import SymbolEdge, EdgeType, Confidence
    sym = _sym(sid="s1")
    edge = SymbolEdge(
        edge_type=EdgeType.CALLS,
        from_symbol="s1",
        to_symbol=None,
        to_unresolved="some.external.lib",
        confidence=Confidence.LOW,
        resolver="heuristic",
    )
    builder = CardBuilder(max_edges=5)
    card = builder.build_card(sym, [edge])
    assert len(card.key_edges) == 1
    assert "some.external.lib" in card.key_edges[0]


def test_max_edges_zero_produces_empty_key_edges():
    """max_edges=0 should produce empty key_edges list."""
    sym = _sym(sid="s1")
    edges = [_edge("s1", f"s{i}") for i in range(5)]
    builder = CardBuilder(max_edges=0)
    card = builder.build_card(sym, edges)
    assert card.key_edges == []


def test_only_incoming_edges_not_included_in_key_edges():
    """Edges where from_symbol != symbol.symbol_id (incoming edges) are NOT included in key_edges."""
    sym = _sym(sid="s1")
    # These edges all point TO s1, not FROM s1
    edges = [
        _edge("other1", "s1", EdgeType.CALLS),
        _edge("other2", "s1", EdgeType.IMPORTS),
    ]
    builder = CardBuilder(max_edges=5)
    card = builder.build_card(sym, edges)
    assert card.key_edges == []
