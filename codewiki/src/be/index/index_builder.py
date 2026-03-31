# codewiki/src/be/index/index_builder.py
"""IndexBuilder: orchestrates index construction from source files."""
import fnmatch
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from codewiki.src.be.index.models import (
    Symbol, ImportStatement, SymbolEdge, ComponentCard, EdgeType, Confidence, SourceRange,
    SymbolKind,
)
from codewiki.src.be.index.symbol_table import SymbolTable
from codewiki.src.be.index.import_graph import ImportGraph
from codewiki.src.be.index.component_card import CardBuilder
from codewiki.src.be.index.adapters.python_adapter import PythonIndexAdapter
from codewiki.src.be.index.edge_index import EdgeIndex
from codewiki.src.be.index.graph_stats import GraphStats
from codewiki.src.be.dependency_analyzer.utils.patterns import CODE_EXTENSIONS
from codewiki.src.be.dependency_analyzer.utils.security import safe_open_text

logger = logging.getLogger(__name__)

INDEX_VERSION = "2"


@dataclass
class IndexProducts:
    """All outputs of the index building process."""
    symbol_table: SymbolTable
    import_graph: ImportGraph
    edges: list[SymbolEdge]
    cards: list[ComponentCard]
    stats: "GraphStats | None" = field(default=None)
    edge_index: EdgeIndex = field(init=False, repr=False)

    def __post_init__(self):
        self.edge_index = EdgeIndex(self.edges)

    def to_dict(self) -> dict:
        return {
            "symbols": [s.model_dump() for s in self.symbol_table.all_symbols()],
            "imports": [i.model_dump() for i in self.import_graph.all_imports()],
            "edges": [e.model_dump() for e in self.edges],
            "cards": [c.model_dump() for c in self.cards],
            "stats": self.stats.model_dump() if self.stats else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "IndexProducts":
        symbols = [Symbol.model_validate(d) for d in data["symbols"]]
        imports = [ImportStatement.model_validate(d) for d in data["imports"]]
        edges = [SymbolEdge.model_validate(d) for d in data["edges"]]
        cards = [ComponentCard.model_validate(d) for d in data["cards"]]
        stats_data = data.get("stats")
        stats = GraphStats.model_validate(stats_data) if stats_data else None
        return cls(
            symbol_table=SymbolTable(symbols),
            import_graph=ImportGraph(imports),
            edges=edges,
            cards=cards,
            stats=stats,
        )


class IndexBuilder:
    """Builds index products from a repository's source files."""

    def __init__(self, repo_path: str, include_patterns: list[str] | None = None,
                 exclude_patterns: list[str] | None = None,
                 output_dir: str | None = None):
        self.repo_path = os.path.abspath(repo_path)
        self.include_patterns = include_patterns
        self.exclude_patterns = exclude_patterns
        self.output_dir = output_dir

    # ── Caching ────────────────────────────────────────────────────────────

    def _get_commit_hash(self) -> str | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, cwd=self.repo_path, timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def _cache_path(self) -> Path | None:
        if not self.output_dir:
            return None
        return Path(self.output_dir) / "_index_cache.json"

    def _try_load_cache(self) -> "IndexProducts | None":
        cache_file = self._cache_path()
        if not cache_file or not cache_file.exists():
            return None
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            cache_key = data.get("_cache_key", {})
            if (cache_key.get("commit") == self._get_commit_hash()
                    and cache_key.get("index_version") == INDEX_VERSION):
                logger.info("Index cache hit — skipping rebuild")
                return IndexProducts.from_dict(data)
        except Exception as e:
            logger.debug(f"Cache load failed: {e}")
        return None

    def _save_cache(self, products: "IndexProducts") -> None:
        cache_file = self._cache_path()
        if not cache_file:
            return
        try:
            data = products.to_dict()
            data["_cache_key"] = {
                "commit": self._get_commit_hash(),
                "index_version": INDEX_VERSION,
            }
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            logger.info(f"Index cache saved to {cache_file}")
        except Exception as e:
            logger.warning(f"Cache save failed: {e}")

    # ── Build ──────────────────────────────────────────────────────────────

    def build(self) -> IndexProducts:
        cached = self._try_load_cache()
        if cached is not None:
            return cached

        all_symbols: list[Symbol] = []
        all_imports: list[ImportStatement] = []
        # Pass 1: collect adapter references for two-pass call extraction.
        adapters_by_file: dict[str, object] = {}
        # Placeholder for generic adapter call relationships (Phase 2 data path).
        all_call_rels: list = []

        # Pass 1: Walk source files, extract symbols + imports, save adapters.
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
                adapters_by_file[file_path] = adapter
            elif lang in ("typescript", "javascript"):
                try:
                    from codewiki.src.be.index.adapters.ts_js_adapter import TSJSIndexAdapter
                    adapter = TSJSIndexAdapter(abs_path, content, self.repo_path, language=lang)
                    symbols, imports = adapter.extract()
                    all_symbols.extend(symbols)
                    all_imports.extend(imports)
                    adapters_by_file[file_path] = adapter
                except ImportError:
                    logger.debug(f"TS/JS adapter not available, using generic for {file_path}")
                    self._generic_fallback(abs_path, content, lang, all_symbols, all_call_rels)
            else:
                self._generic_fallback(abs_path, content, lang, all_symbols, all_call_rels)

        # Build cross-file indexes needed for Pass 2 resolution.
        symbol_table = SymbolTable(all_symbols)
        import_graph = ImportGraph(all_imports)

        # IMPORTS + EXTENDS edges (unchanged from original single-pass).
        edges = self._build_edges(all_imports, symbol_table)
        edges.extend(self._build_extends_edges(symbol_table))

        # Pass 2a: language-specific CALLS extraction (Python AST, TS tree-sitter, etc.).
        for file_path, adapter in adapters_by_file.items():
            if hasattr(adapter, "extract_calls"):
                try:
                    edges.extend(adapter.extract_calls(symbol_table, import_graph))
                except Exception as e:
                    logger.warning(f"extract_calls failed for {file_path}: {e}")

        # Pass 2b: convert generic adapter CallRelationships collected in Pass 1.
        # all_call_rels is populated by _generic_fallback via _analyze_with_existing.
        if all_call_rels:
            from codewiki.src.be.index.adapters.generic_adapter import GenericIndexAdapter
            edges.extend(GenericIndexAdapter.convert_calls(all_call_rels, symbol_table))

        card_builder = CardBuilder()
        cards = [card_builder.build_card(s, edges) for s in all_symbols if s.parent_symbol_id is None]

        stats = GraphStats.compute(all_symbols, edges)
        logger.info(
            f"Index built: {len(all_symbols)} symbols, {len(all_imports)} imports, "
            f"{len(edges)} edges, {len(cards)} cards"
        )
        products = IndexProducts(symbol_table, import_graph, edges, cards, stats=stats)
        self._save_cache(products)
        return products

    def _generic_fallback(self, abs_path: str, content: str, lang: str,
                          all_symbols: list[Symbol], all_call_rels: list | None = None):
        """Parse with existing analyzer and convert via generic adapter."""
        from codewiki.src.be.index.adapters.generic_adapter import GenericIndexAdapter

        nodes, relationships = self._analyze_with_existing(abs_path, content, lang)
        if nodes:
            adapter = GenericIndexAdapter(lang=lang)
            all_symbols.extend(adapter.convert(nodes))
        if all_call_rels is not None and relationships:
            all_call_rels.extend(relationships)

    def _analyze_with_existing(self, abs_path: str, content: str, lang: str) -> tuple[list, list]:
        """Use existing language analyzers to get (Node objects, CallRelationships).

        NOTE: This calls CallGraphAnalyzer._analyze_code_file, a private method.
        TODO: Replace with a public API wrapper once CallGraphAnalyzer exposes one.

        Returns:
            A (nodes, relationships) tuple.  Both are empty lists on failure.
        """
        try:
            from codewiki.src.be.dependency_analyzer.analysis.call_graph_analyzer import CallGraphAnalyzer
            analyzer = CallGraphAnalyzer()
            file_info = {"path": os.path.relpath(abs_path, self.repo_path), "language": lang}
            funcs, relationships = analyzer._analyze_code_file(self.repo_path, file_info)
            return list(funcs.values()), relationships
        except Exception as e:
            logger.debug(f"Existing analyzer failed for {abs_path}: {e}")
            return [], []

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

    def _build_extends_edges(self, symbol_table: SymbolTable) -> list[SymbolEdge]:
        """Build EXTENDS edges from class inheritance relationships."""
        edges: list[SymbolEdge] = []
        # Build (file_path, name) → Symbol index for same-file lookup
        name_index: dict[tuple[str, str], Symbol] = {
            (s.file_path, s.name): s for s in symbol_table.all_symbols()
        }

        for sym in symbol_table.all_symbols():
            if sym.kind != SymbolKind.CLASS:
                continue
            base_names = self._extract_base_classes(sym)
            for base_name in base_names:
                # Try same-file lookup first, then cross-file search
                to_sym = name_index.get((sym.file_path, base_name))
                if not to_sym:
                    candidates = symbol_table.search(base_name)
                    to_sym = next(
                        (c for c in candidates if c.kind == SymbolKind.CLASS and c.name == base_name),
                        None,
                    )

                edges.append(SymbolEdge(
                    edge_type=EdgeType.EXTENDS,
                    from_symbol=sym.symbol_id,
                    to_symbol=to_sym.symbol_id if to_sym else None,
                    to_unresolved=base_name if not to_sym else None,
                    evidence_refs=[SourceRange(
                        file_path=sym.file_path,
                        start_line=sym.range.start_line,
                        start_col=sym.range.start_col,
                        end_line=sym.range.start_line,
                        end_col=sym.range.end_col,
                    )],
                    confidence=Confidence.HIGH if to_sym else Confidence.LOW,
                    resolver="ast" if sym.lang == "python" else "treesitter",
                ))
        return edges

    def _extract_base_classes(self, sym: Symbol) -> list[str]:
        """Extract base class names from a class symbol's signature.

        Handles Python signatures of the form ``class Foo(Bar, Baz)``.
        TS/JS signatures currently only carry ``class Foo`` (no extends info
        until the TS/JS adapter is updated to embed it).
        """
        if not sym.signature:
            return []
        match = re.search(r'\(([^)]+)\)', sym.signature)
        if not match:
            return []
        bases_str = match.group(1)
        bases = [b.strip() for b in bases_str.split(',')]
        # Filter out keyword args (metaclass=...), starred args, and generic
        # type parameters (Generic[T], Protocol[T], etc.)
        filtered = []
        for b in bases:
            if not b:
                continue
            if '=' in b:
                continue
            if b.startswith('*'):
                continue
            # Strip generic params: Generic[T] → Generic, but keep as base
            bare = re.sub(r'\[.*\]$', '', b).strip()
            if bare:
                filtered.append(bare)
        return filtered
