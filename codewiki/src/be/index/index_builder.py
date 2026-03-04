# codewiki/src/be/index/index_builder.py
"""IndexBuilder: orchestrates index construction from source files."""
import fnmatch
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from codewiki.src.be.index.models import (
    Symbol, ImportStatement, SymbolEdge, ComponentCard, EdgeType, Confidence, SourceRange,
)
from codewiki.src.be.index.symbol_table import SymbolTable
from codewiki.src.be.index.import_graph import ImportGraph
from codewiki.src.be.index.component_card import CardBuilder
from codewiki.src.be.index.adapters.python_adapter import PythonIndexAdapter
from codewiki.src.be.dependency_analyzer.utils.patterns import CODE_EXTENSIONS
from codewiki.src.be.dependency_analyzer.utils.security import safe_open_text

logger = logging.getLogger(__name__)


@dataclass
class IndexProducts:
    """All outputs of the index building process."""
    symbol_table: SymbolTable
    import_graph: ImportGraph
    edges: list[SymbolEdge]
    cards: list[ComponentCard]

    def to_dict(self) -> dict:
        return {
            "symbols": [s.model_dump() for s in self.symbol_table.all_symbols()],
            "imports": [i.model_dump() for i in self.import_graph.all_imports()],
            "edges": [e.model_dump() for e in self.edges],
            "cards": [c.model_dump() for c in self.cards],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "IndexProducts":
        symbols = [Symbol.model_validate(d) for d in data["symbols"]]
        imports = [ImportStatement.model_validate(d) for d in data["imports"]]
        edges = [SymbolEdge.model_validate(d) for d in data["edges"]]
        cards = [ComponentCard.model_validate(d) for d in data["cards"]]
        return cls(
            symbol_table=SymbolTable(symbols),
            import_graph=ImportGraph(imports),
            edges=edges,
            cards=cards,
        )


class IndexBuilder:
    """Builds index products from a repository's source files."""

    def __init__(self, repo_path: str, include_patterns: list[str] | None = None,
                 exclude_patterns: list[str] | None = None):
        self.repo_path = os.path.abspath(repo_path)
        self.include_patterns = include_patterns
        self.exclude_patterns = exclude_patterns

    def build(self) -> IndexProducts:
        all_symbols: list[Symbol] = []
        all_imports: list[ImportStatement] = []

        # Walk source files
        for file_path, lang in self._discover_files():
            abs_path = os.path.join(self.repo_path, file_path)
            try:
                content = safe_open_text(Path(self.repo_path), Path(abs_path))
            except Exception as e:
                logger.warning(f"Cannot read {file_path}: {e}")
                continue

            if lang == "python":
                adapter = PythonIndexAdapter(abs_path, content, self.repo_path)
                symbols, imports = adapter.extract()
                all_symbols.extend(symbols)
                all_imports.extend(imports)
            elif lang in ("typescript", "javascript"):
                try:
                    from codewiki.src.be.index.adapters.ts_js_adapter import TSJSIndexAdapter
                    adapter = TSJSIndexAdapter(abs_path, content, self.repo_path, language=lang)
                    symbols, imports = adapter.extract()
                    all_symbols.extend(symbols)
                    all_imports.extend(imports)
                except ImportError:
                    logger.debug(f"TS/JS adapter not available, using generic for {file_path}")
                    self._generic_fallback(abs_path, content, lang, all_symbols)
            else:
                self._generic_fallback(abs_path, content, lang, all_symbols)

        # Build products
        symbol_table = SymbolTable(all_symbols)
        import_graph = ImportGraph(all_imports)
        edges = self._build_edges(all_imports, symbol_table)
        card_builder = CardBuilder()
        cards = [card_builder.build_card(s, edges) for s in all_symbols if s.parent_symbol_id is None]

        logger.info(
            f"Index built: {len(all_symbols)} symbols, {len(all_imports)} imports, "
            f"{len(edges)} edges, {len(cards)} cards"
        )
        return IndexProducts(symbol_table, import_graph, edges, cards)

    def _generic_fallback(self, abs_path: str, content: str, lang: str,
                          all_symbols: list[Symbol]):
        """Parse with existing analyzer and convert via generic adapter."""
        from codewiki.src.be.index.adapters.generic_adapter import GenericIndexAdapter

        nodes = self._analyze_with_existing(abs_path, content, lang)
        if nodes:
            adapter = GenericIndexAdapter(lang=lang)
            all_symbols.extend(adapter.convert(nodes))

    def _analyze_with_existing(self, abs_path: str, content: str, lang: str) -> list:
        """Use existing language analyzers to get Node objects.

        NOTE: This calls CallGraphAnalyzer._analyze_code_file, a private method.
        TODO: Replace with a public API wrapper once CallGraphAnalyzer exposes one.
        """
        try:
            from codewiki.src.be.dependency_analyzer.analysis.call_graph_analyzer import CallGraphAnalyzer
            analyzer = CallGraphAnalyzer()
            file_info = {"path": os.path.relpath(abs_path, self.repo_path), "language": lang}
            funcs, _ = analyzer._analyze_code_file(self.repo_path, file_info)
            return list(funcs.values())
        except Exception as e:
            logger.debug(f"Existing analyzer failed for {abs_path}: {e}")
            return []

    def _should_include(self, rel_path: str) -> bool:
        """Check if a file passes include/exclude pattern filters."""
        if self.include_patterns:
            if not any(fnmatch.fnmatch(rel_path, p) for p in self.include_patterns):
                return False
        if self.exclude_patterns:
            if any(fnmatch.fnmatch(rel_path, p) for p in self.exclude_patterns):
                return False
        return True

    def _discover_files(self) -> list[tuple[str, str]]:
        """Walk repo and return sorted (relative_path, language) pairs for source files."""
        results = []
        for root, dirs, files in os.walk(self.repo_path):
            # Skip common non-source directories
            dirs[:] = sorted(d for d in dirs if d not in {
                ".git", "node_modules", "__pycache__", ".venv", "venv",
                ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
            })
            for fname in sorted(files):
                abs_path = os.path.join(root, fname)
                rel_path = os.path.relpath(abs_path, self.repo_path).replace("\\", "/")
                ext = os.path.splitext(fname)[1].lower()
                lang = CODE_EXTENSIONS.get(ext)
                if lang and self._should_include(rel_path):
                    results.append((rel_path, lang))
        return results

    def _build_edges(self, imports: list[ImportStatement],
                     symbol_table: SymbolTable) -> list[SymbolEdge]:
        """Build SymbolEdge list from resolved imports.

        Import edges are recorded as file_path → symbol, not anchored to an
        arbitrary first symbol in the file.  The from_symbol uses the file path
        directly as an identifier (prefixed with "file:") so that downstream
        consumers can distinguish file-level import edges from symbol-to-symbol edges.
        """
        edges = []
        # Pre-build (file_path, name) → Symbol index for O(1) lookup
        name_index: dict[tuple[str, str], Symbol] = {
            (s.file_path, s.name): s for s in symbol_table.all_symbols()
        }

        for imp in imports:
            if not imp.resolved_path:
                continue
            for name in imp.imported_names:
                if name == "*":
                    continue
                to_sym = name_index.get((imp.resolved_path, name))
                edges.append(SymbolEdge(
                    edge_type=EdgeType.IMPORTS,
                    from_symbol=f"file:{imp.file_path}",
                    to_symbol=to_sym.symbol_id if to_sym else None,
                    to_unresolved=name if not to_sym else None,
                    evidence_refs=[SourceRange(
                        file_path=imp.file_path,
                        start_line=imp.line, start_col=0,
                        end_line=imp.line, end_col=0,
                    )],
                    confidence=Confidence.HIGH if to_sym else Confidence.LOW,
                    resolver="ast",
                ))
        return edges
