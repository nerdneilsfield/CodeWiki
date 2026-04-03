"""Context pack builder: assembles evidence-rich context for LLM prompts.

Aligned with v3.md section 6.4 RETRIEVE_CONTEXT pseudocode.
All filtering is component-level precise (not file-level).
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_context_pack(
    module_components: list[str],
    components: dict[str, Any],  # Dict[str, Node]
    index_products: Any | None,  # IndexProducts or None
    glossary: dict[str, str] | None = None,
    link_map: dict[str, str] | None = None,
) -> dict:
    """Build evidence-rich context for LLM prompt.

    Returns dict with:
    - symbol_cards: list[str] — formatted symbol summaries
    - boundary_edges: list[str] — cross-module relationship descriptions
    - internal_edges: list[str] — intra-module relationship descriptions
    - evidence_snippets: list[str] — code location references
    - glossary_context: str — formatted glossary excerpt
    - link_map_context: str — formatted link map excerpt

    Returns empty dict sections when index_products is None (graceful degradation).
    """
    result = {
        "symbol_cards": [],
        "boundary_edges": [],
        "internal_edges": [],
        "evidence_snippets": [],
        "glossary_context": "",
        "link_map_context": "",
    }

    if not index_products:
        if glossary:
            result["glossary_context"] = _format_glossary(glossary)
        if link_map:
            result["link_map_context"] = _format_link_map(link_map)
        return result

    # Build precise set of symbol_ids belonging to this module's components
    module_sym_ids = _build_module_symbol_ids(module_components, components, index_products)

    # 1. Symbol cards (component-level precise)
    result["symbol_cards"] = _build_symbol_cards(module_sym_ids, index_products)

    # 2. Boundary and internal edges (component-level precise)
    boundary, internal = _classify_edges(module_sym_ids, index_products)
    result["boundary_edges"] = boundary
    result["internal_edges"] = internal

    # 3. Evidence snippets
    result["evidence_snippets"] = _build_evidence_snippets(module_sym_ids, index_products)

    # 4. Glossary and link map
    if glossary:
        result["glossary_context"] = _format_glossary(glossary)
    if link_map:
        result["link_map_context"] = _format_link_map(link_map)

    return result


def _build_module_symbol_ids(module_components, components, index_products) -> set[str]:
    """Build set of symbol_ids that belong to this module's components (precise).

    Maps each component to its symbols by matching (file, component_name)
    against the symbol table, same logic as clustering/graph_builder.

    Falls back to file-level matching if symbol_table is unavailable,
    or to card-based matching if neither is available.
    """
    symbol_table = getattr(index_products, "symbol_table", None)

    if symbol_table:
        from codewiki.src.be.clustering.graph_builder import extract_component_name

        symbol_ids: set[str] = set()
        for cid in module_components:
            node = components.get(cid)
            if not node:
                continue
            file_path = getattr(node, "relative_path", "").replace("\\", "/")
            comp_name = extract_component_name(cid)
            for sym in symbol_table.by_file(file_path):
                if sym.name == comp_name:
                    symbol_ids.add(sym.symbol_id)
                elif not comp_name:
                    # Only include if this is the sole symbol in the file (unambiguous)
                    file_syms = symbol_table.by_file(file_path)
                    if len(file_syms) == 1:
                        symbol_ids.add(sym.symbol_id)
                    else:
                        logger.warning(
                            "Skipping ambiguous symbol %s: comp_name is empty "
                            "but file %s has %d symbols",
                            sym.symbol_id,
                            file_path,
                            len(file_syms),
                        )
        return symbol_ids

    # Fallback: match cards by file path (less precise but works with mock)
    module_files = set()
    for cid in module_components:
        node = components.get(cid)
        if node:
            module_files.add(getattr(node, "relative_path", "").replace("\\", "/"))

    symbol_ids = set()
    for card in getattr(index_products, "cards", []):
        file_path = _extract_file(card.symbol_id)
        if file_path in module_files:
            symbol_ids.add(card.symbol_id)

    # Also include edge endpoints in module files
    for edge in getattr(index_products, "edges", []):
        from_file = _extract_file(edge.from_symbol)
        if from_file in module_files:
            symbol_ids.add(edge.from_symbol)
        if edge.to_symbol:
            to_file = _extract_file(edge.to_symbol)
            if to_file in module_files:
                symbol_ids.add(edge.to_symbol)

    return symbol_ids


def _build_symbol_cards(module_sym_ids: set[str], index_products) -> list[str]:
    """Format ComponentCards for module's symbols (component-level precise)."""
    cards = []
    for card in index_products.cards:
        if card.symbol_id in module_sym_ids:
            formatted = (
                f"**{card.signature}** ({card.kind.value}): "
                f"{card.docstring_summary or 'No docstring'}"
            )
            if card.key_edges:
                formatted += f" | Edges: {', '.join(card.key_edges[:3])}"
            formatted += f" | {card.file_context}"
            cards.append(formatted)
    return cards


def _classify_edges(module_sym_ids: set[str], index_products):
    """Split edges into boundary (cross-module) and internal.

    Uses component-level symbol_id set for precise classification.
    """
    boundary = []
    internal = []
    seen: set[tuple[str, str, str]] = set()

    edge_index = getattr(index_products, "edge_index", None)

    if edge_index is None:
        candidate_edges = getattr(index_products, "edges", [])
    else:
        candidate_edges = []
        for symbol_id in module_sym_ids:
            candidate_edges.extend(edge_index.callees_of(symbol_id))
            candidate_edges.extend(edge_index.callers_of(symbol_id))

    for edge in candidate_edges:
        if not edge.to_symbol:
            continue

        edge_key = (edge.from_symbol, edge.to_symbol, edge.edge_type.value)
        if edge_key in seen:
            continue
        seen.add(edge_key)

        from_in = edge.from_symbol in module_sym_ids
        to_in = edge.to_symbol in module_sym_ids

        desc = f"{edge.from_symbol} --{edge.edge_type.value}--> {edge.to_symbol}"
        if edge.confidence:
            desc += f" [{edge.confidence.value}]"

        if from_in and to_in:
            internal.append(desc)
        elif from_in or to_in:
            boundary.append(desc)

    return boundary[:15], internal[:15]


def _build_evidence_snippets(module_sym_ids: set[str], index_products) -> list[str]:
    """Extract file:line evidence references for module symbols."""
    snippets = []
    for edge in index_products.edges:
        if edge.from_symbol not in module_sym_ids:
            continue
        for ref in edge.evidence_refs:
            snippet = f"{ref.file_path}:{ref.start_line} ({edge.edge_type.value})"
            snippets.append(snippet)
            if len(snippets) >= 20:
                return snippets
    return snippets


def _extract_file(symbol_id: str) -> str:
    """Extract file path from symbol_id."""
    if not symbol_id:
        return ""
    if symbol_id.startswith("file:"):
        return symbol_id[5:]
    if ":" in symbol_id and "#" in symbol_id:
        return symbol_id.split(":", 1)[1].split("#", 1)[0]
    return ""


def _format_glossary(glossary: dict[str, str] | None) -> str:
    """Format glossary dict into prompt-friendly text."""
    if not glossary:
        return ""
    lines = [f"- **{term}**: {defn}" for term, defn in sorted(glossary.items())]
    return "\n".join(lines)


def _format_link_map(link_map: dict[str, str] | None) -> str:
    """Format link map into prompt-friendly text."""
    if not link_map:
        return ""
    lines = [f"- [{path}]({doc_path})" for path, doc_path in sorted(link_map.items())]
    return "\n".join(lines)


def format_context_pack_section(context_pack: dict | None) -> str:
    """Format context pack dict into prompt sections.

    Used by all three prompt paths (leaf, system, overview).
    Returns empty string if context_pack is None or empty.
    """
    if not context_pack:
        return ""

    sections = []

    if context_pack.get("symbol_cards"):
        sections.append(
            "<SYMBOL_CARDS>\n"
            "Static analysis summaries (use for evidence citations):\n"
            + "\n".join(f"- {c}" for c in context_pack["symbol_cards"])
            + "\n</SYMBOL_CARDS>"
        )

    if context_pack.get("boundary_edges"):
        sections.append(
            "<BOUNDARY_EDGES>\n"
            "External dependencies of this module:\n"
            + "\n".join(f"- {e}" for e in context_pack["boundary_edges"])
            + "\n</BOUNDARY_EDGES>"
        )

    if context_pack.get("internal_edges"):
        sections.append(
            "<INTERNAL_EDGES>\n"
            "Internal relationships within this module:\n"
            + "\n".join(f"- {e}" for e in context_pack["internal_edges"])
            + "\n</INTERNAL_EDGES>"
        )

    if context_pack.get("glossary_context"):
        sections.append("<GLOSSARY>\n" + context_pack["glossary_context"] + "\n</GLOSSARY>")

    if context_pack.get("link_map_context"):
        sections.append("<LINK_MAP>\n" + context_pack["link_map_context"] + "\n</LINK_MAP>")

    return "\n\n".join(sections)
