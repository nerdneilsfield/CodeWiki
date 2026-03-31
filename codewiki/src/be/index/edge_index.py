"""EdgeIndex: queryable index over SymbolEdge relationships."""
from collections import defaultdict
from typing import Optional

from codewiki.src.be.index.models import SymbolEdge, EdgeType


class EdgeIndex:
    """Provides O(1) lookups for symbol relationships.

    Forward index (_by_from): from_symbol → edges
        Used by callees_of() — what does this symbol call/import/extend?

    Reverse index (_by_to): to_symbol → edges (resolved only)
        Used by callers_of() — who calls this symbol?
        Unresolved edges (to_symbol=None) are intentionally excluded from
        _by_to; they are only reachable via _by_from (callees_of).
    """

    def __init__(self, edges: list[SymbolEdge]):
        self._by_from: dict[str, list[SymbolEdge]] = defaultdict(list)
        self._by_to: dict[str, list[SymbolEdge]] = defaultdict(list)
        for e in edges:
            self._by_from[e.from_symbol].append(e)
            if e.to_symbol:
                self._by_to[e.to_symbol].append(e)

    def callers_of(self, symbol_id: str) -> list[SymbolEdge]:
        """Return edges where to_symbol == symbol_id (who calls/imports/extends this symbol)."""
        return list(self._by_to.get(symbol_id, []))

    def callees_of(self, symbol_id: str) -> list[SymbolEdge]:
        """Return edges where from_symbol == symbol_id (what this symbol calls/imports/extends)."""
        return list(self._by_from.get(symbol_id, []))

    def edges_of(self, symbol_id: str, edge_type: Optional[EdgeType] = None) -> list[SymbolEdge]:
        """Return all edges involving symbol_id as source or resolved target.

        Optionally filter by edge_type.
        """
        seen: set[tuple[str, str, str]] = set()
        result: list[SymbolEdge] = []
        for e in self.callees_of(symbol_id) + self.callers_of(symbol_id):
            key = (e.from_symbol, e.to_symbol or "", e.edge_type.value)
            if key not in seen:
                seen.add(key)
                if edge_type is None or e.edge_type == edge_type:
                    result.append(e)
        return result

    def dependency_subgraph(self, symbol_ids: set[str]) -> list[SymbolEdge]:
        """Return edges where both from_symbol and to_symbol are in symbol_ids.

        Only resolved edges (to_symbol is not None) can satisfy this condition.
        """
        result = []
        for sid in symbol_ids:
            for e in self._by_from.get(sid, []):
                if e.to_symbol and e.to_symbol in symbol_ids:
                    result.append(e)
        return result
