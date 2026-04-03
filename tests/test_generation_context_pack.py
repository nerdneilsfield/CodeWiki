"""Tests for generation.context_pack — TDD phase (written before implementation).

Run with:
    python3.13 -m pytest tests/test_generation_context_pack.py -q
"""

from dataclasses import dataclass, field

import pytest

from codewiki.src.be.index.models import (
    ComponentCard,
    Confidence,
    EdgeType,
    SourceRange,
    SymbolEdge,
    SymbolKind,
)


# ---------------------------------------------------------------------------
# Minimal mock infrastructure
# ---------------------------------------------------------------------------


@dataclass
class _MockNode:
    """Minimal stand-in for a clustering Node."""

    relative_path: str


@dataclass
class _MockIndexProducts:
    """Minimal stand-in for IndexProducts — only what context_pack needs."""

    cards: list[ComponentCard] = field(default_factory=list)
    edges: list[SymbolEdge] = field(default_factory=list)


def _make_card(
    symbol_id: str,
    signature: str = "def foo()",
    kind: SymbolKind = SymbolKind.FUNCTION,
    docstring_summary: str = "Does foo.",
    key_edges: list[str] | None = None,
    file_context: str = "src/mod.py:1",
) -> ComponentCard:
    return ComponentCard(
        symbol_id=symbol_id,
        signature=signature,
        docstring_summary=docstring_summary,
        kind=kind,
        key_edges=key_edges or [],
        file_context=file_context,
    )


def _make_edge(
    from_sym: str,
    to_sym: str | None,
    edge_type: EdgeType = EdgeType.CALLS,
    confidence: Confidence = Confidence.HIGH,
    evidence_refs: list[SourceRange] | None = None,
) -> SymbolEdge:
    return SymbolEdge(
        edge_type=edge_type,
        from_symbol=from_sym,
        to_symbol=to_sym,
        evidence_refs=evidence_refs or [],
        confidence=confidence,
    )


def _source_range(file_path: str, line: int = 1) -> SourceRange:
    return SourceRange(
        file_path=file_path,
        start_line=line,
        start_col=0,
        end_line=line,
        end_col=0,
    )


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

MODULE_FILE = "src/auth/login.py"
EXTERNAL_FILE = "src/utils/helpers.py"

MODULE_SYMBOL = f"py:{MODULE_FILE}#LoginService(class)"
EXTERNAL_SYMBOL = f"py:{EXTERNAL_FILE}#Helper(class)"


@pytest.fixture
def module_components():
    return ["comp_a", "comp_b"]


@pytest.fixture
def components():
    return {
        "comp_a": _MockNode(relative_path=MODULE_FILE),
        "comp_b": _MockNode(relative_path=MODULE_FILE),
    }


@pytest.fixture
def index_with_cards_and_edges():
    cards = [
        _make_card(
            symbol_id=MODULE_SYMBOL,
            signature="class LoginService",
            kind=SymbolKind.CLASS,
            docstring_summary="Handles login.",
            key_edges=["calls:validate", "calls:hash_password"],
            file_context=f"{MODULE_FILE}:10",
        ),
        _make_card(
            symbol_id=EXTERNAL_SYMBOL,
            signature="class Helper",
            kind=SymbolKind.CLASS,
            docstring_summary="Utility helper.",
            file_context=f"{EXTERNAL_FILE}:5",
        ),
    ]
    edges = [
        _make_edge(MODULE_SYMBOL, EXTERNAL_SYMBOL, EdgeType.CALLS),
    ]
    return _MockIndexProducts(cards=cards, edges=edges)


# ---------------------------------------------------------------------------
# Test 1: build_context_pack with a populated index
# ---------------------------------------------------------------------------


def test_build_context_pack_with_index(module_components, components, index_with_cards_and_edges):
    from codewiki.src.be.generation.context_pack import build_context_pack

    result = build_context_pack(
        module_components=module_components,
        components=components,
        index_products=index_with_cards_and_edges,
    )

    assert isinstance(result, dict)
    assert "symbol_cards" in result
    # Only the card whose symbol belongs to MODULE_FILE should appear
    assert len(result["symbol_cards"]) == 1
    assert "LoginService" in result["symbol_cards"][0]
    # Result must have all required keys
    required_keys = {
        "symbol_cards",
        "boundary_edges",
        "internal_edges",
        "evidence_snippets",
        "glossary_context",
        "link_map_context",
    }
    assert required_keys == set(result.keys())


# ---------------------------------------------------------------------------
# Test 2: build_context_pack without index (graceful degradation)
# ---------------------------------------------------------------------------


def test_build_context_pack_without_index(module_components, components):
    from codewiki.src.be.generation.context_pack import build_context_pack

    glossary = {"token": "An auth credential", "session": "User session"}
    result = build_context_pack(
        module_components=module_components,
        components=components,
        index_products=None,
        glossary=glossary,
    )

    assert result["symbol_cards"] == []
    assert result["boundary_edges"] == []
    assert result["internal_edges"] == []
    assert result["evidence_snippets"] == []
    # Glossary must still work even with no index
    assert "token" in result["glossary_context"]
    assert result["link_map_context"] == ""


# ---------------------------------------------------------------------------
# Test 3: boundary edges — from module to external file
# ---------------------------------------------------------------------------


def test_build_context_pack_boundary_edges(module_components, components):
    from codewiki.src.be.generation.context_pack import build_context_pack

    edges = [
        _make_edge(MODULE_SYMBOL, EXTERNAL_SYMBOL, EdgeType.CALLS),
    ]
    index = _MockIndexProducts(cards=[], edges=edges)

    result = build_context_pack(
        module_components=module_components,
        components=components,
        index_products=index,
    )

    assert len(result["boundary_edges"]) == 1
    assert result["internal_edges"] == []
    desc = result["boundary_edges"][0]
    assert MODULE_SYMBOL in desc
    assert EXTERNAL_SYMBOL in desc
    assert "calls" in desc


# ---------------------------------------------------------------------------
# Test 4: internal edges — both symbols within module files
# ---------------------------------------------------------------------------


def test_build_context_pack_internal_edges(module_components, components):
    from codewiki.src.be.generation.context_pack import build_context_pack

    # Both symbols live in MODULE_FILE
    sym_a = f"py:{MODULE_FILE}#LoginService(class)"
    sym_b = f"py:{MODULE_FILE}#AuthMiddleware(class)"
    edges = [
        _make_edge(sym_a, sym_b, EdgeType.IMPORTS),
    ]
    index = _MockIndexProducts(cards=[], edges=edges)

    result = build_context_pack(
        module_components=module_components,
        components=components,
        index_products=index,
    )

    assert len(result["internal_edges"]) == 1
    assert result["boundary_edges"] == []
    assert "imports" in result["internal_edges"][0]


# ---------------------------------------------------------------------------
# Test 5: evidence_refs → snippets populated with file:line
# ---------------------------------------------------------------------------


def test_build_context_pack_evidence_refs(module_components, components):
    from codewiki.src.be.generation.context_pack import build_context_pack

    card = _make_card(symbol_id=MODULE_SYMBOL, file_context=f"{MODULE_FILE}:10")
    ref = _source_range(MODULE_FILE, line=42)
    edge = _make_edge(
        MODULE_SYMBOL,
        EXTERNAL_SYMBOL,
        EdgeType.CALLS,
        evidence_refs=[ref],
    )
    index = _MockIndexProducts(cards=[card], edges=[edge])

    result = build_context_pack(
        module_components=module_components,
        components=components,
        index_products=index,
    )

    assert len(result["evidence_snippets"]) >= 1
    snippet = result["evidence_snippets"][0]
    assert MODULE_FILE in snippet
    assert "42" in snippet
    assert "calls" in snippet


# ---------------------------------------------------------------------------
# Test 6: glossary formatting
# ---------------------------------------------------------------------------


def test_build_context_pack_glossary(module_components, components):
    from codewiki.src.be.generation.context_pack import build_context_pack

    glossary = {"jwt": "JSON Web Token", "bcrypt": "Password hash algorithm"}
    result = build_context_pack(
        module_components=module_components,
        components=components,
        index_products=None,
        glossary=glossary,
    )

    ctx = result["glossary_context"]
    assert "jwt" in ctx
    assert "JSON Web Token" in ctx
    assert "bcrypt" in ctx
    assert "Password hash algorithm" in ctx


# ---------------------------------------------------------------------------
# Test 7: link_map formatting
# ---------------------------------------------------------------------------


def test_build_context_pack_link_map(module_components, components):
    from codewiki.src.be.generation.context_pack import build_context_pack

    link_map = {
        "src/auth/login.py": "docs/auth/login.md",
        "src/utils/helpers.py": "docs/utils/helpers.md",
    }
    result = build_context_pack(
        module_components=module_components,
        components=components,
        index_products=None,
        link_map=link_map,
    )

    ctx = result["link_map_context"]
    assert "src/auth/login.py" in ctx
    assert "docs/auth/login.md" in ctx
    assert "src/utils/helpers.py" in ctx


def test_build_context_pack_filters_link_map_by_module_paths(
    module_components, components, index_with_cards_and_edges
):
    from codewiki.src.be.generation.context_pack import build_context_pack

    link_map = {
        "src/auth/login.py": "docs/auth/login.md",
        "src/utils/helpers.py": "docs/utils/helpers.md",
    }

    result = build_context_pack(
        module_components=module_components,
        components=components,
        index_products=index_with_cards_and_edges,
        link_map=link_map,
    )

    ctx = result["link_map_context"]
    assert "src/auth/login.py" in ctx
    assert "docs/auth/login.md" in ctx
    assert "src/utils/helpers.py" not in ctx
    assert "docs/utils/helpers.md" not in ctx


# ---------------------------------------------------------------------------
# Test 8: format_context_pack_section — None or empty input
# ---------------------------------------------------------------------------


def test_format_context_pack_section_empty():
    from codewiki.src.be.generation.context_pack import format_context_pack_section

    assert format_context_pack_section(None) == ""
    assert format_context_pack_section({}) == ""
    assert format_context_pack_section({"symbol_cards": [], "boundary_edges": []}) == ""


# ---------------------------------------------------------------------------
# Test 9: format_context_pack_section — symbol_cards present
# ---------------------------------------------------------------------------


def test_format_context_pack_section_symbol_cards():
    from codewiki.src.be.generation.context_pack import format_context_pack_section

    pack = {
        "symbol_cards": ["**LoginService** (class): Handles login"],
        "boundary_edges": [],
        "internal_edges": [],
        "glossary_context": "",
        "link_map_context": "",
    }
    output = format_context_pack_section(pack)
    assert "<SYMBOL_CARDS>" in output
    assert "LoginService" in output
    assert "<BOUNDARY_EDGES>" not in output


# ---------------------------------------------------------------------------
# Test 10: format_context_pack_section — all sections present
# ---------------------------------------------------------------------------


def test_format_context_pack_section_all_sections():
    from codewiki.src.be.generation.context_pack import format_context_pack_section

    pack = {
        "symbol_cards": ["**Foo** (function): does something"],
        "boundary_edges": ["A --calls--> B"],
        "internal_edges": ["C --imports--> D"],
        "evidence_snippets": ["src/a.py:10 (calls)"],
        "glossary_context": "- **foo**: bar",
        "link_map_context": "- [src/a.py](docs/a.md)",
    }
    output = format_context_pack_section(pack)

    assert "<SYMBOL_CARDS>" in output
    assert "</SYMBOL_CARDS>" in output
    assert "<BOUNDARY_EDGES>" in output
    assert "</BOUNDARY_EDGES>" in output
    assert "<INTERNAL_EDGES>" in output
    assert "</INTERNAL_EDGES>" in output
    assert "<GLOSSARY>" in output
    assert "</GLOSSARY>" in output
    assert "<LINK_MAP>" in output
    assert "</LINK_MAP>" in output


# ---------------------------------------------------------------------------
# Test 11: _format_glossary — entries sorted alphabetically
# ---------------------------------------------------------------------------


def test_format_glossary_sorted():
    from codewiki.src.be.generation.context_pack import _format_glossary

    glossary = {"zebra": "last letter", "alpha": "first letter", "mango": "middle"}
    output = _format_glossary(glossary)
    lines = output.splitlines()

    assert len(lines) == 3
    # alpha must come before mango, mango before zebra
    assert lines[0].startswith("- **alpha**")
    assert lines[1].startswith("- **mango**")
    assert lines[2].startswith("- **zebra**")


# ---------------------------------------------------------------------------
# Test 12: _format_link_map — entries sorted alphabetically by path
# ---------------------------------------------------------------------------


def test_format_link_map_sorted():
    from codewiki.src.be.generation.context_pack import _format_link_map

    link_map = {
        "src/z_module.py": "docs/z.md",
        "src/a_module.py": "docs/a.md",
        "src/m_module.py": "docs/m.md",
    }
    output = _format_link_map(link_map)
    lines = output.splitlines()

    assert len(lines) == 3
    assert lines[0].startswith("- [src/a_module.py]")
    assert lines[1].startswith("- [src/m_module.py]")
    assert lines[2].startswith("- [src/z_module.py]")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_build_context_pack_empty_module_components(components):
    """Empty component list → no cards, no edges, no snippets."""
    from codewiki.src.be.generation.context_pack import build_context_pack

    card = _make_card(symbol_id=MODULE_SYMBOL)
    index = _MockIndexProducts(cards=[card], edges=[])

    result = build_context_pack(
        module_components=[],
        components=components,
        index_products=index,
    )

    assert result["symbol_cards"] == []


def test_build_context_pack_edge_with_no_to_symbol(module_components, components):
    """Unresolved edges (to_symbol=None) must not raise and are skipped."""
    from codewiki.src.be.generation.context_pack import build_context_pack

    edge = _make_edge(MODULE_SYMBOL, None)
    index = _MockIndexProducts(cards=[], edges=[edge])

    result = build_context_pack(
        module_components=module_components,
        components=components,
        index_products=index,
    )

    assert result["boundary_edges"] == []
    assert result["internal_edges"] == []


def test_build_context_pack_deduplicates_edges(module_components, components):
    """Duplicate edges (same from/to/type) appear only once."""
    from codewiki.src.be.generation.context_pack import build_context_pack

    edge = _make_edge(MODULE_SYMBOL, EXTERNAL_SYMBOL, EdgeType.CALLS)
    # Same edge duplicated
    index = _MockIndexProducts(cards=[], edges=[edge, edge])

    result = build_context_pack(
        module_components=module_components,
        components=components,
        index_products=index,
    )

    assert len(result["boundary_edges"]) == 1


def test_build_context_pack_caps_boundary_edges(module_components, components):
    """boundary_edges capped at 15."""
    from codewiki.src.be.generation.context_pack import build_context_pack

    edges = [
        _make_edge(MODULE_SYMBOL, f"py:src/ext/mod{i}.py#Sym{i}(class)", EdgeType.CALLS)
        for i in range(25)
    ]
    index = _MockIndexProducts(cards=[], edges=edges)

    result = build_context_pack(
        module_components=module_components,
        components=components,
        index_products=index,
    )

    assert len(result["boundary_edges"]) <= 15


def test_build_context_pack_caps_evidence_snippets(module_components, components):
    """evidence_snippets capped at 20."""
    from codewiki.src.be.generation.context_pack import build_context_pack

    refs = [_source_range(MODULE_FILE, line=i) for i in range(30)]
    card = _make_card(symbol_id=MODULE_SYMBOL)
    edge = _make_edge(MODULE_SYMBOL, EXTERNAL_SYMBOL, evidence_refs=refs)
    index = _MockIndexProducts(cards=[card], edges=[edge])

    result = build_context_pack(
        module_components=module_components,
        components=components,
        index_products=index,
    )

    assert len(result["evidence_snippets"]) <= 20


def test_build_context_pack_key_edges_truncated_to_three(module_components, components):
    """card.key_edges truncated to first 3 in the formatted output."""
    from codewiki.src.be.generation.context_pack import build_context_pack

    card = _make_card(
        symbol_id=MODULE_SYMBOL,
        key_edges=["edge1", "edge2", "edge3", "edge4", "edge5"],
    )
    index = _MockIndexProducts(cards=[card], edges=[])

    result = build_context_pack(
        module_components=module_components,
        components=components,
        index_products=index,
    )

    assert len(result["symbol_cards"]) == 1
    formatted = result["symbol_cards"][0]
    # edge4 and edge5 must NOT appear (only first 3 shown)
    assert "edge4" not in formatted
    assert "edge5" not in formatted


def test_format_glossary_empty():
    from codewiki.src.be.generation.context_pack import _format_glossary

    assert _format_glossary({}) == ""
    assert _format_glossary(None) == ""


def test_format_link_map_empty():
    from codewiki.src.be.generation.context_pack import _format_link_map

    assert _format_link_map({}) == ""
    assert _format_link_map(None) == ""


def test_build_context_pack_glossary_and_link_map_both_present(module_components, components):
    """Both glossary and link_map populated simultaneously with index."""
    from codewiki.src.be.generation.context_pack import build_context_pack

    card = _make_card(symbol_id=MODULE_SYMBOL)
    index = _MockIndexProducts(cards=[card], edges=[])

    result = build_context_pack(
        module_components=module_components,
        components=components,
        index_products=index,
        glossary={"key": "value"},
        link_map={"src/a.py": "docs/a.md"},
    )

    assert "key" in result["glossary_context"]
    assert "src/a.py" in result["link_map_context"]


def test_confidence_label_included_in_edge_description(module_components, components):
    """Edge description includes confidence label."""
    from codewiki.src.be.generation.context_pack import build_context_pack

    edge = _make_edge(MODULE_SYMBOL, EXTERNAL_SYMBOL, confidence=Confidence.LOW)
    index = _MockIndexProducts(cards=[], edges=[edge])

    result = build_context_pack(
        module_components=module_components,
        components=components,
        index_products=index,
    )

    assert len(result["boundary_edges"]) == 1
    assert "low" in result["boundary_edges"][0]
