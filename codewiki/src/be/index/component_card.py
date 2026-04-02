# codewiki/src/be/index/component_card.py
"""ComponentCard builder: creates LLM-facing symbol summaries."""

import re

from codewiki.src.be.index.models import Symbol, SymbolEdge, ComponentCard


class CardBuilder:
    """Builds ComponentCard from Symbol + its outgoing edges."""

    def __init__(self, max_edges: int = 5):
        self.max_edges = max_edges

    def build_card(self, symbol: Symbol, edges: list[SymbolEdge]) -> ComponentCard:
        outgoing = [e for e in edges if e.from_symbol == symbol.symbol_id]
        key_edges = [
            f"{e.edge_type.value}: {e.to_symbol or e.to_unresolved}"
            for e in outgoing[: self.max_edges]
        ]
        return ComponentCard(
            symbol_id=symbol.symbol_id,
            signature=symbol.signature or symbol.name,
            docstring_summary=self._truncate_docstring(symbol.docstring),
            kind=symbol.kind,
            key_edges=key_edges,
            file_context=f"{symbol.file_path} (lines {symbol.range.start_line}-{symbol.range.end_line})",
        )

    _MAX_SUMMARY_CHARS = 300

    @staticmethod
    def _truncate_docstring(doc: str | None, max_sentences: int = 2) -> str:
        if not doc:
            return ""
        # Split on sentence boundaries (period + space or end)
        sentences = re.split(r"(?<=[.!?])\s+", doc.strip())
        summary = " ".join(sentences[:max_sentences]).strip()
        if len(summary) > CardBuilder._MAX_SUMMARY_CHARS:
            summary = summary[: CardBuilder._MAX_SUMMARY_CHARS].rstrip() + "…"
        return summary
