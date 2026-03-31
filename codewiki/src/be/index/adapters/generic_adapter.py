# codewiki/src/be/index/adapters/generic_adapter.py
"""Generic adapter: converts existing Node objects to Symbol (1:1 fallback)."""
import hashlib
from typing import Optional

from codewiki.src.be.dependency_analyzer.models.core import Node
from codewiki.src.be.index.models import (
    Symbol, SymbolKind, Visibility, ExportStatus, SourceRange,
    SymbolEdge, EdgeType, Confidence,
)

_KIND_MAP = {
    "class": SymbolKind.CLASS,
    "interface": SymbolKind.INTERFACE,
    "struct": SymbolKind.STRUCT,
    "enum": SymbolKind.ENUM,
    "trait": SymbolKind.TRAIT,
    "method": SymbolKind.METHOD,
    "function": SymbolKind.FUNCTION,
    "variable": SymbolKind.VARIABLE,
    "constant": SymbolKind.CONSTANT,
    "type": SymbolKind.TYPE,
}


class GenericIndexAdapter:
    """Converts existing Node objects to Symbol with minimal metadata."""

    def __init__(self, lang: str):
        self.lang = lang

    def convert(self, nodes: list[Node]) -> list[Symbol]:
        return [self._convert_one(n) for n in nodes]

    @staticmethod
    def convert_calls(relationships: list, symbol_table) -> list[SymbolEdge]:
        """Convert a list of CallRelationship objects into SymbolEdge CALLS edges.

        Args:
            relationships: list of CallRelationship (typed as list to avoid
                           importing the dependency_analyzer model at module level).
            symbol_table:  SymbolTable instance used for symbol resolution.

        Returns:
            A new list of SymbolEdge objects (never mutates inputs).
        """
        _CALLABLE_KINDS = {SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.CLASS}

        def _lookup(qname: str) -> Optional[Symbol]:
            # 1. Exact qualified-name match.
            sym = symbol_table.by_qualified_name(qname)
            if sym:
                return sym
            # 2. Fuzzy: search by simple name, restrict to callable kinds.
            short = qname.split(".")[-1]
            candidates = symbol_table.by_name(short)
            for c in candidates:
                if c.kind in _CALLABLE_KINDS and c.name == short:
                    return c
            return None

        edges: list[SymbolEdge] = []
        for rel in relationships:
            caller_sym = _lookup(rel.caller)
            callee_sym = _lookup(rel.callee)

            both_resolved = caller_sym is not None and callee_sym is not None
            confidence = Confidence.HIGH if both_resolved else Confidence.LOW

            # Build evidence ref from caller file + call line.
            # Always produce at least one SourceRange — use caller's file
            # if resolved, otherwise extract file from the caller qname.
            evidence_file = ""
            if caller_sym:
                evidence_file = caller_sym.file_path
            else:
                # Best-effort: derive path from qualified caller name
                parts = rel.caller.rsplit(".", 1)
                evidence_file = parts[0].replace(".", "/") if parts else ""
            evidence = [SourceRange(
                file_path=evidence_file,
                start_line=rel.call_line or 0,
                start_col=0,
                end_line=rel.call_line or 0,
                end_col=0,
            )]

            edges.append(SymbolEdge(
                edge_type=EdgeType.CALLS,
                from_symbol=caller_sym.symbol_id if caller_sym else f"unresolved:{rel.caller}",
                to_symbol=callee_sym.symbol_id if callee_sym else None,
                to_unresolved=rel.callee if callee_sym is None else None,
                evidence_refs=evidence,
                confidence=confidence,
                resolver="call_graph_analyzer",
            ))

        return edges

    def _convert_one(self, node: Node) -> Symbol:
        kind = _KIND_MAP.get(node.component_type, SymbolKind.FUNCTION)
        rel_path = node.relative_path.replace("\\", "/")
        source_hash = hashlib.sha256(
            (node.source_code or node.id).encode()
        ).hexdigest()[:16]

        return Symbol(
            symbol_id=f"{self.lang}:{rel_path}#{node.name}:{node.start_line}({kind.value})",
            lang=self.lang,
            kind=kind,
            name=node.name,
            qualified_name=node.id,
            file_path=rel_path,
            range=SourceRange(
                file_path=rel_path,
                start_line=node.start_line,
                start_col=0,
                end_line=node.end_line,
                end_col=0,
            ),
            signature=node.display_name,
            visibility=Visibility.UNKNOWN,
            export_status=ExportStatus.UNKNOWN,
            docstring=node.docstring or None,
            source_hash=source_hash,
        )
