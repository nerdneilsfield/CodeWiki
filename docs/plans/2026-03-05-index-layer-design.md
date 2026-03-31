# Index Layer v1 Design

**Date:** 2026-03-05
**Scope:** Build offline index layer (Symbol Table + Import Graph + Component Cards) for CodeWiki v3
**Priority languages:** Python, TypeScript/JavaScript (others get 1:1 Node→Symbol fallback)

## Core Problem

The current pipeline feeds raw AST output directly into LLM prompts with no intermediate "index" layer. This causes:
- Methods inside classes are invisible to the dependency graph
- No import graph (cross-file call resolution is name-based and lossy)
- No evidence refs (can't trace assertions back to source locations)
- No symbol visibility/export tracking
- LLM gets full source code instead of focused summaries

## Data Models (`codewiki/src/be/index/models.py`)

All `file_path` fields are **relative to repo root** (never absolute).

### Symbol
```python
class Symbol(BaseModel):
    symbol_id: str              # "py:src/auth/login.py#LoginService(class)"
    lang: str                   # "python", "typescript", "javascript", ...
    kind: SymbolKind            # class, function, method, interface, type, variable, constant
    name: str                   # "LoginService"
    qualified_name: str         # "src.auth.login.LoginService"
    file_path: str              # relative: "src/auth/login.py"
    range: SourceRange          # {start_line, start_col, end_line, end_col}
    signature: str | None       # "login(username: str, password: str) -> bool"
    visibility: Visibility      # public, protected, private, internal, unknown
    export_status: ExportStatus # exported, not_exported, unknown
    docstring: str | None
    parent_symbol_id: str|None  # class symbol_id for methods
    children: list[str]         # child symbol_ids (methods of a class)
    source_hash: str            # hash of definition source for cache invalidation
```

### ImportStatement
```python
class ImportStatement(BaseModel):
    file_path: str              # relative: "src/auth/login.py"
    module_path: str            # "os.path" or "./utils" (as written in source)
    resolved_path: str | None   # resolved relative path: "src/utils.py"
    imported_names: list[str]   # ["join", "dirname"] or ["*"] or []
    alias: str | None           # import X as Y → "Y"
    is_reexport: bool
    line: int
```

### SymbolEdge
```python
class SymbolEdge(BaseModel):
    edge_type: EdgeType         # imports, calls, extends, implements, references, tests
    from_symbol: str            # symbol_id
    to_symbol: str | None       # resolved symbol_id
    to_unresolved: str | None   # raw name if unresolved (never discarded)
    evidence_refs: list[SourceRange]
    confidence: Confidence      # high, medium, low
    resolver: str               # "ast", "treesitter", "heuristic"
```

### ComponentCard
```python
class ComponentCard(BaseModel):
    symbol_id: str
    signature: str
    docstring_summary: str      # first 2 sentences
    kind: SymbolKind
    key_edges: list[str]        # top N dependency descriptions
    file_context: str           # "src/auth/login.py (lines 10-42)"
```

## Package Structure

```
codewiki/src/be/index/
├── __init__.py
├── models.py              # All model classes above
├── symbol_table.py        # SymbolTable: lookup by id/file/qualified_name/children
├── import_graph.py        # ImportGraph: file-level import edges, module resolution
├── component_card.py      # CardBuilder: Symbol → ComponentCard
├── index_builder.py       # IndexBuilder: orchestrator
└── adapters/
    ├── __init__.py
    ├── python_adapter.py  # Enhanced Python extraction (methods + imports + visibility)
    ├── ts_js_adapter.py   # Enhanced TS/JS extraction (methods + imports + exports)
    └── generic_adapter.py # Node → Symbol 1:1 fallback for other languages
```

## Integration with Existing Pipeline

```
DependencyParser.parse_repository()
  → CallGraphAnalyzer
    → PythonASTAnalyzer ──→ python_adapter → Symbols + Imports
    → TreeSitterTS/JS ────→ ts_js_adapter  → Symbols + Imports
    → other analyzers ────→ generic_adapter → Symbols (no imports)

IndexBuilder.build(repo_path, config) → IndexProducts
  1. Run adapters on analyzer output
  2. Build SymbolTable from all Symbols
  3. Build ImportGraph from all ImportStatements
  4. Build SymbolEdges from relationships + ImportGraph
  5. Generate ComponentCards
  Return IndexProducts(symbol_table, import_graph, edges, cards)
```

Downstream consumers (DocumentationGenerator, cluster_modules) can use IndexProducts
where available, falling back to existing Node-based flow for unchanged code paths.

## Enhanced Analyzer Behaviors

### Python Adapter
- **Method extraction**: visit FunctionDef/AsyncFunctionDef inside ClassDef → child Symbol
- **Import extraction**: visit_Import / visit_ImportFrom → ImportStatement
- **Visibility**: `_name` → private, `__all__` → exported, else public
- **Signatures**: extract type annotations from ast.arg and returns

### TS/JS Adapter
- **Method extraction**: walk class body tree-sitter nodes → child Symbol
- **Import extraction**: import_statement / import_clause tree-sitter nodes → ImportStatement
- **Export detection**: `export` keyword → exported, else not_exported
- **Visibility**: public/private/protected keywords in class methods

### Generic Adapter (all other languages)
- Node.id → symbol_id, Node.relative_path → file_path
- kind/visibility/export_status = unknown, no imports, no method children

## LLM JSON Parsing

Use `json_repair` library (https://github.com/mangiucugna/json_repair) for all LLM JSON
output parsing. Replace `json.loads()` with `json_repair.loads()` in the index layer.
Existing files (cluster_modules.py, docs_fixer.py, etc.) can be migrated in a follow-up.

## Caching

- IndexProducts serializable to JSON
- Cache key: `(repo_commit_hash, index_version_constant)`
- Stored as `{output_dir}/_index_cache.json`
- Cache hit → skip all index building

## SymbolTable Key Operations

- `get(symbol_id)` → Symbol
- `by_file(file_path)` → list[Symbol]
- `by_qualified_name(qname)` → Symbol | None
- `children_of(symbol_id)` → list[Symbol]
- `public_api()` → list[Symbol]
- `search(name)` → list[Symbol] (fuzzy)

## ImportGraph Key Operations

- `imports_of(file_path)` → list[ImportStatement]
- `importers_of(file_path)` → list[str]
- `resolve(file_path, imported_name)` → Symbol | None
- `file_dependency_graph()` → Dict[str, Set[str]]

## Package Registration

`pyproject.toml` uses auto-discovery (`[tool.setuptools.packages.find]` with
`include = ["codewiki*"]`), so new packages are automatically found as long as they
contain `__init__.py`. No manual package registration needed.

## Decisions & Trade-offs

1. **All paths relative to repo root** — portable across machines
2. **Adapters re-parse files** — cleaner than modifying existing analyzers in-place
3. **Unresolved refs preserved** — SymbolEdge.to_unresolved instead of discarding
4. **json_repair for LLM output** — handles malformed JSON from models
5. **Method-level granularity** for Python/TS/JS only — other languages stay class/function level
6. **No vector DB in this phase** — structural index only, embedding search later
7. **Use `self.config.include_patterns` / `self.config.exclude_patterns` properties** — these safely handle `agent_instructions` being `None` (do NOT call `.get()` on `agent_instructions` directly)
