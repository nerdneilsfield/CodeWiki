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

## Phase 2: 统一 Heading Anchor + 链接校验（对齐 v3.md 4.5 L424-425）

### 问题现状

当前前端没有稳定 heading anchor：
- 服务端 `markdown_to_html()` 用裸 MarkdownIt()，不生成 heading id（visualise_docs.py:46, :115）
- 浏览器端 JS 事后按顺序赋 h-0, h-1...（templates.py:542）
- 文档中的 `[text](#heading)` 锚点链接根本不工作

### 方案：一次统一生成 + 校验

**Step 2a: 引入稳定 heading slug 函数**

**新文件:** `codewiki/src/be/postprocess/anchor.py`

```python
def heading_to_slug(text: str) -> str:
    """Convert heading text to a stable anchor slug.

    Rules (deterministic, same as this function everywhere):
    - Lowercase
    - Strip leading/trailing whitespace
    - Replace spaces and underscores with hyphens
    - Remove non-alphanumeric chars except hyphens and CJK
    - Collapse consecutive hyphens
    - Strip leading/trailing hyphens

    This is the SINGLE source of truth for anchor generation.
    Both the renderer and the link validator MUST use this function.
    """
```

**Step 2b: 修改渲染链路注入 heading ids**

**修改文件:** `codewiki/src/fe/visualise_docs.py`

在 `markdown_to_html()` 中，渲染后对每个 `<h1>`...`<h6>` 标签注入
`id` 属性（使用 `heading_to_slug`）。移除前端 JS 的 h-0/h-1 顺序赋值。

```python
import re
from codewiki.src.be.postprocess.anchor import heading_to_slug

def _inject_heading_ids(html: str) -> str:
    """Add id attributes to heading tags using stable slug function."""
    def replacer(match):
        tag = match.group(1)  # h1, h2, ...
        text = re.sub(r'<[^>]+>', '', match.group(2))  # strip inner HTML
        slug = heading_to_slug(text)
        return f'<{tag} id="{slug}">{match.group(2)}</{tag}>'
    return re.sub(r'<(h[1-6])>(.*?)</\1>', replacer, html)
```

**Step 2c: 链接校验**

**新文件:** `codewiki/src/be/postprocess/link_validator.py`

```python
def build_anchor_registry(docs_dir: str) -> dict[str, set[str]]:
    """Scan all .md files, extract headings and compute anchor slugs.

    Uses heading_to_slug() — the same function used by the renderer.
    Returns: {relative_filename: {slug1, slug2, ...}}
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

**anchor.py:**
- `test_heading_to_slug_basic` — "My Heading" → "my-heading"
- `test_heading_to_slug_strips_punctuation` — "Hello, World!" → "hello-world"
- `test_heading_to_slug_collapses_hyphens` — "foo  --  bar" → "foo-bar"
- `test_heading_to_slug_cjk` — "中文标题" → "中文标题"（保留 CJK）
- `test_heading_to_slug_deterministic` — 同输入多次调用结果一致

**link_validator.py:**
- `test_anchor_registry_extracts_headings` — # H1, ## H2 → slugs
- `test_anchor_registry_uses_heading_to_slug` — 验证用的是同一个 slug 函数
- `test_validate_links_file_not_found` — [text](missing.md) → LinkIssue
- `test_validate_links_anchor_not_found` — [text](file.md#bad) → LinkIssue
- `test_validate_links_same_file_anchor` — [text](#heading) → valid
- `test_validate_links_skips_external` — https://... → no issue
- `test_validate_links_empty_docs_dir` — empty dir → no issues

**visualise_docs.py (integration):**
- `test_rendered_html_has_heading_ids` — 渲染后 `<h1 id="...">` 存在
- `test_heading_ids_match_slug_function` — 渲染 id = heading_to_slug(text)

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

**入口：新增 Config 字段**

```python
# codewiki/src/config.py
@dataclass
class Config:
    # ... existing fields ...
    postprocess_strict: bool = False  # 新增：True 时 lint 失败阻断构建
```

`fix_docs` 从 config 读取：

```python
def fix_docs(working_dir: str, config: Config) -> FixStats:
    """
    ...
    If config.postprocess_strict is True, raises LintError when
    unfixable issues remain. Default False preserves backward compat.
    """
    strict = getattr(config, 'postprocess_strict', False)
    ...
```

不引入环境变量，统一走 Config 对象。

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
- `codewiki/src/be/postprocess/anchor.py` — heading_to_slug（唯一锚点规则源）
- `codewiki/src/be/postprocess/link_validator.py`

### 修改文件
- `codewiki/src/be/docs_fixer.py` — 降级策略 + LintReport + 门禁
- `codewiki/src/fe/visualise_docs.py` — 注入 heading ids（使用 heading_to_slug）
- `codewiki/src/fe/templates.py` — 移除 JS 端 h-0/h-1 顺序赋值逻辑
- `codewiki/src/config.py` — 新增 postprocess_strict: bool = False

### 新测试
- `tests/test_postprocess_anchor.py`
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
