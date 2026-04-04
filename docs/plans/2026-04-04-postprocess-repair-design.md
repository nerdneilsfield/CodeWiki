# Enhanced Math & Mermaid Repair — Design Spec

**Date:** 2026-04-04
**Status:** Draft
**Source:** Ported from `ai-deepresearch-flow/python/deepresearch_flow/recognize/{math,mermaid}.py`

---

## 1. Problem

The current postprocess pipeline (`docs_fixer.py`) has basic math and mermaid repair:
- **Math:** bracket/`\begin-\end` matching only; no KaTeX rendering validation; no cleanup rule library; one-at-a-time LLM repair using `main_model`.
- **Mermaid:** mmdc/regex validation; no deterministic cleanup rules; one-at-a-time LLM repair using `main_model`.

`ai-deepresearch-flow` has battle-tested validators with dual-layer math validation (pylatexenc AST + KaTeX rendering), extensive cleanup rule libraries that fix common errors without LLM, and batch LLM repair. This spec ports those capabilities into CodeWiki.

## 2. Goals

1. Dual-layer math validation: pylatexenc (strong dependency) + KaTeX (optional, graceful fallback)
2. Deterministic cleanup rules for math and mermaid (fix without LLM when possible)
3. Batch LLM repair (multiple issues per prompt, JSON structured I/O)
4. Dedicated `[postprocess]` config section with repair model, two fallbacks, batch size, max retries
5. Migrate existing `postprocess_strict` and `postprocess_fix_links` into `[postprocess]` section

## 3. Config Surface

### 3.1 TOML

```toml
[postprocess]
strict             = false                              # was postprocess_strict
fix_links          = true                               # was postprocess_fix_links
repair_model       = "openai/gpt-4o-mini"               # empty string = use main_model
repair_fallback_1  = "claude/claude-sonnet-4-5-20250929" # first fallback; empty = none
repair_fallback_2  = "openai/gpt-4.1"                   # second fallback; empty = none
repair_batch_size  = 8                                   # issues per LLM prompt
repair_max_retries = 2                                   # retries per model before fallback
```

### 3.2 Python Model

```python
class PostprocessConfig(BaseModel):
    strict: bool = False
    fix_links: bool = True
    repair_model: str = ""
    repair_fallback_1: str = ""
    repair_fallback_2: str = ""
    repair_batch_size: int = 8
    repair_max_retries: int = 2
```

`CodeWikiConfig` changes:
- **Delete** top-level fields `postprocess_strict` and `postprocess_fix_links` entirely — no properties, no backward compat shim.
- **Add** field: `postprocess: PostprocessConfig = Field(default_factory=PostprocessConfig)`
- All call sites and tests that referenced `config.postprocess_strict` or `config.postprocess_fix_links` are migrated to `config.postprocess.strict` / `config.postprocess.fix_links` in the same change. This is a breaking config change — old TOML files with top-level `postprocess_strict` will be silently ignored (pydantic `extra="ignore"`).

### 3.3 Config Loader

`config_loader.py` parses `data.get("postprocess", {})` into `PostprocessConfig`. No backward-compat merging from old `runtime.postprocess_strict` — users must move to `[postprocess] strict = true`.

### 3.4 Model Fallback Chain

Repair functions build a model chain: `[repair_model, repair_fallback_1, repair_fallback_2]` (filtering empty strings). If `repair_model` is empty, `config.main_model` is used as the first entry. Each model is tried with up to `repair_max_retries` retries (via existing `with_retry_sync`). On exhaustion, the next model is tried. If all fail, the issue is recorded as failed and the content is degraded.

## 4. New File: `codewiki/src/be/postprocess/math_validator.py`

### 4.1 Validation

**`validate_formula(text: str, display_mode: bool) -> list[str]`** — returns error list (empty = valid).

Two layers, both run when available:
1. **pylatexenc** (`_validate_pylatex`): `LatexWalker(text).get_latex_nodes()`. Strong dependency — always available (added to `pyproject.toml` as required).
2. **KaTeX** (`_validate_katex`): persistent Node subprocess via `NodeKatexValidator`. Optional — import-guarded; skipped if `node` binary or `katex` npm package unavailable.

Both layers run independently; their error lists are concatenated. No fallback to the old bracket/env matching — pylatexenc subsumes it.

**`NodeKatexValidator`**: identical to deepresearch-flow. Spawns `node katex_check.js` once, communicates via stdin/stdout JSON lines, auto-respawns on crash, `atexit` cleanup.

### 4.2 Cleanup Rules

**`cleanup_formula(text: str) -> str`** — deterministic regex fixes, LLM-output focused (OCR rules removed):
- Escape corruption: `\x08eta` → `\beta`, `\x08ar` → `\bar`, `\x08egin` → `\begin`, `\x08oldsymbol` → `\boldsymbol`
- Double superscript: `x^a^b` → `(x^a)^b`
- `\text{}` with embedded math commands → split out
- Unknown capitalized commands → `\text{Command}`
- Spaced text collapse in `\text{}` / `\operatorname{}`
- Stray subscript/superscript cleanup (`^_`, `^''`, `_\times`)
- `\left ceil` / `\right ceil` → `\left\lceil` / `\right\rceil`

### 4.3 Batch LLM Repair

**`build_repair_prompt(issues: list[FormulaIssue]) -> str`**: JSON payload with `id`, `delimiter`, `latex`, `errors` per issue. System prompt instructs JSON-only output with `{"items": [{"id", "latex"}]}`.

**`parse_repair_response(response: str) -> dict[str, str]`**: extract JSON from response, map issue_id → fixed latex.

**`repair_batch_sync(issues, config, pp_config, usage_stats) -> dict[str, str]`**: iterates model chain, calls `call_llm` with JSON prompt, parses response. Uses `with_retry_sync` per model.

### 4.4 Data Types

```python
@dataclass(frozen=True)
class FormulaSpan:
    start: int; end: int; delimiter: str; content: str; line: int

@dataclass
class FormulaIssue:
    issue_id: str; span: FormulaSpan; errors: list[str]; cleaned: str
```

### 4.5 Top-level Entry

**`extract_math_spans(text: str) -> list[FormulaSpan]`**: regex extraction with two masking layers:
1. **Code-block masking** — fenced blocks (``` ``` ```, `~~~`) and inline code are replaced with placeholders before math regex runs, preventing false positives inside code.
2. **Escaped-dollar masking** — literal `\$` sequences are masked before inline-math regex runs, then restored after. This preserves the fix from `docs_fixer.py:232` that prevents `\$` (backslash-dollar) from being misinterpreted as an inline-math delimiter. The masking uses `str.split` (not regex) because Python's `re` module treats `\$` as a literal-dollar pattern.

Handles `$$`, `$`, `\[`, `\(`.

**`fix_math_in_text(text, config, pp_config, stats, usage_stats, report, filename) -> str`**: full pipeline — extract → validate → cleanup → collect issues → batch repair → apply replacements → degrade failures.

## 5. New File: `codewiki/src/be/postprocess/mermaid_validator.py`

### 5.1 Validation

Reuses existing `_validate_with_mmdc()` (moved here) and `_validate_with_regex()` (moved here).

### 5.2 Cleanup Rules

**`cleanup_mermaid(text: str) -> str`** — deterministic fixes (OCR rules removed):
- Smart quotes → ASCII quotes
- `\n` escape expansion (inside label → `<br/>`, outside → newline)
- Subgraph label normalization (CJK in ID → generated ID + label)
- Edge label repair: `-->[label]` → `-->|label|`
- Compacted statement splitting
- Chained edge splitting
- Unbalanced bracket closure
- HTML label wrapping (`<br/>` in labels)
- Cylinder label normalization
- Multi-source edge expansion (`A & B --> C` → two lines)
- Subgraph ID deduplication

### 5.3 Batch LLM Repair

Same structure as math: `build_repair_prompt` / `parse_repair_response` / `repair_batch_sync`. System prompt includes Mermaid safe subset constraints (ASCII IDs, double-quoted labels, `graph TD`, one statement per line, no `%%` comments).

### 5.4 Data Types

```python
@dataclass(frozen=True)
class MermaidSpan:
    start: int; end: int; content: str; line: int

@dataclass
class MermaidIssue:
    issue_id: str; span: MermaidSpan; errors: list[str]
```

### 5.5 Top-level Entry

**`extract_mermaid_spans(text: str) -> list[MermaidSpan]`**: regex extraction.

**`fix_mermaid_in_text(text, config, pp_config, stats, usage_stats, report, filename) -> str`**: extract → validate → cleanup → collect issues → batch repair → apply replacements → degrade failures.

## 6. New File: `codewiki/src/be/postprocess/katex_check.js`

30-line Node.js script from deepresearch-flow, verbatim:
- Reads JSON lines from stdin: `{"latex": "...", "opts": {"displayMode": true}}`
- Calls `katex.renderToString()` with `throwOnError: true`
- Writes `{"ok": true}` or `{"ok": false, "error": "..."}` to stdout

## 7. Modified: `codewiki/src/be/docs_fixer.py`

### 7.1 Removals

Delete from `docs_fixer.py`:
- `_validate_math`, `_MATH_REPAIR_USER`, `_llm_repair_math`, `_fix_math_in_text` — replaced by `math_validator.py`
- `_MMDC_PATH`, `_MMDC_CHECKED`, `_find_mmdc`, `_validate_with_mmdc`, `_validate_with_regex`, `_has_unquoted_nonascii`, `_REPAIR_USER`, `_llm_repair`, `_fix_mermaid_in_text`, `fix_mermaid_in_file` — replaced by `mermaid_validator.py`
- Math/Mermaid regex constants that move to the new modules

### 7.2 Changes

`fix_docs()` Phase 2 and Phase 3 call into new modules:
```python
from codewiki.src.be.postprocess.math_validator import fix_math_in_text
from codewiki.src.be.postprocess.mermaid_validator import fix_mermaid_in_text

# Phase 2
text = fix_math_in_text(text, config, pp_config, stats, usage_stats, report, filename)
# Phase 3
text = fix_mermaid_in_text(text, config, pp_config, stats, usage_stats, report, filename)
```

`FixStats` stays in `docs_fixer.py` (shared by both phases). `fix_mermaid_in_file()` backward-compat wrapper delegates to new module.

### 7.3 Config Access

`fix_docs` reads `config.postprocess` to get `PostprocessConfig`, passes it to both fix functions.

## 8. Modified: `config.example.toml`

Add `[postprocess]` section with all fields commented with defaults.

## 9. Modified: `pyproject.toml`

Add `pylatexenc` to dependencies (not optional — it's lightweight).

## 10. Dependencies

| Dependency | Type | Fallback |
|-----------|------|----------|
| `pylatexenc` | **Required** (pip) | N/A — always installed |
| `katex` (npm) | Optional | Skip KaTeX rendering validation; pylatexenc still runs |
| `node` | Optional (system) | Skip KaTeX validation |
| `mmdc` | Optional (`npm i -g @mermaid-js/mermaid-cli`) | Fall back to regex heuristics (existing behavior) |

## 11. File Manifest

| Action | File |
|--------|------|
| Create | `codewiki/src/be/postprocess/math_validator.py` |
| Create | `codewiki/src/be/postprocess/mermaid_validator.py` |
| Create | `codewiki/src/be/postprocess/katex_check.js` |
| Modify | `codewiki/src/codewiki_config.py` — add `PostprocessConfig`, migrate properties |
| Modify | `codewiki/src/config_loader.py` — parse `[postprocess]` section |
| Modify | `codewiki/src/be/docs_fixer.py` — delete old math/mermaid code, wire new modules |
| Modify | `config.example.toml` — add `[postprocess]` section |
| Modify | `pyproject.toml` — add `pylatexenc` |
| Create | `tests/test_math_validator.py` |
| Create | `tests/test_mermaid_validator.py` |
| Create | `tests/test_postprocess_config.py` |
