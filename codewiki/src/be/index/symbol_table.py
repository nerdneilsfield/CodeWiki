# codewiki/src/be/index/symbol_table.py
"""SymbolTable: indexed collection of Symbols with fast lookups."""

from collections import defaultdict
from typing import Optional

from codewiki.src.be.index.models import Symbol, ExportStatus


class SymbolTable:
    """Holds all symbols and provides O(1) lookups by various keys."""

    def __init__(self, symbols: list[Symbol]):
        self._by_id: dict[str, Symbol] = {}
        self._by_file: dict[str, list[Symbol]] = defaultdict(list)
        self._by_qname: dict[str, Symbol] = {}
        self._by_name: dict[str, list[Symbol]] = defaultdict(list)

        for s in symbols:
            self._by_id[s.symbol_id] = s
            self._by_file[s.file_path].append(s)
            self._by_qname[s.qualified_name] = s
            self._by_name[s.name].append(s)

    def get(self, symbol_id: str) -> Optional[Symbol]:
        return self._by_id.get(symbol_id)

    def by_file(self, file_path: str) -> list[Symbol]:
        return self._by_file.get(file_path, [])

    def by_qualified_name(self, qname: str) -> Optional[Symbol]:
        return self._by_qname.get(qname)

    def children_of(self, symbol_id: str) -> list[Symbol]:
        parent = self._by_id.get(symbol_id)
        if not parent:
            return []
        return [self._by_id[cid] for cid in parent.children if cid in self._by_id]

    def by_name(self, name: str) -> list[Symbol]:
        """O(1) exact name lookup."""
        return list(self._by_name.get(name, []))

    def public_api(self) -> list[Symbol]:
        return [s for s in self._by_id.values() if s.export_status == ExportStatus.EXPORTED]

    def search(self, name: str) -> list[Symbol]:
        lower = name.lower()
        return [s for s in self._by_id.values() if lower in s.name.lower()]

    def all_symbols(self) -> list[Symbol]:
        return list(self._by_id.values())

    def all_files(self) -> set[str]:
        return set(self._by_file.keys())
