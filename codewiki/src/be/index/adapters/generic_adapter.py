# codewiki/src/be/index/adapters/generic_adapter.py
"""Generic adapter: converts existing Node objects to Symbol (1:1 fallback)."""
import hashlib

from codewiki.src.be.dependency_analyzer.models.core import Node
from codewiki.src.be.index.models import (
    Symbol, SymbolKind, Visibility, ExportStatus, SourceRange,
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
