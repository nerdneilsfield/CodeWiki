# Enhanced Math & Mermaid Repair — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port battle-tested math (pylatexenc + KaTeX) and mermaid (cleanup rules + batch LLM) repair from `ai-deepresearch-flow` into CodeWiki's postprocess pipeline, with dedicated `[postprocess]` config section.

**Architecture:** New `math_validator.py` and `mermaid_validator.py` modules under `codewiki/src/be/postprocess/` replace inline repair code in `docs_fixer.py`. A `PostprocessConfig` pydantic model holds all repair settings. Batch LLM repair uses the existing `call_llm` + `with_retry_sync` with a 3-model fallback chain.

**Tech Stack:** pylatexenc (LaTeX AST), KaTeX via Node.js subprocess, existing `call_llm`/`with_retry_sync`, pydantic config models

---

### Task 1: Config — `PostprocessConfig` and call-site migration

**Files:**
- Modify: `codewiki/src/codewiki_config.py`
- Modify: `codewiki/src/config_loader.py`
- Modify: `codewiki/src/config.py`
- Modify: `codewiki/src/be/docs_fixer.py`
- Modify: `codewiki/cli/commands/config.py`
- Modify: `config.example.toml`
- Modify: `pyproject.toml`
- Modify: `tests/test_docs_fixer_helpers.py`
- Modify: `tests/test_generation_state.py`
- Modify: `tests/test_link_rewriter.py`
- Create: `tests/test_postprocess_config.py`

- [ ] **Step 1: Write failing tests for PostprocessConfig**

Create `tests/test_postprocess_config.py`:

```python
from codewiki.src.codewiki_config import CodeWikiConfig, PostprocessConfig


class TestPostprocessConfig:
    def test_defaults(self):
        pp = PostprocessConfig()
        assert pp.strict is False
        assert pp.fix_links is True
        assert pp.repair_model == ""
        assert pp.repair_fallback_1 == ""
        assert pp.repair_fallback_2 == ""
        assert pp.repair_batch_size == 8
        assert pp.repair_max_retries == 2

    def test_codewiki_config_has_postprocess(self):
        config = CodeWikiConfig(
            repo_path="/tmp",
            docs_dir="/tmp/docs",
        )
        assert isinstance(config.postprocess, PostprocessConfig)
        assert config.postprocess.strict is False

    def test_codewiki_config_no_top_level_postprocess_strict(self):
        config = CodeWikiConfig(
            repo_path="/tmp",
            docs_dir="/tmp/docs",
        )
        assert not hasattr(config, "postprocess_strict") or "postprocess_strict" not in config.model_fields

    def test_postprocess_section_parsed_from_dict(self):
        config = CodeWikiConfig(
            repo_path="/tmp",
            docs_dir="/tmp/docs",
            postprocess=PostprocessConfig(
                strict=True,
                repair_model="openai/gpt-4o-mini",
                repair_fallback_1="claude/claude-sonnet-4-5-20250929",
                repair_fallback_2="openai/gpt-4.1",
                repair_batch_size=16,
                repair_max_retries=3,
            ),
        )
        assert config.postprocess.strict is True
        assert config.postprocess.repair_model == "openai/gpt-4o-mini"
        assert config.postprocess.repair_batch_size == 16


class TestConfigLoaderPostprocess:
    def test_load_config_parses_postprocess_section(self, tmp_path):
        import tomli_w

        from codewiki.src.config_loader import load_config

        toml_data = {
            "runtime": {"output_dir": str(tmp_path / "docs")},
            "generation": {"main_model": "openai/gpt-4o-mini", "cluster_model": "openai/gpt-4o-mini"},
            "postprocess": {
                "strict": True,
                "repair_model": "openai/gpt-4o-mini",
                "repair_batch_size": 4,
            },
            "providers": [
                {
                    "name": "openai",
                    "type": "openai_compatible",
                    "base_url": "http://localhost",
                    "api_keys": ["test-key"],
                    "model_list": ["gpt-4o-mini"],
                }
            ],
        }
        config_path = tmp_path / "config.toml"
        config_path.write_bytes(tomli_w.dumps(toml_data))

        config = load_config(str(config_path), repo_path=str(tmp_path))
        assert config.postprocess.strict is True
        assert config.postprocess.repair_model == "openai/gpt-4o-mini"
        assert config.postprocess.repair_batch_size == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_postprocess_config.py -v`
Expected: FAIL — `PostprocessConfig` does not exist yet

- [ ] **Step 3: Add PostprocessConfig to codewiki_config.py**

In `codewiki/src/codewiki_config.py`, add `PostprocessConfig` class before `CodeWikiConfig`, delete `postprocess_strict` and `postprocess_fix_links` fields from `CodeWikiConfig`, add `postprocess` field:

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

In `CodeWikiConfig`:
- Delete lines: `postprocess_strict: bool = False` and `postprocess_fix_links: bool = True`
- Add: `postprocess: PostprocessConfig = Field(default_factory=PostprocessConfig)`

- [ ] **Step 4: Update config_loader.py to parse [postprocess]**

In `codewiki/src/config_loader.py`:
- Delete `postprocess_strict` field from `RuntimeOverrides` dataclass (line 42)
- Delete `postprocess_strict` resolution from `_resolve_runtime_section` and the kwargs that pass it to `CodeWikiConfig` (lines 300-303)
- Add parsing of `[postprocess]` section into `PostprocessConfig`:

```python
pp_data = data.get("postprocess", {})
postprocess_config = PostprocessConfig(**pp_data)
```

Pass `postprocess=postprocess_config` when constructing `CodeWikiConfig`.

Note: both `postprocess_strict` AND `postprocess_fix_links` must be fully removed from the old paths — `RuntimeOverrides`, `_resolve_runtime_section`, and the `CodeWikiConfig(...)` constructor call. The old `runtime.get("postprocess_strict", ...)` read path is dead after this step.

- [ ] **Step 5: Delete `postprocess_fix_links` constant from config.py**

In `codewiki/src/config.py`, delete line 16: `postprocess_fix_links = True`

- [ ] **Step 6: Migrate call sites in docs_fixer.py**

In `codewiki/src/be/docs_fixer.py`:
- Line 666: `if getattr(config, "postprocess_fix_links", True):` → `if config.postprocess.fix_links:`
- Line 714: `if getattr(config, "postprocess_strict", False) and report.has_failures:` → `if config.postprocess.strict and report.has_failures:`

- [ ] **Step 7: Migrate call sites in cli/commands/config.py**

In `codewiki/cli/commands/config.py`:
- Template string line 46: `postprocess_strict = false` → remove this line from the `[runtime]` template, add a `[postprocess]` section instead:
```
[postprocess]
strict             = false
fix_links          = true
# repair_model     = ""
# repair_fallback_1 = ""
# repair_fallback_2 = ""
# repair_batch_size = 8
# repair_max_retries = 2
```
- In `_config_to_dict()` / show command output: replace `"postprocess_strict": cfg.postprocess_strict` with a full `postprocess` sub-dict:
```python
"postprocess": {
    "strict": cfg.postprocess.strict,
    "fix_links": cfg.postprocess.fix_links,
    "repair_model": cfg.postprocess.repair_model,
    "repair_fallback_1": cfg.postprocess.repair_fallback_1,
    "repair_fallback_2": cfg.postprocess.repair_fallback_2,
    "repair_batch_size": cfg.postprocess.repair_batch_size,
    "repair_max_retries": cfg.postprocess.repair_max_retries,
},
```
Remove any old `"postprocess_strict"` or `"postprocess_fix_links"` keys from the dict.

- [ ] **Step 8: Migrate tests**

In `tests/test_docs_fixer_helpers.py`:
- Lines 85-86: replace `config.postprocess_fix_links = False` / `config.postprocess_strict = True` with:
```python
config.postprocess = MagicMock()
config.postprocess.fix_links = False
config.postprocess.strict = True
```
- Lines 143-144: same pattern, set `config.postprocess.fix_links = True`, `config.postprocess.strict = False`

In `tests/test_generation_state.py`:
- Delete lines 16 and 21 that import and assert `postprocess_fix_links` from `codewiki.src.config`

In `tests/test_link_rewriter.py`:
- Line 130: replace `SimpleNamespace(postprocess_strict=False, postprocess_fix_links=True)` with `SimpleNamespace(postprocess=SimpleNamespace(strict=False, fix_links=True))`

- [ ] **Step 9: Update config.example.toml**

Replace line 24 (`postprocess_strict = false`) with a new `[postprocess]` section after `[agent]`:

```toml
[postprocess]
strict             = false       # true = block build on unfixable lint issues
fix_links          = true        # validate and rewrite internal links
# repair_model     = "openai/gpt-4o-mini"    # model for math/mermaid repair; empty = main_model
# repair_fallback_1 = ""                      # first fallback model
# repair_fallback_2 = ""                      # second fallback model
# repair_batch_size = 8                       # issues per repair prompt
# repair_max_retries = 2                      # retries per model
```

- [ ] **Step 10: Add pylatexenc to pyproject.toml**

Add `"pylatexenc>=3.0"` to the `dependencies` list in `pyproject.toml`.

- [ ] **Step 11: Run all tests**

Run: `uv sync && uv run python -m pytest tests/ -q`
Expected: all pass (including the new `test_postprocess_config.py`)

- [ ] **Step 12: Run typecheck**

Run: `make typecheck`
Expected: PASS

- [ ] **Step 13: Commit**

```bash
git add codewiki/src/codewiki_config.py codewiki/src/config_loader.py codewiki/src/config.py \
  codewiki/src/be/docs_fixer.py codewiki/cli/commands/config.py config.example.toml \
  pyproject.toml uv.lock tests/test_postprocess_config.py tests/test_docs_fixer_helpers.py \
  tests/test_generation_state.py tests/test_link_rewriter.py
git commit -m "feat(config): add [postprocess] section with repair model/batch/retry fields"
```

---

### Task 2: Math validator — validation + cleanup + batch repair

**Files:**
- Create: `codewiki/src/be/postprocess/katex_check.js`
- Create: `codewiki/src/be/postprocess/math_validator.py`
- Create: `tests/test_math_validator.py`

- [ ] **Step 1: Create katex_check.js**

Create `codewiki/src/be/postprocess/katex_check.js`:

```javascript
const katex = require("katex");

function check(expr, opts) {
  try {
    katex.renderToString(expr, {
      throwOnError: true,
      displayMode: !!opts.displayMode,
      strict: opts.strict ?? "warn",
    });
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e && e.message ? e.message : String(e) };
  }
}

let buf = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buf += chunk;
  let idx;
  while ((idx = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, idx);
    buf = buf.slice(idx + 1);
    if (!line.trim()) continue;
    const req = JSON.parse(line);
    const res = check(req.latex, req.opts || {});
    process.stdout.write(JSON.stringify(res) + "\n");
  }
});
```

- [ ] **Step 2: Write failing tests for math_validator**

Create `tests/test_math_validator.py`:

```python
import pytest


class TestCleanupFormula:
    def test_backspace_beta(self):
        from codewiki.src.be.postprocess.math_validator import cleanup_formula

        assert r"\beta" in cleanup_formula("\x08eta")

    def test_double_superscript(self):
        from codewiki.src.be.postprocess.math_validator import cleanup_formula

        result = cleanup_formula("x^a^b")
        assert "^" in result
        assert result != "x^a^b"

    def test_left_ceil_repair(self):
        from codewiki.src.be.postprocess.math_validator import cleanup_formula

        result = cleanup_formula(r"\left ceil")
        assert r"\left\lceil" in result

    def test_noop_on_valid(self):
        from codewiki.src.be.postprocess.math_validator import cleanup_formula

        valid = r"\frac{1}{2}"
        assert cleanup_formula(valid) == valid


class TestValidateFormula:
    def test_valid_formula_returns_empty(self):
        from codewiki.src.be.postprocess.math_validator import validate_formula

        assert validate_formula(r"\frac{1}{2}", display_mode=True) == []

    def test_unmatched_brace_detected(self):
        from codewiki.src.be.postprocess.math_validator import validate_formula

        errors = validate_formula(r"\frac{1}{", display_mode=True)
        assert len(errors) > 0


class TestExtractMathSpans:
    def test_extracts_display_and_inline(self):
        from codewiki.src.be.postprocess.math_validator import extract_math_spans

        text = "Inline $x^2$ and display $$\\sum_{i=0}^n i$$"
        spans = extract_math_spans(text)
        assert len(spans) == 2
        delimiters = {s.delimiter for s in spans}
        assert delimiters == {"$", "$$"}

    def test_escaped_dollar_not_matched(self):
        from codewiki.src.be.postprocess.math_validator import extract_math_spans

        text = r"Price is \$5 and \$10"
        spans = extract_math_spans(text)
        assert len(spans) == 0

    def test_code_block_not_matched(self):
        from codewiki.src.be.postprocess.math_validator import extract_math_spans

        text = "```\n$x^2$\n```"
        spans = extract_math_spans(text)
        assert len(spans) == 0

    def test_bracket_delimiters(self):
        from codewiki.src.be.postprocess.math_validator import extract_math_spans

        text = r"Display \[x + y\] and inline \(a + b\)"
        spans = extract_math_spans(text)
        assert len(spans) == 2

    def test_escaped_dollar_inside_code_block_no_interaction(self):
        from codewiki.src.be.postprocess.math_validator import extract_math_spans

        text = "```\n\\$5 and $x^2$\n```\nOutside \\$10 and $y^2$"
        spans = extract_math_spans(text)
        # Only the $y^2$ outside the code block should match
        assert len(spans) == 1
        assert spans[0].content == "y^2"


class TestBuildRepairPrompt:
    def test_prompt_contains_json(self):
        from codewiki.src.be.postprocess.math_validator import (
            FormulaIssue,
            FormulaSpan,
            build_repair_prompt,
        )

        span = FormulaSpan(start=0, end=10, delimiter="$$", content=r"\frac{1}{", line=1)
        issue = FormulaIssue(issue_id="a:0", span=span, errors=["unmatched brace"], cleaned=r"\frac{1}{")
        prompt = build_repair_prompt([issue])
        assert '"id"' in prompt
        assert '"latex"' in prompt
        assert "a:0" in prompt


class TestParseRepairResponse:
    def test_parses_valid_json(self):
        from codewiki.src.be.postprocess.math_validator import parse_repair_response

        response = '{"items": [{"id": "a:0", "latex": "\\\\frac{1}{2}"}]}'
        result = parse_repair_response(response)
        assert result["a:0"] == r"\frac{1}{2}"

    def test_returns_empty_on_bad_json(self):
        from codewiki.src.be.postprocess.math_validator import parse_repair_response

        assert parse_repair_response("not json") == {}


class TestRepairModelChain:
    def test_build_model_chain_uses_main_model_when_repair_empty(self):
        from codewiki.src.be.postprocess.math_validator import _build_model_chain
        from codewiki.src.codewiki_config import PostprocessConfig

        pp = PostprocessConfig(repair_model="", repair_fallback_1="m2", repair_fallback_2="")
        chain = _build_model_chain(pp, main_model="m1")
        assert chain == ["m1", "m2"]

    def test_build_model_chain_full(self):
        from codewiki.src.be.postprocess.math_validator import _build_model_chain
        from codewiki.src.codewiki_config import PostprocessConfig

        pp = PostprocessConfig(repair_model="r", repair_fallback_1="f1", repair_fallback_2="f2")
        chain = _build_model_chain(pp, main_model="m")
        assert chain == ["r", "f1", "f2"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_math_validator.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 4: Implement math_validator.py**

Create `codewiki/src/be/postprocess/math_validator.py`. The file must contain:

1. **Data types**: `FormulaSpan` (frozen dataclass), `FormulaIssue` (dataclass)
2. **`cleanup_formula(text)`**: regex rules ported from deepresearch-flow (LLM-output subset only)
3. **`_validate_pylatex(text)`**: pylatexenc `LatexWalker` validation (always available)
4. **`NodeKatexValidator` class**: persistent Node subprocess, stdin/stdout JSON lines, auto-respawn, atexit cleanup
5. **`_validate_katex(text, display_mode)`**: calls validator, returns error or None
6. **`validate_formula(text, display_mode)`**: runs both layers, concatenates errors
7. **`extract_math_spans(text)`**: regex extraction with code-block masking + escaped-dollar masking (using `str.split`, not regex, for `\$`)
8. **`build_repair_prompt(issues)`**: JSON prompt with system instruction
9. **`parse_repair_response(response)`**: JSON parse → `dict[str, str]`
10. **`_build_model_chain(pp_config, main_model)`**: `[repair_model or main_model, fallback_1, fallback_2]` filtering empties
11. **`repair_batch_sync(issues, config, pp_config, usage_stats)`**: iterate model chain, each with `with_retry_sync(call_llm, ...)`, parse response
12. **`fix_math_in_text(text, config, stats, usage_stats, report, filename)`**: full pipeline — extract → validate → cleanup → collect issues → batch by `pp_config.repair_batch_size` → repair → apply replacements → degrade failures

Key implementation detail for `extract_math_spans`: the escaped-dollar masking must use `str.split("\\$")` then placeholder substitution (not regex), matching the pattern at `docs_fixer.py:236-244`.

- [ ] **Step 5: Run tests**

Run: `uv run python -m pytest tests/test_math_validator.py -v`
Expected: all pass

- [ ] **Step 6: Run typecheck**

Run: `make typecheck`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add codewiki/src/be/postprocess/katex_check.js codewiki/src/be/postprocess/math_validator.py \
  tests/test_math_validator.py
git commit -m "feat(postprocess): add math_validator with pylatexenc + KaTeX + batch LLM repair"
```

---

### Task 3: Mermaid validator — cleanup rules + batch repair

**Files:**
- Create: `codewiki/src/be/postprocess/mermaid_validator.py`
- Create: `tests/test_mermaid_validator.py`

- [ ] **Step 1: Write failing tests for mermaid_validator**

Create `tests/test_mermaid_validator.py`:

```python
import subprocess
from unittest.mock import MagicMock, patch

import pytest


class TestCleanupMermaid:
    def test_smart_quotes_replaced(self):
        from codewiki.src.be.postprocess.mermaid_validator import cleanup_mermaid

        result = cleanup_mermaid('\u201cHello\u201d')
        assert "\u201c" not in result
        assert '"' in result

    def test_escaped_newline_in_label(self):
        from codewiki.src.be.postprocess.mermaid_validator import cleanup_mermaid

        result = cleanup_mermaid('A["line1\\nline2"]')
        assert "<br/>" in result
        assert "\\n" not in result

    def test_edge_label_repair(self):
        from codewiki.src.be.postprocess.mermaid_validator import cleanup_mermaid

        result = cleanup_mermaid("A -->[label] B")
        assert "-->|label|" in result

    def test_unbalanced_bracket_closed(self):
        from codewiki.src.be.postprocess.mermaid_validator import cleanup_mermaid

        result = cleanup_mermaid("A[open label")
        assert result.count("[") == result.count("]")

    def test_multi_source_expanded(self):
        from codewiki.src.be.postprocess.mermaid_validator import cleanup_mermaid

        result = cleanup_mermaid("A & B --> C")
        assert "A --> C" in result
        assert "B --> C" in result


class TestExtractMermaidSpans:
    def test_extracts_mermaid_blocks(self):
        from codewiki.src.be.postprocess.mermaid_validator import extract_mermaid_spans

        text = "```mermaid\ngraph TD\nA-->B\n```"
        spans = extract_mermaid_spans(text)
        assert len(spans) == 1
        assert "graph TD" in spans[0].content

    def test_no_match_in_other_fences(self):
        from codewiki.src.be.postprocess.mermaid_validator import extract_mermaid_spans

        text = "```python\nprint('hello')\n```"
        assert extract_mermaid_spans(text) == []


class TestValidateMermaid:
    def test_mmdc_valid_returns_none(self):
        from codewiki.src.be.postprocess.mermaid_validator import validate_with_mmdc

        proc = MagicMock(returncode=0)
        with (
            patch("codewiki.src.be.postprocess.mermaid_validator._find_mmdc", return_value="/usr/bin/mmdc"),
            patch("codewiki.src.be.postprocess.mermaid_validator.subprocess.run", return_value=proc),
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.stat", return_value=MagicMock(st_size=100)),
        ):
            assert validate_with_mmdc("graph TD\nA-->B") is None

    def test_mmdc_timeout_returns_error(self):
        from codewiki.src.be.postprocess.mermaid_validator import validate_with_mmdc

        with (
            patch("codewiki.src.be.postprocess.mermaid_validator._find_mmdc", return_value="/usr/bin/mmdc"),
            patch(
                "codewiki.src.be.postprocess.mermaid_validator.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd=["mmdc"], timeout=30),
            ),
        ):
            assert validate_with_mmdc("graph TD\nA-->B") == "mmdc timed out"

    def test_regex_detects_bad_unicode(self):
        from codewiki.src.be.postprocess.mermaid_validator import validate_with_regex

        issues = validate_with_regex("A[∃x ∈ S] --> B")
        assert any("Unicode" in i for i in issues)


class TestBuildRepairPrompt:
    def test_prompt_contains_mermaid_constraints(self):
        from codewiki.src.be.postprocess.mermaid_validator import (
            MermaidIssue,
            MermaidSpan,
            build_repair_prompt,
        )

        span = MermaidSpan(start=0, end=20, content="graph TD\nA[bad", line=1)
        issue = MermaidIssue(issue_id="a:0", span=span, errors=["unbalanced"])
        prompt = build_repair_prompt([issue])
        assert "graph TD" in prompt
        assert '"id"' in prompt


class TestParseRepairResponse:
    def test_parses_valid_json(self):
        from codewiki.src.be.postprocess.mermaid_validator import parse_repair_response

        response = '{"items": [{"id": "a:0", "mermaid": "graph TD\\nA-->B"}]}'
        result = parse_repair_response(response)
        assert "a:0" in result

    def test_returns_empty_on_bad_json(self):
        from codewiki.src.be.postprocess.mermaid_validator import parse_repair_response

        assert parse_repair_response("not json") == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_mermaid_validator.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement mermaid_validator.py**

Create `codewiki/src/be/postprocess/mermaid_validator.py`. The file must contain:

1. **Data types**: `MermaidSpan` (frozen dataclass), `MermaidIssue` (dataclass)
2. **`cleanup_mermaid(text)`**: ported cleanup rules — smart quotes, `\n` expansion, subgraph normalization, edge label repair, compacted statement splitting, chained edge splitting, unbalanced bracket closure, HTML label wrapping, cylinder label normalization, multi-source edge expansion, subgraph ID deduplication
3. **`_find_mmdc()`**: moved from `docs_fixer.py` (global cached lookup)
4. **`validate_with_mmdc(mmd_text)`**: moved from `docs_fixer.py`, returns `None` or error string
5. **`validate_with_regex(content)`**: moved from `docs_fixer.py`, returns list of issue strings
6. **`extract_mermaid_spans(text)`**: regex extraction
7. **`build_repair_prompt(issues)`**: JSON prompt with Mermaid safe-subset system instructions
8. **`parse_repair_response(response)`**: JSON parse → `dict[str, str]`
9. **`repair_batch_sync(issues, config, pp_config, usage_stats)`**: same model chain pattern as math
10. **`fix_mermaid_in_text(text, config, stats, usage_stats, report, filename)`**: full pipeline — extract → validate (mmdc or regex) → cleanup → collect issues → batch repair → apply replacements → degrade failures

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/test_mermaid_validator.py -v`
Expected: all pass

- [ ] **Step 5: Run typecheck**

Run: `make typecheck`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add codewiki/src/be/postprocess/mermaid_validator.py tests/test_mermaid_validator.py
git commit -m "feat(postprocess): add mermaid_validator with cleanup rules + batch LLM repair"
```

---

### Task 4: Wire new modules into docs_fixer.py

**Files:**
- Modify: `codewiki/src/be/docs_fixer.py`
- Modify: `tests/test_docs_fixer_helpers.py`

- [ ] **Step 1: Write integration tests**

These tests are written AFTER the wiring is done (step 3) because they patch the new import paths which only exist once `docs_fixer.py` imports from the new modules. Add to `tests/test_docs_fixer_helpers.py`:

```python
def test_fix_docs_uses_new_math_validator(tmp_path):
    from unittest.mock import patch

    from codewiki.src.be.docs_fixer import fix_docs
    from codewiki.src.codewiki_config import CodeWikiConfig, PostprocessConfig

    (tmp_path / "test.md").write_text("$$\\frac{1}{2}$$", encoding="utf-8")
    config = CodeWikiConfig(
        repo_path=str(tmp_path),
        docs_dir=str(tmp_path),
        postprocess=PostprocessConfig(fix_links=False),
    )

    with patch("codewiki.src.be.docs_fixer.fix_math", return_value="$$\\frac{1}{2}$$") as mock_math:
        fix_docs(str(tmp_path), config)
        mock_math.assert_called()


def test_fix_docs_uses_new_mermaid_validator(tmp_path):
    from unittest.mock import patch

    from codewiki.src.be.docs_fixer import fix_docs
    from codewiki.src.codewiki_config import CodeWikiConfig, PostprocessConfig

    (tmp_path / "test.md").write_text("```mermaid\ngraph TD\nA-->B\n```", encoding="utf-8")
    config = CodeWikiConfig(
        repo_path=str(tmp_path),
        docs_dir=str(tmp_path),
        postprocess=PostprocessConfig(fix_links=False),
    )

    with patch("codewiki.src.be.docs_fixer.fix_mermaid", return_value="```mermaid\ngraph TD\nA-->B\n```") as mock_mermaid:
        fix_docs(str(tmp_path), config)
        mock_mermaid.assert_called()
```

Note: these patch `codewiki.src.be.docs_fixer.fix_math` and `codewiki.src.be.docs_fixer.fix_mermaid` — the names as imported in `docs_fixer.py` (via `from ... import fix_math_in_text as fix_math`). This avoids the problem of patching a module that doesn't exist yet at test collection time.

- [ ] **Step 3: Gut old math/mermaid code from docs_fixer.py**

Delete from `codewiki/src/be/docs_fixer.py`:
- All math-related code: `_MATH_DISPLAY_RE`, `_MATH_INLINE_RE`, `_MATH_BK_DISP_RE`, `_MATH_BK_INLN_RE`, `_MATH_ENV_RE`, `_CODE_FENCE_RE`, `_validate_math`, `_MATH_REPAIR_USER`, `_llm_repair_math`, `_fix_math_in_text`
- All mermaid-related code: `_MERMAID_BLOCK_RE`, `_MERMAID_BAD_UNICODE_RE`, `_MERMAID_SINGLE_QUOTE_RE`, `_MMDC_PATH`, `_MMDC_CHECKED`, `_find_mmdc`, `_validate_with_mmdc`, `_has_unquoted_nonascii`, `_validate_with_regex`, `_REPAIR_USER`, `_llm_repair`, `_fix_mermaid_in_text`
- Remove `import shutil`, `import subprocess`, `import tempfile` if they become unused
- Remove `from codewiki.src.be.llm_services import call_llm` and `from codewiki.src.be.llm_retry import with_retry_sync` if they become unused

Replace Phase 2+3 in `fix_docs()` with:

```python
from codewiki.src.be.postprocess.math_validator import fix_math_in_text as fix_math
from codewiki.src.be.postprocess.mermaid_validator import fix_mermaid_in_text as fix_mermaid

# Phase 2 — Math repair
text = fix_math(text, config, stats, usage_stats, report=report, filename=md_file.name)

# Phase 3 — Mermaid repair
text = fix_mermaid(text, config, stats, usage_stats, report=report, filename=md_file.name)
```

Keep `fix_mermaid_in_file()` as a thin wrapper calling the new module:

```python
def fix_mermaid_in_file(
    path: Path,
    config: CodeWikiConfig,
    stats: FixStats,
    usage_stats: LLMUsageStats | None = None,
) -> bool:
    from codewiki.src.be.postprocess.mermaid_validator import fix_mermaid_in_text

    text = path.read_text(encoding="utf-8")
    new_text = fix_mermaid_in_text(text, config, stats, usage_stats, report=None, filename=path.name)
    if new_text == text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True
```

- [ ] **Step 4: Update existing tests to match new module paths**

In `tests/test_docs_fixer_helpers.py`:
- `test_fix_math_in_text_degrades_when_repair_is_unchanged`: change import to `from codewiki.src.be.postprocess.math_validator import fix_math_in_text` and update mock patches to target `codewiki.src.be.postprocess.math_validator.*`
- `test_fix_mermaid_in_text_degrades_when_repair_is_unchanged`: change import to `from codewiki.src.be.postprocess.mermaid_validator import fix_mermaid_in_text` and update mock patches
- `test_validate_with_mmdc_*`: change imports to `from codewiki.src.be.postprocess.mermaid_validator import validate_with_mmdc` and update mock target paths
- `test_fix_docs_strict_mode_raises_lint_error`: update mock target for `_fix_mermaid_in_text` to `codewiki.src.be.postprocess.mermaid_validator.fix_mermaid_in_text`

- [ ] **Step 5: Run all tests**

Run: `uv run python -m pytest tests/ -q`
Expected: all pass

- [ ] **Step 6: Run typecheck**

Run: `make typecheck`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add codewiki/src/be/docs_fixer.py tests/test_docs_fixer_helpers.py
git commit -m "refactor(postprocess): wire math_validator and mermaid_validator into docs_fixer"
```

---

### Task 5: Full regression + documentation

**Files:**
- Modify: `README.md`
- Modify: `README_ZH.md`

- [ ] **Step 1: Run full test suite**

Run: `uv run python -m pytest tests/ -q`
Expected: all pass

- [ ] **Step 2: Run typecheck**

Run: `make typecheck`
Expected: PASS

- [ ] **Step 3: Update README.md**

Three locations must be updated:

1. **"Post Layer" details section** (around line 219): replace `Config.postprocess_strict = True raises LintError` with `config.postprocess.strict = true raises LintError`. Add mentions of dual-layer math validation (pylatexenc + KaTeX), deterministic cleanup rules, and batch LLM repair for both math and mermaid.
2. **"Configuration Reference" TOML example** (around line 276-317): remove old `postprocess_strict = false` from `[runtime]` section. Add full `[postprocess]` section with all 7 fields.
3. **Quick Start config example** (around line 40-51): if `postprocess_strict` appears here, remove it.

- [ ] **Step 4: Update README_ZH.md**

Same three locations as README.md but in Chinese:

1. **"后处理层" details section** (around line 219): `Config.postprocess_strict = True` → `config.postprocess.strict = true`; add validation and repair descriptions
2. **"配置参考" TOML example** (around line 273-316): remove old `postprocess_strict`, add `[postprocess]` section
3. **Quick Start config example**: same cleanup if needed

- [ ] **Step 5: Commit**

```bash
git add README.md README_ZH.md
git commit -m "docs: document enhanced postprocess repair and [postprocess] config section"
```
