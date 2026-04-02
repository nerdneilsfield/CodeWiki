# codewiki/src/be/index/import_graph.py
"""ImportGraph: file-level import relationships and symbol resolution."""

from collections import defaultdict
from typing import Optional

from codewiki.src.be.index.models import ImportStatement, Symbol
from codewiki.src.be.index.symbol_table import SymbolTable


class ImportGraph:
    """Tracks import statements across the codebase and resolves imported symbols."""

    def __init__(self, imports: list[ImportStatement]):
        self._by_file: dict[str, list[ImportStatement]] = defaultdict(list)
        self._by_resolved: dict[str, list[str]] = defaultdict(
            list
        )  # resolved_path → [importing files]

        for imp in imports:
            self._by_file[imp.file_path].append(imp)
            if imp.resolved_path:
                self._by_resolved[imp.resolved_path].append(imp.file_path)

    def imports_of(self, file_path: str) -> list[ImportStatement]:
        return self._by_file.get(file_path, [])

    def importers_of(self, file_path: str) -> list[str]:
        return list(set(self._by_resolved.get(file_path, [])))

    def file_dependency_graph(self) -> dict[str, set[str]]:
        graph: dict[str, set[str]] = defaultdict(set)
        for file_path, imps in self._by_file.items():
            for imp in imps:
                if imp.resolved_path:
                    graph[file_path].add(imp.resolved_path)
        return dict(graph)

    def resolve(self, file_path: str, name: str, symbol_table: SymbolTable) -> Optional[Symbol]:
        """Resolve an imported name to a Symbol via the import chain.

        Handles both direct names (``from mod import helper; helper()``)
        and aliases (``from mod import helper as h; h()``).
        """
        for imp in self._by_file.get(file_path, []):
            if not imp.resolved_path:
                continue
            # Direct name match
            if name in imp.imported_names:
                for sym in symbol_table.by_file(imp.resolved_path):
                    if sym.name == name:
                        return sym
            # Alias match: ``from mod import X as Y`` — caller uses Y
            if imp.alias and imp.alias == name and imp.imported_names:
                original_name = imp.imported_names[0]
                for sym in symbol_table.by_file(imp.resolved_path):
                    if sym.name == original_name:
                        return sym
        return None

    def all_imports(self) -> list[ImportStatement]:
        result = []
        for imps in self._by_file.values():
            result.extend(imps)
        return result
