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
