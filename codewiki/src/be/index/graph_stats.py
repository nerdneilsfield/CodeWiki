"""GraphStats: summary statistics for the index graph."""

from collections import Counter

from pydantic import BaseModel

from codewiki.src.be.index.models import Symbol, SymbolEdge


class GraphStats(BaseModel):
    """Summary statistics for edges and symbols in the index graph.

    All dict keys are EdgeType.value strings (e.g. "imports", "calls").
    """

    edge_counts: dict[str, int]  # EdgeType.value → total edge count
    unresolved_counts: dict[str, int]  # EdgeType.value → count where to_unresolved is set
    unresolved_ratios: dict[str, float]  # EdgeType.value → ratio in [0.0, 1.0]
    total_symbols: int
    total_edges: int

    @classmethod
    def compute(cls, symbols: list[Symbol], edges: list[SymbolEdge]) -> "GraphStats":
        """Compute statistics from a flat list of symbols and edges.

        Safe against empty inputs — no ZeroDivisionError.
        """
        edge_counts: Counter[str] = Counter()
        unresolved_counts: Counter[str] = Counter()

        for e in edges:
            key = e.edge_type.value
            edge_counts[key] += 1
            if e.to_unresolved is not None:
                unresolved_counts[key] += 1

        unresolved_ratios: dict[str, float] = {
            key: unresolved_counts.get(key, 0) / total
            for key, total in edge_counts.items()
            if total > 0
        }

        return cls(
            edge_counts=dict(edge_counts),
            unresolved_counts=dict(unresolved_counts),
            unresolved_ratios=unresolved_ratios,
            total_symbols=len(symbols),
            total_edges=len(edges),
        )
