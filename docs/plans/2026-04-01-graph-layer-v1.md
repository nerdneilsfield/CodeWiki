# Graph Layer v1 Implementation Plan

**Date:** 2026-04-01
**Scope:** CALLS edge extraction + EdgeIndex query API + minimal GraphStats
**Depends on:** Index Layer v1 (commit 973abd3, 162 tests)

## Scope Decision

**v1 (本次):** CALLS 边 + EdgeIndex + 最小 GraphStats，继续沿用 repo 级缓存
**v1.1 (后续):** Per-file incremental cache（缓存单位 = symbols + imports + call facts）
**v1.2 (后续):** Generic 语言 CALLS 深化、TS/JS binding 增强

理由：per-file 增量缓存与当前 IndexBuilder 的构建链路改造量大，和 CALLS 同时做会从
"加一层图"变成"重写构建链路"。先把数据模型和查询语义做对，再优化性能。

---

## 数据契约（硬规则）

### CALLS 边必须遵守

1. **保留 unresolved calls** — 解析失败的调用不丢弃，写入 `to_unresolved`，`confidence=LOW`
2. **每条边必须带 `evidence_refs`** — 指向调用发生的源码位置（file + line）
3. **每条边必须带 `confidence`** — HIGH=resolved, MEDIUM=heuristic, LOW=unresolved
4. **`resolver` 字段标明来源** — "ast"=Python, "treesitter"=TS/JS, "call_graph_analyzer"=generic

### from_symbol 锚定规则（最小可执行符号优先）

| 能定位到 | from_symbol | confidence 影响 |
|---------|-------------|----------------|
| 当前函数/方法 | 该 function/method 的 symbol_id | 不降级 |
| 当前类体（不确定具体方法） | 该 class 的 symbol_id | confidence=LOW |
| 只知道文件 | `file:<rel_path>` | confidence=LOW |

原始边保留函数/方法级粒度。v1 仅提供精确 symbol 查询，类/文件上卷视图留待后续版本。

---

## Phase 1: Python CALLS 提取 + 两阶段构建

### 改造 IndexBuilder.build() 为两阶段

**当前（单阶段）：** 遍历文件 → 提取 symbols+imports → 建 SymbolTable → 建 edges

**目标（两阶段）：**
- Pass 1: 遍历文件 → 提取 symbols+imports → 保存 adapter 引用
- Pass 2: 建好 SymbolTable+ImportGraph 后 → 用保存的 adapter 调 extract_calls()

```python
# IndexBuilder.build() 结构
def build(self):
    # Pass 1: symbols + imports + collect generic CallRelationships
    adapters_by_file = {}
    all_call_rels = []  # CallRelationship from generic adapter path
    for file_path, lang in self._discover_files():
        adapter = ...  # create and run adapter
        symbols, imports = adapter.extract()
        adapters_by_file[file_path] = (adapter, lang)
        all_symbols.extend(symbols)
        all_imports.extend(imports)
        # generic fallback also collects relationships
        # (see _generic_fallback changes in Phase 2)

    symbol_table = SymbolTable(all_symbols)
    import_graph = ImportGraph(all_imports)

    # Pass 2: calls (needs cross-file resolution)
    edges = self._build_edges(all_imports, symbol_table)
    edges.extend(self._build_extends_edges(symbol_table))

    # 2a: language-specific adapters with extract_calls()
    for file_path, (adapter, lang) in adapters_by_file.items():
        if hasattr(adapter, 'extract_calls'):
            edges.extend(adapter.extract_calls(symbol_table, import_graph))

    # 2b: generic adapter converts collected CallRelationships
    if all_call_rels:
        from .adapters.generic_adapter import GenericIndexAdapter
        edges.extend(GenericIndexAdapter.convert_calls(all_call_rels, symbol_table))
    ...
```

### PythonIndexAdapter.extract_calls()

**签名:** `extract_calls(self, symbol_table: SymbolTable, import_graph: ImportGraph) -> list[SymbolEdge]`

**实现逻辑:**
1. 复用 `self._tree`（在 extract() 时保存 AST tree）
2. 遍历每个 FunctionDef/AsyncFunctionDef 体内的 ast.Call 节点
3. 提取 callee name（处理 simple name, attribute access, self.method）
4. 过滤 Python builtins（print, len, isinstance, type, range, ...）
5. 解析顺序：同文件 symbols → import_graph.resolve() → 标为 unresolved
6. 生成 SymbolEdge(edge_type=CALLS, from_symbol=当前函数 symbol_id, ...)

**Builtins 过滤集:**
```python
_PYTHON_BUILTINS = frozenset({
    "print", "len", "range", "type", "isinstance", "issubclass",
    "int", "str", "float", "bool", "list", "dict", "set", "tuple",
    "bytes", "bytearray", "memoryview", "object", "super",
    "getattr", "setattr", "hasattr", "delattr", "property",
    "staticmethod", "classmethod", "abs", "max", "min", "sum",
    "sorted", "reversed", "enumerate", "zip", "map", "filter",
    "any", "all", "id", "hash", "repr", "format", "open",
    "input", "round", "pow", "divmod", "chr", "ord", "hex", "oct", "bin",
    "iter", "next", "callable", "vars", "dir",
    "ValueError", "TypeError", "KeyError", "IndexError", "AttributeError",
    "RuntimeError", "StopIteration", "Exception", "NotImplementedError",
})
```

### 测试验收标准 (test_index_calls.py)

- `test_py_calls_same_file_function` — foo() calls bar() in same file → CALLS edge, HIGH confidence
- `test_py_calls_imported_function` — calls imported symbol → resolved CALLS edge
- `test_py_calls_self_method` — self.helper() → from_symbol=当前 method, to_symbol=helper method
- `test_py_calls_unresolved_external` — os.path.join() → to_unresolved="os.path.join", LOW confidence
- `test_py_calls_builtins_filtered` — print(), len() 不生成 CALLS 边
- `test_py_calls_evidence_ref_correct` — evidence_refs 指向 call site 行号
- `test_py_calls_nested_calls` — foo(bar()) 生成两条边
- `test_py_calls_class_instantiation` — MyClass() → CALLS edge to class symbol

---

## Phase 2: TS/JS CALLS 提取（启发式 v1）

### TSJSIndexAdapter.extract_calls()

**范围限定（v1 启发式）：**
- `call_expression` 节点 → 提取 callee
- `this.method()` / `super.method()` → 解析为当前类的 method
- 明确 import 的函数调用 → 通过 import_graph 解析
- 其余 → to_unresolved, confidence=LOW

**不做（留 v1.2）：**
- 复杂 binding（回调、高阶函数、动态调用）
- template literal 里的调用
- decorator 调用

### Generic CALLS 接入

**当前问题：** `_analyze_with_existing()` 只取 `funcs`，丢掉了 `relationships`:
```python
# 当前代码 (index_builder.py:189)
funcs, _ = analyzer._analyze_code_file(self.repo_path, file_info)
return list(funcs.values())
```

**修改方案：**
```python
# _analyze_with_existing() 改为返回 (nodes, relationships)
def _analyze_with_existing(self, abs_path, content, lang) -> tuple[list, list]:
    ...
    funcs, relationships = analyzer._analyze_code_file(self.repo_path, file_info)
    return list(funcs.values()), relationships

# _generic_fallback() 同步改造
def _generic_fallback(self, abs_path, content, lang, all_symbols, all_call_rels):
    nodes, relationships = self._analyze_with_existing(abs_path, content, lang)
    if nodes:
        adapter = GenericIndexAdapter(lang=lang)
        all_symbols.extend(adapter.convert(nodes))
        all_call_rels.extend(relationships)
```

**GenericIndexAdapter.convert_calls() 新方法:**
```python
def convert_calls(self, relationships: list[CallRelationship],
                  symbol_table: SymbolTable) -> list[SymbolEdge]:
```

**caller/callee → symbol_id 映射:**
- CallRelationship.caller 格式类似 `module.ClassName.method_name`
- 通过 `symbol_table.by_qualified_name()` 查找
- 查不到时降级为 `symbol_table.search()` 按 name 匹配
- 仍找不到时写 to_unresolved

### 测试验收标准

TS/JS:
- `test_ts_calls_function_call` — 顶层函数互调
- `test_ts_calls_this_method` — this.method() 解析
- `test_ts_calls_imported_symbol` — 导入函数调用
- `test_ts_calls_unresolved` — 未导入名字 → unresolved

Generic:
- `test_generic_calls_resolved` — is_resolved=True → HIGH confidence
- `test_generic_calls_unresolved` — is_resolved=False → LOW confidence + to_unresolved
- `test_generic_calls_evidence_ref` — call_line 正确映射到 evidence_refs

---

## Phase 3: EdgeIndex 查询 API

### 新文件: `codewiki/src/be/index/edge_index.py`

```python
class EdgeIndex:
    def __init__(self, edges: list[SymbolEdge]):
        self._by_from: dict[str, list[SymbolEdge]] = defaultdict(list)
        self._by_to: dict[str, list[SymbolEdge]] = defaultdict(list)
        # Only index resolved to_symbol for reverse lookup (callers_of).
        # Unresolved edges (to_unresolved set, to_symbol=None) are only
        # reachable via forward lookup (callees_of) through _by_from.
        for e in edges:
            self._by_from[e.from_symbol].append(e)
            if e.to_symbol:
                self._by_to[e.to_symbol].append(e)

    def callers_of(self, symbol_id: str) -> list[SymbolEdge]: ...
    def callees_of(self, symbol_id: str) -> list[SymbolEdge]: ...
    def edges_of(self, symbol_id: str, edge_type: EdgeType | None = None) -> list[SymbolEdge]: ...
    def dependency_subgraph(self, symbol_ids: set[str]) -> list[SymbolEdge]: ...
```

### 接入 IndexProducts（自动派生字段）

IndexProducts 是 dataclass，改用 `__post_init__` 自动重建 EdgeIndex:

```python
@dataclass
class IndexProducts:
    symbol_table: SymbolTable
    import_graph: ImportGraph
    edges: list[SymbolEdge]
    cards: list[ComponentCard]
    edge_index: EdgeIndex = field(init=False)

    def __post_init__(self):
        self.edge_index = EdgeIndex(self.edges)
```

这样 build()、from_dict()、测试手工构造都会自动有 edge_index。
to_dict() 不序列化 edge_index（它从 edges 派生）。

### 测试验收标准

- `test_callers_of` — B 被 A 和 C 调用
- `test_callees_of` — A 调用 B 和 C
- `test_edges_of_all_types` — IMPORTS + CALLS + EXTENDS 混合
- `test_edges_of_filtered` — 只取 CALLS
- `test_dependency_subgraph` — 只保留两端都在给定集合内的边
- `test_empty_edge_index` — 全返空列表
- `test_cache_hit_has_edge_index` — from_dict() 后 edge_index 可用

---

## Phase 4: 最小 GraphStats

### 新文件: `codewiki/src/be/index/graph_stats.py`

```python
class GraphStats(BaseModel):
    edge_counts: dict[str, int]          # EdgeType.value → count
    unresolved_counts: dict[str, int]     # EdgeType.value → unresolved count
    unresolved_ratios: dict[str, float]   # EdgeType.value → ratio (0.0-1.0)
    total_symbols: int
    total_edges: int

    @classmethod
    def compute(cls, symbols: list[Symbol], edges: list[SymbolEdge]) -> "GraphStats": ...
```

不做 per-file density（过重，留 v1.1）。

### 接入 IndexProducts

- 加 `stats: GraphStats | None = None` 字段
- build() 时 compute
- to_dict/from_dict 序列化

### 测试验收标准

- `test_edge_counts_by_type` — 已知边 → 按类型计数正确
- `test_unresolved_ratio` — 3/10 unresolved → ratio=0.3
- `test_empty_stats` — 0 symbols, 0 edges → 全零，无除零错误

---

## 缓存策略

**v1: 继续沿用 repo 级缓存**
- INDEX_VERSION 从 "1" 升到 "2"（因为 IndexProducts 结构变了）
- Cache key 不变: (commit_hash, INDEX_VERSION)
- 缓存命中后 __post_init__ 自动重建 EdgeIndex

**v1.1 (后续): Per-file incremental cache**
- 缓存单位: symbols + imports + call_facts per file
- file hash → 跳过未变文件的 AST 解析 + call 提取
- edges/cards/stats 始终全量重算

---

## 实施顺序

| 顺序 | 内容 | 产出 |
|------|------|------|
| 1 | Phase 1: Python CALLS + 两阶段构建 | CALLS 边端到端可用 |
| 2 | Phase 3: EdgeIndex | 图可查询 |
| 3 | Phase 2: TS/JS + Generic CALLS | 多语言覆盖 |
| 4 | Phase 4: GraphStats | 统计信息 |
| 5 | 全量测试 + commit | 交付 |
