# Generation v2 Implementation Plan

**Date:** 2026-04-01
**Scope:** 证据驱动上下文注入 + 全局术语表 + 链接规范
**Depends on:** Clustering v2 (commit 797d687, 365 tests)
**Spec:** docs/v3.md sections 3.4, 4.4, 6.1-6.4, 7

---

## 与 v3.md 的对齐映射

| v3.md 规格 | 位置 | 本 plan 对应 |
|-----------|------|------------|
| 两阶段写作（概览→细节） | 3.4 L189-190 | 现有 pipeline 已是 leaf→parent，保持不变 |
| 证据驱动写作协议 | 4.4 L377-381, 6.1 L607-619 | Phase 1: system prompt + evidence rules |
| RETRIEVE_CONTEXT 伪代码 | 6.4 L655-678 | Phase 2: context_pack.py |
| 全局术语表 | 3.4 L193, 4.4 L388 | Phase 3: glossary generator |
| 链接规范 | 3.4 L194, 4.4 L388 | Phase 3: link_map builder |
| 重复控制 | 3.4 L195, 4.4 L388 | Phase 3: dedup rules in prompt |
| 缓存键含 prompt_version + model_version | 6.3 L639-648 | 后置 — 继续沿用现有缓存 |
| 递归拆分从"LLM自由分裂"改为"图边界" | 6.2 L627-631 | 后置 — Clustering v2 已处理拆分 |

### Scope cut（有意收缩）

- v3.md L382-385 的递归拆分改造已在 Clustering v2 中部分落地（图算法定结构），本次不再改拆分逻辑
- v3.md L403 的 embedding 近重复检测留待后续
- v3.md L639-648 的多维缓存键留待后续
- v3.md L398 的一致性 Pass（只改链接/术语/重复）留待 Post Layer v2

---

## 改造策略（最小侵入）

现有 pipeline 已经可以工作。本次改造的核心是在 **不改变调用链结构** 的前提下：

1. 把 IndexProducts 穿透到 prompt 构建层
2. 在 prompt 中注入 symbol cards + boundary edges + evidence refs
3. 在 system prompt 中添加证据驱动写作规则
4. 生成全局术语表 + 链接 map 作为共享只读上下文

### 改动范围

| 文件 | 改动量 | 内容 |
|------|--------|------|
| `documentation_generator.py` | ~10 行 | 传 index_products 到 orchestrator + 生成 glossary |
| `agent_orchestrator.py` | ~5 行 | 接收并存储 index_products，传入 deps |
| `agent_tools/deps.py` | ~5 行 | CodeWikiDeps 加 index_products + global_assets 字段 |
| `prompt_template.py` | ~80 行 | 注入 symbol cards + boundary edges + evidence rules |
| **新** `context_pack.py` | ~120 行 | RETRIEVE_CONTEXT 实现 |
| **新** `glossary.py` | ~80 行 | 全局术语表 + 链接 map 生成 |

---

## Phase 1: 证据驱动规则注入到所有 Prompt 通路（对齐 v3.md 6.1 L607-619）

**文件:** `codewiki/src/be/prompt_template.py`

### 三条独立 Prompt 通路（必须全部覆盖）

| 通路 | 调用点 | 影响范围 |
|------|--------|---------|
| `format_system_prompt()` | agent_orchestrator.py:120 | 非叶子 agent |
| `format_leaf_system_prompt()` | agent_orchestrator.py:128, generate_sub_module_documentations.py:180 | 叶子/子模块 agent |
| `format_overview_prompt()` | documentation_generator.py:658, prompt_template.py:1288 | overview / parent docs |

**改造方式：** 提取共享 evidence rules block，在三个 format 函数中统一注入。

```python
EVIDENCE_RULES_BLOCK = """
## Evidence-Driven Writing Rules

1. Every behavioral assertion MUST cite evidence:
   - Reference symbol_id for definitions
   - Reference file:line for call sites / imports
2. If evidence is insufficient, write "Based on index analysis..." and note
   the limitation explicitly
3. Example code MUST come from the repository (tests, README, examples);
   if synthesized, mark as "[Synthetic example]"
4. Do NOT invent function signatures, parameter types, or call chains
   not supported by the provided symbol cards and edges
"""

# Inject into all three:
# - format_system_prompt(): append to system prompt string
# - format_leaf_system_prompt(): append to leaf system prompt string
# - format_overview_prompt(): prepend to overview instruction block
```

### 测试
- `test_system_prompt_contains_evidence_rules` — verify in format_system_prompt()
- `test_leaf_system_prompt_contains_evidence_rules` — verify in format_leaf_system_prompt()
- `test_overview_prompt_contains_evidence_rules` — verify in format_overview_prompt()

---

## Phase 2: Context Pack Builder（对齐 v3.md 6.4 L655-678）

**新文件:** `codewiki/src/be/generation/context_pack.py`

```python
def build_context_pack(
    module_components: list[str],  # component_ids in this module
    components: dict[str, Node],   # all components
    index_products: IndexProducts | None,
    glossary: dict[str, str] | None = None,  # term → definition
    link_map: dict[str, str] | None = None,  # module_path → doc_path
) -> dict:
    """Build evidence-rich context for LLM prompt (v3.md 6.4).

    Returns dict with:
    - symbol_cards: list of formatted symbol summaries
    - boundary_edges: list of cross-module relationship descriptions
    - internal_edges: list of intra-module relationship descriptions
    - evidence_snippets: list of code location references
    - glossary_context: formatted glossary excerpt
    - link_map_context: formatted link map excerpt
    """
```

**逻辑：**
1. 从 IndexProducts.cards 提取本模块 symbol cards（按 component → symbol 映射）
2. 从 EdgeIndex 提取 boundary edges（一端在模块内，一端在模块外）和 internal edges
3. 从 evidence_refs 提取代码位置引用
4. 格式化 glossary 和 link_map 的相关条目

### 注入到 prompt_template.py（所有三条通路）

提取共享注入函数，被三个 format 函数调用：

```python
def format_context_pack_section(context_pack: dict | None) -> str:
    """Format context pack into prompt sections. Used by all three prompt paths."""
    if not context_pack:
        return ""
    sections = []
    if context_pack.get("symbol_cards"):
        sections.append("<SYMBOL_CARDS>\nStatic analysis summaries:\n"
                       + "\n".join(f"- {c}" for c in context_pack["symbol_cards"])
                       + "\n</SYMBOL_CARDS>")
    if context_pack.get("boundary_edges"):
        sections.append("<BOUNDARY_EDGES>\nExternal dependencies:\n"
                       + "\n".join(f"- {e}" for e in context_pack["boundary_edges"])
                       + "\n</BOUNDARY_EDGES>")
    if context_pack.get("glossary_context"):
        sections.append("<GLOSSARY>\n" + context_pack["glossary_context"] + "</GLOSSARY>")
    if context_pack.get("link_map_context"):
        sections.append("<LINK_MAP>\n" + context_pack["link_map_context"] + "</LINK_MAP>")
    return "\n\n".join(sections)
```

注入点：
- `format_user_prompt()` — 末尾追加（叶子 agent）
- `format_overview_prompt()` — 末尾追加（parent/overview）
- `format_leaf_system_prompt()` — 不注入数据段，只注入 EVIDENCE_RULES_BLOCK

### 测试
- `test_build_context_pack_with_index` — mock IndexProducts → 返回 symbol_cards
- `test_build_context_pack_without_index` — None → 返回空 dict
- `test_build_context_pack_boundary_edges` — 模块内外边正确分类
- `test_context_pack_injected_in_prompt` — verify `<SYMBOL_CARDS>` appears in formatted prompt
- `test_context_pack_evidence_refs` — evidence file:line 格式正确

---

## Phase 3: 全局术语表 + 链接 Map（对齐 v3.md 3.4 L193-195）

**新文件:** `codewiki/src/be/generation/glossary.py`

### 术语表生成

```python
def build_glossary(
    index_products: IndexProducts,
    components: dict[str, Node],
) -> dict[str, str]:
    """Build global glossary from public API symbols.

    Terms: class names, key function names, module names
    Definitions: from docstrings (first sentence) or signature

    Returns: {term: definition}
    """
```

逻辑：
1. 从 SymbolTable.public_api() 提取所有 EXPORTED symbols
2. 从 docstring 第一句生成定义
3. 按字母序排序
4. 格式：`"AuthHandler": "Main authentication handler (class, src/auth/handler.py)"`

### 链接 Map 生成

```python
def build_link_map(
    module_tree: dict,  # v1 legacy format
) -> dict[str, str]:
    """Build stable link map for cross-module references.

    Keys are module path (stable, from tree["path"]), not title (unstable).
    Values are actual document filenames generated by module_doc_filename()
    from codewiki/src/utils.py — this function converts "/" to "_" and
    uses "-" for hierarchy, so "mod/auth" becomes "mod_auth.md".

    MUST call module_doc_filename() for values, not invent path→.md rules.

    Returns: {"mod/auth": "mod_auth.md", ...}
    """
```

逻辑：从 module_tree 递归遍历，构建 path → document_filename 映射。
**键是 path（稳定），值通过 `module_doc_filename()` 生成（对齐真实文件名规则）。**
不手动拼 `.md` 后缀，复用 `codewiki/src/utils.py:64` 的现有函数。

### 注入到 DocumentationGenerator

在 `run()` 中，clustering 完成后、doc generation 之前：

```python
# After module_tree is built
glossary = build_glossary(self.index_products, components)
link_map = build_link_map(module_tree)
# Store for injection into all prompts
self.global_assets = {"glossary": glossary, "link_map": link_map}
```

### 测试
- `test_build_glossary_from_public_api` — EXPORTED symbols → glossary entries
- `test_build_glossary_empty_index` — None index → empty dict
- `test_build_link_map` — module_tree → path mapping
- `test_glossary_injected_in_prompt` — verify `<GLOSSARY>` in prompt

---

## Phase 4: 穿透 IndexProducts 到 Prompt 层

### 注入时机问题

AgentOrchestrator 在 DocumentationGenerator.__init__() 中创建（L57），
但 index_products 和 glossary/link_map 要到 run() 中聚类之后才有。
**不改构造时机，用后注入接口：**

```python
# AgentOrchestrator 新增 setter（不改 __init__ 签名）
class AgentOrchestrator:
    def set_generation_context(self, index_products, global_assets):
        """Late injection after index build + clustering completes."""
        self.index_products = index_products
        self.global_assets = global_assets
```

```python
# DocumentationGenerator.run() 中，clustering 之后、doc generation 之前
self.agent_orchestrator.set_generation_context(
    index_products=self.index_products,
    global_assets={"glossary": glossary, "link_map": link_map},
)
```

### 改动链路

```
DocumentationGenerator.run()
  ├─ build IndexProducts → self.index_products
  ├─ cluster modules
  ├─ build_glossary() + build_link_map()
  ├─ orchestrator.set_generation_context(index_products, global_assets)  # 后注入
  │
  ├─ AgentOrchestrator.process_module()
  │   ├─ context_pack = build_context_pack(module_components, components,
  │   │                                     self.index_products, glossary, link_map)
  │   ├─ format_user_prompt(..., context_pack=context_pack)
  │   └─ CodeWikiDeps(..., index_products=self.index_products,
  │                       global_assets=self.global_assets)  # 递归子模块也能拿到
  │
  ├─ format_user_prompt()
  │   └─ appends <SYMBOL_CARDS>, <BOUNDARY_EDGES>, <GLOSSARY> sections
  │
  └─ format_overview_prompt()
      └─ appends <BOUNDARY_EDGES>, <GLOSSARY> sections (parent/overview 通路)
```

### 递归子模块通路

`generate_sub_module_documentations.py:202` 直接用 `ctx.deps` 调 `format_user_prompt()`。
因此 **CodeWikiDeps 必须同时携带 index_products 和 global_assets**，否则顶层模块
有上下文但递归子模块没有。

```python
# agent_tools/deps.py
@dataclass
class CodeWikiDeps:
    # ... existing fields ...
    index_products: Any = None       # IndexProducts or None
    global_assets: dict | None = None  # {"glossary": dict, "link_map": dict}
```

`generate_sub_module_documentations.py` 的 `format_user_prompt()` 调用需要：
1. 从 `ctx.deps.index_products` 和 `ctx.deps.global_assets` 构建 context_pack
2. 传入 `format_user_prompt(..., context_pack=context_pack)`

### 测试
- `test_set_generation_context_stores_values` — setter 正确存储
- `test_process_module_uses_context_pack` — mock 验证 context_pack 传入 prompt
- `test_overview_prompt_gets_boundary_edges` — overview 通路也注入了证据上下文
- `test_sub_module_generation_gets_global_assets` — 递归子模块 deps 包含 global_assets

---

## Phase 5: 集成 + 全量测试

### 新文件
- `codewiki/src/be/generation/__init__.py`
- `codewiki/src/be/generation/context_pack.py`
- `codewiki/src/be/generation/glossary.py`

### 新测试
- `tests/test_generation_context_pack.py`
- `tests/test_generation_glossary.py`

### 回归
- 全量 `tests/test_clustering_*.py` + `tests/test_index_*.py`
- 确认无 IndexProducts 时降级正常

---

## 实施顺序

| 顺序 | Phase | 产出 |
|------|-------|------|
| 1 | Phase 2: context_pack.py | 上下文构建器（独立可测） |
| 2 | Phase 3: glossary.py | 术语表 + 链接 map（独立可测） |
| 3 | Phase 1: system prompt | 证据驱动规则注入 |
| 4 | Phase 4: 穿透 + prompt 注入 | 全链路接通 |
| 5 | Phase 5: 全量测试 + commit | 交付 |
