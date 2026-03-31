# Post Layer v2 Implementation Plan

**Date:** 2026-04-01
**Scope:** Link 校验 + 降级策略 + Lint 报告 + 门禁
**Depends on:** Generation v2 (commit ed25d29, 405 tests)
**Spec:** docs/v3.md sections 3.5, 4.5

---

## 与 v3.md 的对齐映射

| v3.md 规格 | 位置 | 本 plan 对应 |
|-----------|------|------------|
| Mermaid 失败则降级为代码块 | 3.5 L204 | Phase 1: fallback degradation |
| Math 失败则降级 | 3.5 L207 | Phase 1: fallback degradation |
| 本地链接+锚点校验 | 4.5 L424-425 | Phase 2: link validator |
| Heading anchor 提取+一致性检查 | 4.5 L424 | Phase 2: anchor registry |
| 回归报告：坏图/坏公式/断链列表 | 4.5 L438 | Phase 3: lint report |
| 新增失败阻断 | 4.5 L439 | Phase 3: CI gate (configurable) |
| 外链检查 (lychee) | 4.5 L435 | 后置 — 需要外部工具 |
| Mermaid SVG 预编译 | 4.5 L428 | 后置 — 依赖 mmdc 可用性 |
| KaTeX 全渲染 | 4.5 L431 | 后置 — 依赖 Node.js |

### Scope cut

- 外链检查 (lychee) 需要额外安装，留待 CI 集成
- Mermaid SVG 预编译是 mmdc 的扩展用法，当前 mmdc 已在 docs_fixer 中使用
- KaTeX 全渲染需要 Node.js，用现有 regex 校验 + 降级策略代替

---

## 现有代码基线

docs_fixer.py 已有：
- Phase 1: mdformat（可选，并行）
- Phase 2: Math 正则校验 + LLM 修复
- Phase 3: Mermaid mmdc/正则校验 + LLM 修复
- Hash 缓存（增量跳过未变文件）
- FixStats 返回值（仅记录，不阻断）

**缺失：**
- 失败降级（坏 Mermaid/Math 直接留原文）
- 链接校验（完全没有）
- Lint 报告（仅 FixStats 日志）
- 构建门禁（不 raise）

---

## Phase 1: 降级策略（对齐 v3.md 3.5 L204, L207）

**修改文件:** `codewiki/src/be/docs_fixer.py`

### Mermaid 降级

当 Mermaid 修复失败（LLM 也修不好）时，当前行为是留原文。改为：

```python
# 降级：将 ```mermaid ... ``` 改为 ```text ... ``` 并标注错误
degraded = f"```text\n[MERMAID DIAGRAM - RENDER FAILED]\n{original_code}\n```\n"
degraded += f"<!-- mermaid-error: {error_message} -->\n"
```

### Math 降级

当 Math 修复失败时，区分 display math 和 inline math：

```python
# Inline math ($...$) → 单反引号内联代码
if is_inline:
    degraded = f"`{original_math}` <!-- math-error: {error_message} -->"
# Display math ($$...$$, \[...\]) → fenced code block 保留可读性
else:
    degraded = f"```latex\n{original_math}\n```\n<!-- math-error: {error_message} -->"
```

### 测试
- `test_mermaid_fallback_degradation` — 不可修复的 Mermaid → 降级为 text 代码块
- `test_math_fallback_degradation` — 不可修复的 Math → 降级为内联代码
- `test_degradation_preserves_original` — 降级后原始内容可恢复

---

## Phase 2: 链接校验 + 锚点注册（对齐 v3.md 4.5 L424-425）

**新文件:** `codewiki/src/be/postprocess/link_validator.py`

### 锚点注册

```python
def build_anchor_registry(docs_dir: str) -> dict[str, set[str]]:
    """Scan all .md files, extract headings as anchors.

    Returns: {filename: {anchor1, anchor2, ...}}

    Anchor generation MUST match the actual renderer's rules.
    Current frontend uses bare MarkdownIt() without anchor/slug plugins
    (codewiki/src/fe/visualise_docs.py:46, :115), so heading IDs follow
    MarkdownIt's default: lowercase, spaces→hyphens, strip most punctuation,
    collapse consecutive hyphens.

    Implementation: use markdown_it_py (already in deps) to parse each file
    and extract the actual anchor IDs from the rendered HTML, rather than
    reimplementing slug rules. This guarantees consistency with the renderer.
    """
```

### 链接提取与校验

```python
@dataclass
class LinkIssue:
    source_file: str
    line_number: int
    link_text: str
    target: str
    issue_type: str  # "file_not_found", "anchor_not_found", "empty_link"

def validate_links(docs_dir: str) -> list[LinkIssue]:
    """Scan all .md files for internal links, validate each.

    Checks:
    1. [text](file.md) — file exists
    2. [text](file.md#anchor) — file exists AND anchor exists
    3. [text](#anchor) — same-file anchor exists
    4. Empty/malformed links

    Skips external links (http://, https://).
    """
```

### 接入 docs_fixer

在 fix_docs() 末尾调用 validate_links()，结果纳入 FixStats。

### 测试
- `test_anchor_registry_extracts_headings` — # H1, ## H2 → anchors
- `test_anchor_generation_rules` — "My Heading!" → "my-heading"
- `test_validate_links_file_not_found` — [text](missing.md) → LinkIssue
- `test_validate_links_anchor_not_found` — [text](file.md#bad) → LinkIssue
- `test_validate_links_same_file_anchor` — [text](#heading) → valid
- `test_validate_links_skips_external` — https://... → no issue
- `test_validate_links_empty_docs_dir` — empty dir → no issues

---

## Phase 3: Lint 报告 + 门禁（对齐 v3.md 4.5 L438-439）

**修改文件:** `codewiki/src/be/docs_fixer.py`

### Lint 报告

```python
@dataclass
class LintReport:
    """Structured report of all lint issues found during post-processing."""
    mermaid_failures: list[dict]  # {file, block_index, error, degraded}
    math_failures: list[dict]    # {file, expression, error, degraded}
    link_issues: list[dict]      # {file, line, target, issue_type}
    timestamp: str
    total_files: int

    def to_json(self) -> str: ...
    def summary(self) -> str: ...

    @property
    def has_failures(self) -> bool:
        return bool(self.mermaid_failures or self.math_failures or self.link_issues)
```

保存到 `{docs_dir}/_lint_report.json`。

### 门禁（可配置）

```python
def fix_docs(working_dir: str, config: Config, strict: bool = False) -> FixStats:
    """
    ...
    If strict=True, raises LintError when unfixable issues remain.
    If strict=False (default), logs warnings and continues.
    """
```

`strict` 模式由配置控制（config 或环境变量），默认 False 保持向后兼容。

### 测试
- `test_lint_report_written_to_disk` — fix_docs 后 _lint_report.json 存在
- `test_lint_report_contains_failures` — 有坏 mermaid → report 包含条目
- `test_lint_report_summary` — summary() 返回可读摘要
- `test_strict_mode_raises_on_failures` — strict=True + failures → raises
- `test_non_strict_mode_continues` — strict=False + failures → 正常返回

---

## Phase 4: 集成

### 新文件
- `codewiki/src/be/postprocess/__init__.py`
- `codewiki/src/be/postprocess/link_validator.py`

### 修改文件
- `codewiki/src/be/docs_fixer.py` — 降级策略 + LintReport + 门禁
- `codewiki/src/be/documentation_generator.py` — 传 strict 参数

### 新测试
- `tests/test_postprocess_link_validator.py`
- `tests/test_postprocess_lint_report.py`

---

## 实施顺序

| 顺序 | Phase | 产出 |
|------|-------|------|
| 1 | Phase 2: link_validator.py | 锚点注册 + 链接校验（独立可测） |
| 2 | Phase 1: 降级策略 | docs_fixer 内修改 |
| 3 | Phase 3: LintReport + 门禁 | 报告 + strict 模式 |
| 4 | Phase 4: 集成 + 全量测试 | 交付 |
