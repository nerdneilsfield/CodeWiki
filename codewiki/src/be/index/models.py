"""Data models for the index layer.

All file_path fields are relative to the repository root — never absolute.
This ensures index products are portable across machines.
"""
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class SymbolKind(str, Enum):
    PACKAGE = "package"
    MODULE = "module"
    FILE = "file"
    CLASS = "class"
    INTERFACE = "interface"
    FUNCTION = "function"
    METHOD = "method"
    TYPE = "type"
    VARIABLE = "variable"
    CONSTANT = "constant"
    ENUM = "enum"
    STRUCT = "struct"
    TRAIT = "trait"


class Visibility(str, Enum):
    PUBLIC = "public"
    PROTECTED = "protected"
    PRIVATE = "private"
    INTERNAL = "internal"
    UNKNOWN = "unknown"


class ExportStatus(str, Enum):
    EXPORTED = "exported"
    NOT_EXPORTED = "not_exported"
    UNKNOWN = "unknown"


class EdgeType(str, Enum):
    IMPORTS = "imports"
    CALLS = "calls"
    EXTENDS = "extends"
    IMPLEMENTS = "implements"
    REFERENCES = "references"
    TESTS = "tests"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SourceRange(BaseModel):
    """A span in a source file. file_path is relative to repo root."""
    file_path: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int


class Symbol(BaseModel):
    """A code symbol with full metadata and evidence position."""
    symbol_id: str
    lang: str
    kind: SymbolKind
    name: str
    qualified_name: str
    file_path: str
    range: SourceRange
    signature: Optional[str] = None
    visibility: Visibility = Visibility.UNKNOWN
    export_status: ExportStatus = ExportStatus.UNKNOWN
    docstring: Optional[str] = None
    parent_symbol_id: Optional[str] = None
    children: list[str] = []
    source_hash: str


class ImportStatement(BaseModel):
    """A single import/require/use statement in a file."""
    file_path: str
    module_path: str
    imported_names: list[str] = []
    alias: Optional[str] = None
    resolved_path: Optional[str] = None
    is_reexport: bool = False
    line: int


class SymbolEdge(BaseModel):
    """A directed relationship between symbols, with evidence."""
    edge_type: EdgeType
    from_symbol: str
    to_symbol: Optional[str] = None
    to_unresolved: Optional[str] = None
    evidence_refs: list[SourceRange] = []
    confidence: Confidence = Confidence.MEDIUM
    resolver: str = "unknown"


class ComponentCard(BaseModel):
    """Lightweight symbol summary for LLM context packs."""
    symbol_id: str
    signature: str
    docstring_summary: str
    kind: SymbolKind
    key_edges: list[str] = []
    file_context: str
