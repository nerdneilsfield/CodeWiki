# Index Layer v1 Code Review

**Date:** 2026-03-05
**Scope:** `codewiki/src/be/index/` and related tests
**Reviewer:** Claude Sonnet 4.6

---

## Summary

总体质量良好：模型设计清晰，适配器分工合理，测试覆盖完整。以下是发现的问题，按严重程度排序。

---

## 🔴 Critical Bugs

### 1. `index_builder.py` L158 — 所有 import edge 的 `from_symbol` 都指向文件里第一个符号

```python
# index_builder.py:152-158
from_syms = symbol_table.by_file(imp.file_path)
# ...
if from_syms:
    edges.append(SymbolEdge(
        ...
        from_symbol=from_syms[0].symbol_id,  # ← 始终取第一个符号！
        ...
    ))
```

**问题：** `from_syms[0]` 永远是文件里第一个符号（通常是第一个 class 或 function），与导入无关。一个有 10 个类的文件，所有 import edge 都会错误地归属到第一个类。

**应该如何修：** 导入是文件级的关系，要么创建文件级符号（`FILE` kind），要么让 `from_symbol` 留空/使用模块级标识符。最简单的修法是跳过这个绑定，只记录 `to_symbol`：

```python
edges.append(SymbolEdge(
    edge_type=EdgeType.IMPORTS,
    from_symbol=imp.file_path,   # 使用文件路径作为标识，或专门建 file-level symbol
    ...
))
```

或者更好：建一个 `SymbolKind.FILE` 的 symbol 代表整个文件，作为 from_symbol。

---

### 2. `index_builder.py` L54-58, L126-142 — `include_patterns`/`exclude_patterns` 被接受但从不使用

```python
# __init__ 中接收了参数：
def __init__(self, repo_path, include_patterns=None, exclude_patterns=None):
    self.include_patterns = include_patterns
    self.exclude_patterns = exclude_patterns

# _discover_files 中完全没用上：
def _discover_files(self):
    for root, dirs, files in os.walk(self.repo_path):
        ...  # include_patterns/exclude_patterns 从未引用
```

**问题：** 调用方（`DocumentationGenerator`）传入了 `self.config.include_patterns` 和 `self.config.exclude_patterns`，但索引器静默忽略它们，始终扫描整个仓库。这是功能缺失而非未来工作，因为调用接口已经暴露了这些参数。

**修法：** 在 `_discover_files` 中加入 fnmatch 过滤：

```python
import fnmatch

def _should_include(self, rel_path: str) -> bool:
    if self.include_patterns:
        if not any(fnmatch.fnmatch(rel_path, p) for p in self.include_patterns):
            return False
    if self.exclude_patterns:
        if any(fnmatch.fnmatch(rel_path, p) for p in self.exclude_patterns):
            return False
    return True
```

---

## 🟠 Medium Bugs

### 3. `python_adapter.py` L106-142 — `kwonlyargs` 丢失

```python
# _extract_signature 处理了 args.args、args.vararg、args.kwarg
# 但没有处理 args.kwonlyargs 和 args.kw_defaults

# 对于：def foo(a, *, b: int, c: str = "x"): ...
# 当前输出：foo(a) → 缺少 b 和 c
```

**修法：** 在 `*args` 处理后，加入 kwonly 参数的提取：

```python
# kwonly args (after *)
for i, arg in enumerate(args.kwonlyargs):
    p = arg.arg
    if arg.annotation:
        p += f": {ast.unparse(arg.annotation)}"
    kw_default = args.kw_defaults[i]
    if kw_default is not None:
        p += f" = {ast.unparse(kw_default)}"
    parts.append(p)
```

---

### 4. `component_card.py` L17 — `to_symbol` 和 `to_unresolved` 均为 None 时输出 `"calls: None"`

```python
key_edges = [
    f"{e.edge_type.value}: {e.to_symbol or e.to_unresolved}"  # → "calls: None"
    for e in outgoing[: self.max_edges]
]
```

**修法：**
```python
target = e.to_symbol or e.to_unresolved or "<unknown>"
key_edges = [f"{e.edge_type.value}: {target}" for e in outgoing[: self.max_edges]]
```

---

### 5. `index_builder.py` L5 — 未使用的 `field` 导入

```python
from dataclasses import dataclass, field   # field 从未使用
```

---

### 6. `ts_js_adapter.py` — `export default class/function` 可能不被识别

`export default class Foo {}` 在 tree-sitter TypeScript AST 中，`export_statement` 的子节点通常不是 `class_declaration` 而是 `class`（类型名不同）。当前 `_handle_export_statement` 只检查 `class_declaration` 和 `function_declaration`：

```python
def _handle_export_statement(self, node) -> None:
    for child in node.children:
        if child.type == "class_declaration":   # export default 时可能是 "class"
            ...
        if child.type == "function_declaration":  # export default 时可能是 "function_expression"
            ...
```

**建议：** 增加对 `"class"` 和 `"function_expression"` 类型的处理，或者用 `child.type in ("class_declaration", "class")` 方式匹配。

---

## 🟡 Design Issues

### 7. `models.py` — SymbolEdge 没有约束 `to_symbol`/`to_unresolved` 至少一个非 None

设计意图是两者至少一个有值，但模型不强制这一约束，可以创建两者都为 None 的 SymbolEdge：

```python
# 当前可以通过：
SymbolEdge(edge_type=EdgeType.CALLS, from_symbol="s1")  # to_symbol=None, to_unresolved=None
```

**建议：** 加 Pydantic validator：

```python
from pydantic import model_validator

class SymbolEdge(BaseModel):
    ...
    @model_validator(mode="after")
    def check_target(self) -> "SymbolEdge":
        if self.to_symbol is None and self.to_unresolved is None:
            raise ValueError("Either to_symbol or to_unresolved must be set")
        return self
```

---

### 8. `models.py` — `children: list[str] = []` 在 Pydantic v2 中安全但建议明确

Pydantic v2 对 `list[str] = []` 会自动处理（每实例独立），但建议用 `Field(default_factory=list)` 明确意图，与团队 Pydantic 代码风格统一：

```python
from pydantic import BaseModel, Field

children: list[str] = Field(default_factory=list)
```

同样适用于 `ImportStatement.imported_names`、`SymbolEdge.evidence_refs`、`ComponentCard.key_edges`。

---

### 9. `python_adapter.py` — 实例不可重复使用（双调用返回翻倍结果）

`self._symbols` 和 `self._imports` 在 `__init__` 中初始化一次，如果 `extract()` 被调用两次，第二次会在已有结果上继续追加：

```python
adapter = PythonIndexAdapter(...)
s1, i1 = adapter.extract()
s2, i2 = adapter.extract()  # s2 = s1 + s1 (doubled!)
```

**建议：** 在方法开头重置状态，或者文档注明单次使用，或者在 extract 开头加 guard：

```python
def extract(self):
    self._symbols = []
    self._imports = []
    ...
```

---

### 10. `index_builder.py` — `_analyze_with_existing` 调用私有方法 `_analyze_code_file`

```python
funcs, _ = analyzer._analyze_code_file(self.repo_path, file_info)  # 下划线前缀=私有
```

这是实现细节耦合，`CallGraphAnalyzer` 内部重构时会在毫无警告的情况下破坏 IndexBuilder。

**建议：** 使用公开 API，或者为 IndexBuilder 提供一个 `CallGraphAnalyzer` 方法的公开包装器。

---

### 11. `index_builder.py` L144-172 — `_build_edges` 对大型仓库有 O(n²) 隐患

对每个 import，对每个 `imported_name`，扫描目标文件的所有符号寻找名称匹配。中等规模仓库（数百文件，每文件数十符号）总复杂度为 O(imports × names × symbols_per_file)。

**建议：** 预建 `(file_path, name) → Symbol` 索引：

```python
name_index: dict[tuple[str, str], Symbol] = {
    (s.file_path, s.name): s for s in symbols
}
```

---

## 🟢 Minor / Style

### 12. `ts_js_adapter.py` — 全局变量用于语言单例，多线程下双重检查锁不完整

```python
_TS_LANGUAGE: "Language | None" = None
_TS_LANGUAGE_LOCK = threading.Lock()

def _get_ts_parser():
    global _TS_LANGUAGE
    if _TS_LANGUAGE is None:          # ← 外层检查没有锁
        with _TS_LANGUAGE_LOCK:
            if _TS_LANGUAGE is None:   # 内层检查有锁（正确）
```

外层 `if` 是优化（避免锁竞争），在 Python GIL 下是安全的，但在严格意义上不是线程安全的（比 Java 语义更宽松）。这里 Python GIL 让这个 pattern 实际上安全，但注释可以说明这一点。

---

### 13. `symbol_table.py` — `by_file` 返回内部列表引用

```python
def by_file(self, file_path: str) -> list[Symbol]:
    return self._by_file.get(file_path, [])
```

返回内部 `defaultdict` 里的真实列表引用，调用方 `list.append()` 会静默修改内部状态。建议返回副本：`return list(self._by_file.get(file_path, []))` 或标注文档"不要修改返回值"。

---

## 优先修复建议

| 优先级 | 问题 | 文件 |
|--------|------|------|
| 🔴 | `from_symbol` 始终取文件第一个符号 | `index_builder.py:158` |
| 🔴 | `include_patterns`/`exclude_patterns` 被忽略 | `index_builder.py:_discover_files` |
| 🟠 | `kwonlyargs` 签名丢失 | `python_adapter.py:_extract_signature` |
| 🟠 | `to_symbol/to_unresolved` 都为 None 时输出 `"None"` | `component_card.py:17` |
| 🟠 | 未使用的 `field` 导入 | `index_builder.py:5` |
| 🟡 | `SymbolEdge` 缺少 target 必填约束 | `models.py` |
| 🟡 | adapter 实例不可重用 | `python_adapter.py` |
| 🟡 | `_build_edges` O(n²) | `index_builder.py` |
