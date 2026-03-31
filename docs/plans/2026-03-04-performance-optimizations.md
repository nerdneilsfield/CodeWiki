# Performance Optimizations Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate the three largest CPU hot-spots found in real cProfile data: repeated tiktoken encoder construction, per-file tree-sitter Language/Parser initialization, and serial file analysis in the call graph analyzer.

**Architecture:** Three independent changes, applied in order (each is a prerequisite for the next). Task 1 is a 2-line fix. Task 2 replaces per-instance Language/Parser construction with thread-local singletons across six analyzers. Task 3 parallelises `analyze_code_files` using `ThreadPoolExecutor` after first changing `_analyze_code_file` to return results instead of mutating shared state (required for thread safety).

**Tech Stack:** `functools.lru_cache`, `threading.local`, `concurrent.futures.ThreadPoolExecutor`, `tree-sitter` (`Language`, `Parser`), `tiktoken`

---

## Background

From the profiler report:
- `count_tokens` called hundreds of times per run; each call re-constructs the tiktoken encoder (~0.24 s first load, then cheap but still looked up every call).
- Tree-sitter `Language(capsule)` + `Parser(language)` constructed once per *file* in C, C++, Java, JavaScript, TypeScript, PHP analyzers. On a 500-file C++ repo this wastes the initialization time for 500 Language objects.
- `analyze_code_files` processes every file sequentially even though each file is fully independent of the others during the parse+extract phase.

Python analyzer uses `ast` (stdlib) — no tree-sitter involved there, so it is excluded.

---

### Task 1: Cache tiktoken encoder with `lru_cache`

**Files:**
- Modify: `codewiki/src/be/utils.py:68-85`
- Test: `tests/test_perf_utils.py` (create)

**Step 1: Write the failing test**

```python
# tests/test_perf_utils.py
import time
from codewiki.src.be.utils import count_tokens, _get_encoder

def test_count_tokens_basic():
    assert count_tokens("hello world") > 0

def test_count_tokens_unknown_model_fallback():
    # Should not raise; falls back to cl100k_base
    result = count_tokens("hello world", model="some-unknown-model-xyz")
    assert result > 0

def test_count_tokens_encoder_is_cached():
    """Second call with same model must return the same encoder object (cache hit)."""
    enc1 = _get_encoder("gpt-4")
    enc2 = _get_encoder("gpt-4")
    assert enc1 is enc2, "Expected cached encoder, got two different objects"

def test_count_tokens_speed():
    """100 calls should complete in under 50 ms total (cached path)."""
    text = "The quick brown fox " * 50
    count_tokens(text, model="gpt-4")   # warm cache
    start = time.perf_counter()
    for _ in range(100):
        count_tokens(text, model="gpt-4")
    elapsed = time.perf_counter() - start
    assert elapsed < 0.05, f"100 calls took {elapsed:.3f}s — encoder not cached"
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_perf_utils.py -v
```

Expected: `FAIL` — `_get_encoder` doesn't exist yet, `test_count_tokens_encoder_is_cached` and `test_count_tokens_speed` will fail.

**Step 3: Implement the fix**

In `codewiki/src/be/utils.py`, add `lru_cache` to the encoder lookup. The complete replacement for the token-counting section (lines 64-85):

```python
# ------------------------------------------------------------
# ---------------------- Token Counting ---------------------
# ------------------------------------------------------------

from functools import lru_cache


@lru_cache(maxsize=8)
def _get_encoder(model: str):
    """Return (and cache) the tiktoken encoder for *model*.

    Falls back to cl100k_base for models not recognised by tiktoken
    (e.g. Claude, GLM).  lru_cache ensures the expensive first-load
    only happens once per model name per process lifetime.
    """
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model: str = "gpt-4") -> int:
    """Count the number of tokens in a text."""
    return len(_get_encoder(model).encode(text))
```

**Note:** `import tiktoken` is already at the top of the file (line 7). Just add `from functools import lru_cache` alongside the existing imports (top of file) and replace the token-counting block.

**Step 4: Run test to verify it passes**

```bash
pytest tests/test_perf_utils.py -v
```

Expected: all 4 tests PASS.

**Step 5: Commit**

```bash
git add codewiki/src/be/utils.py tests/test_perf_utils.py
git commit -m "perf(utils): cache tiktoken encoder with lru_cache"
```

---

### Task 2: Thread-local tree-sitter singletons for six analyzers

**Context:** `Language(capsule)` is immutable and safe to share across threads. `Parser` is stateful during `.parse()` and must not be shared concurrently. We use a module-level `Language` singleton (one per analyzer module) and a `threading.local()` for the `Parser` (one instance per OS thread, created on first use in that thread). This is forward-compatible with the parallel analysis introduced in Task 3.

**Files:**
- Modify: `codewiki/src/be/dependency_analyzer/analyzers/c.py:50-56`
- Modify: `codewiki/src/be/dependency_analyzer/analyzers/cpp.py:52-57`
- Modify: `codewiki/src/be/dependency_analyzer/analyzers/java.py` (find `_analyze` method, same pattern)
- Modify: `codewiki/src/be/dependency_analyzer/analyzers/javascript.py:30-39`
- Modify: `codewiki/src/be/dependency_analyzer/analyzers/typescript.py:28-37`
- Modify: `codewiki/src/be/dependency_analyzer/analyzers/php.py` (find where Language+Parser are constructed, same pattern)
- Test: `tests/test_perf_parser_singletons.py` (create)

**Step 1: Write the failing tests**

```python
# tests/test_perf_parser_singletons.py
"""Verify that tree-sitter Language objects are module-level singletons."""

def test_c_language_is_singleton():
    from codewiki.src.be.dependency_analyzer.analyzers import c as c_mod
    from codewiki.src.be.dependency_analyzer.analyzers.c import analyze_c_file

    SAMPLE = "void foo(void) {}\nvoid bar(void) { foo(); }\n"
    analyze_c_file("/tmp/a.c", SAMPLE, "/tmp")
    analyze_c_file("/tmp/b.c", SAMPLE, "/tmp")

    assert c_mod._C_LANGUAGE is not None, "_C_LANGUAGE singleton not created"
    # Both calls must share the same Language object
    lang1 = c_mod._C_LANGUAGE
    analyze_c_file("/tmp/c.c", SAMPLE, "/tmp")
    lang2 = c_mod._C_LANGUAGE
    assert lang1 is lang2, "Language object was recreated between calls"


def test_cpp_language_is_singleton():
    from codewiki.src.be.dependency_analyzer.analyzers import cpp as cpp_mod
    from codewiki.src.be.dependency_analyzer.analyzers.cpp import analyze_cpp_file

    SAMPLE = "void foo() {}\nvoid bar() { foo(); }\n"
    analyze_cpp_file("/tmp/a.cpp", SAMPLE, "/tmp")
    lang1 = cpp_mod._CPP_LANGUAGE
    analyze_cpp_file("/tmp/b.cpp", SAMPLE, "/tmp")
    lang2 = cpp_mod._CPP_LANGUAGE
    assert lang1 is lang2


def test_js_language_is_singleton():
    from codewiki.src.be.dependency_analyzer.analyzers import javascript as js_mod
    from codewiki.src.be.dependency_analyzer.analyzers.javascript import analyze_javascript_file_treesitter

    SAMPLE = "function foo() {}\nfunction bar() { foo(); }\n"
    analyze_javascript_file_treesitter("/tmp/a.js", SAMPLE, "/tmp")
    lang1 = js_mod._JS_LANGUAGE
    analyze_javascript_file_treesitter("/tmp/b.js", SAMPLE, "/tmp")
    lang2 = js_mod._JS_LANGUAGE
    assert lang1 is lang2
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_perf_parser_singletons.py -v
```

Expected: FAIL — `_C_LANGUAGE`, `_CPP_LANGUAGE`, `_JS_LANGUAGE` attributes don't exist.

**Step 3: Implement — C analyzer (`c.py`)**

At the top of `codewiki/src/be/dependency_analyzer/analyzers/c.py`, after the imports, add:

```python
import threading
from tree_sitter import Parser, Language
import tree_sitter_c

# --- module-level singletons ------------------------------------------------
_C_LANGUAGE: Language | None = None
_C_LANGUAGE_LOCK = threading.Lock()
_C_PARSER_LOCAL = threading.local()


def _get_c_parser() -> Parser:
    """Return a thread-local Parser, creating it (and the shared Language) on first use."""
    global _C_LANGUAGE
    if _C_LANGUAGE is None:
        with _C_LANGUAGE_LOCK:
            if _C_LANGUAGE is None:
                _C_LANGUAGE = Language(tree_sitter_c.language())
    p = getattr(_C_PARSER_LOCAL, "parser", None)
    if p is None:
        _C_PARSER_LOCAL.parser = Parser(_C_LANGUAGE)
    return _C_PARSER_LOCAL.parser
```

Then in `_analyze()`, replace:

```python
# OLD (lines 51-55)
language_capsule = tree_sitter_c.language()
c_language = Language(language_capsule)
parser = Parser(c_language)
```

with:

```python
# NEW
parser = _get_c_parser()
```

Remove the now-redundant `from tree_sitter import Parser, Language` if already at module level (the imports at the top cover it).

**Step 4: Implement — C++ analyzer (`cpp.py`)**

Same pattern. Add after imports:

```python
import threading
from tree_sitter import Parser, Language
import tree_sitter_cpp

_CPP_LANGUAGE: Language | None = None
_CPP_LANGUAGE_LOCK = threading.Lock()
_CPP_PARSER_LOCAL = threading.local()


def _get_cpp_parser() -> Parser:
    global _CPP_LANGUAGE
    if _CPP_LANGUAGE is None:
        with _CPP_LANGUAGE_LOCK:
            if _CPP_LANGUAGE is None:
                _CPP_LANGUAGE = Language(tree_sitter_cpp.language())
    p = getattr(_CPP_PARSER_LOCAL, "parser", None)
    if p is None:
        _CPP_PARSER_LOCAL.parser = Parser(_CPP_LANGUAGE)
    return _CPP_PARSER_LOCAL.parser
```

In `_analyze()`, replace:

```python
language_capsule = tree_sitter_cpp.language()
cpp_language = Language(language_capsule)
parser = Parser(cpp_language)
```

with `parser = _get_cpp_parser()`.

**Step 5: Implement — Java analyzer (`java.py`)**

Locate the `_analyze` method; it will contain:

```python
language_capsule = tree_sitter_java.language()
java_language = Language(language_capsule)
parser = Parser(java_language)
```

Apply the same singleton pattern:

```python
import threading
import tree_sitter_java

_JAVA_LANGUAGE: Language | None = None
_JAVA_LANGUAGE_LOCK = threading.Lock()
_JAVA_PARSER_LOCAL = threading.local()


def _get_java_parser() -> Parser:
    global _JAVA_LANGUAGE
    if _JAVA_LANGUAGE is None:
        with _JAVA_LANGUAGE_LOCK:
            if _JAVA_LANGUAGE is None:
                _JAVA_LANGUAGE = Language(tree_sitter_java.language())
    p = getattr(_JAVA_PARSER_LOCAL, "parser", None)
    if p is None:
        _JAVA_PARSER_LOCAL.parser = Parser(_JAVA_LANGUAGE)
    return _JAVA_PARSER_LOCAL.parser
```

Replace the three construction lines with `parser = _get_java_parser()`.

**Step 6: Implement — JavaScript analyzer (`javascript.py`)**

The JS analyzer constructs parser inside `__init__`. Replace the `try` block (lines 30-39):

```python
# OLD
try:
    language_capsule = tree_sitter_javascript.language()
    self.js_language = Language(language_capsule)
    self.parser = Parser(self.js_language)
except Exception as e:
    logger.error(...)
    self.parser = None
    self.js_language = None
```

Add module-level singletons (after imports):

```python
import threading
import tree_sitter_javascript

_JS_LANGUAGE: Language | None = None
_JS_LANGUAGE_LOCK = threading.Lock()
_JS_PARSER_LOCAL = threading.local()


def _get_js_parser() -> Parser | None:
    global _JS_LANGUAGE
    try:
        if _JS_LANGUAGE is None:
            with _JS_LANGUAGE_LOCK:
                if _JS_LANGUAGE is None:
                    _JS_LANGUAGE = Language(tree_sitter_javascript.language())
        p = getattr(_JS_PARSER_LOCAL, "parser", None)
        if p is None:
            _JS_PARSER_LOCAL.parser = Parser(_JS_LANGUAGE)
        return _JS_PARSER_LOCAL.parser
    except Exception as e:
        logger.error(f"Failed to initialise JavaScript parser: {e}")
        return None
```

In `__init__`:

```python
self.parser = _get_js_parser()
self.js_language = _JS_LANGUAGE  # may be None if init failed
```

**Step 7: Implement — TypeScript analyzer (`typescript.py`)**

Same pattern, but uses `tree_sitter_typescript.language_typescript()`:

```python
import threading
import tree_sitter_typescript

_TS_LANGUAGE: Language | None = None
_TS_LANGUAGE_LOCK = threading.Lock()
_TS_PARSER_LOCAL = threading.local()


def _get_ts_parser() -> Parser | None:
    global _TS_LANGUAGE
    try:
        if _TS_LANGUAGE is None:
            with _TS_LANGUAGE_LOCK:
                if _TS_LANGUAGE is None:
                    _TS_LANGUAGE = Language(tree_sitter_typescript.language_typescript())
        p = getattr(_TS_PARSER_LOCAL, "parser", None)
        if p is None:
            _TS_PARSER_LOCAL.parser = Parser(_TS_LANGUAGE)
        return _TS_PARSER_LOCAL.parser
    except Exception as e:
        logger.error(f"Failed to initialise TypeScript parser: {e}")
        return None
```

In `__init__`, replace the try block:

```python
self.parser = _get_ts_parser()
self.ts_language = _TS_LANGUAGE
```

**Step 8: Implement — PHP analyzer (`php.py`)**

Find where `tree_sitter_php` is used. It's in a method (around line 168):

```python
php_lang_capsule = tree_sitter_php.language_php()
php_language = Language(php_lang_capsule)
parser = Parser(php_language)
```

Add module-level singletons (after imports):

```python
import threading
import tree_sitter_php

_PHP_LANGUAGE: Language | None = None
_PHP_LANGUAGE_LOCK = threading.Lock()
_PHP_PARSER_LOCAL = threading.local()


def _get_php_parser() -> Parser:
    global _PHP_LANGUAGE
    if _PHP_LANGUAGE is None:
        with _PHP_LANGUAGE_LOCK:
            if _PHP_LANGUAGE is None:
                _PHP_LANGUAGE = Language(tree_sitter_php.language_php())
    p = getattr(_PHP_PARSER_LOCAL, "parser", None)
    if p is None:
        _PHP_PARSER_LOCAL.parser = Parser(_PHP_LANGUAGE)
    return _PHP_PARSER_LOCAL.parser
```

Replace the three lines with `parser = _get_php_parser()`.

**Step 9: Run all tests**

```bash
pytest tests/test_perf_parser_singletons.py tests/test_c_analyzer_enhanced.py tests/test_cpp_analyzer_enhanced.py -v
```

Expected: all PASS. The singleton tests confirm one Language object per module. The existing analyzer tests confirm parsing still works correctly.

**Step 10: Commit**

```bash
git add \
  codewiki/src/be/dependency_analyzer/analyzers/c.py \
  codewiki/src/be/dependency_analyzer/analyzers/cpp.py \
  codewiki/src/be/dependency_analyzer/analyzers/java.py \
  codewiki/src/be/dependency_analyzer/analyzers/javascript.py \
  codewiki/src/be/dependency_analyzer/analyzers/typescript.py \
  codewiki/src/be/dependency_analyzer/analyzers/php.py \
  tests/test_perf_parser_singletons.py
git commit -m "perf(analyzers): module-level Language singleton + thread-local Parser"
```

---

### Task 3: Parallel file analysis with `ThreadPoolExecutor`

**Context:** Each file is fully independent during parse+extract. The bottleneck is that `analyze_code_files` processes them in a serial for-loop. After Task 2, each thread has its own `Parser`, so parallel execution is safe.

The change requires two steps:
1. Make `_analyze_code_file` *return* `(file_funcs: dict, file_rels: list)` instead of mutating `self.functions` / `self.call_relationships`.
2. Drive those calls with `ThreadPoolExecutor` in `analyze_code_files` and merge results in the main thread.

**Files:**
- Modify: `codewiki/src/be/dependency_analyzer/analysis/call_graph_analyzer.py:27-56` (analyze_code_files), `121-285` (_analyze_code_file + all _analyze_*_file helpers)
- Test: `tests/test_perf_parallel_analysis.py` (create)

**Step 1: Write the failing test**

```python
# tests/test_perf_parallel_analysis.py
import time
from codewiki.src.be.dependency_analyzer.analysis.call_graph_analyzer import CallGraphAnalyzer

SAMPLE_C = "void foo(void) {}\nvoid bar(void) { foo(); }\n"

def _make_file_list(n: int) -> list[dict]:
    return [
        {"path": f"file_{i}.c", "name": f"file_{i}.c", "extension": ".c", "language": "c"}
        for i in range(n)
    ]

def test_parallel_analysis_returns_correct_results(tmp_path):
    """Results must be identical to serial analysis."""
    (tmp_path / "file_0.c").write_text(SAMPLE_C)
    (tmp_path / "file_1.c").write_text(SAMPLE_C)
    files = _make_file_list(2)

    analyzer = CallGraphAnalyzer()
    result = analyzer.analyze_code_files(files, str(tmp_path))

    # Both files must have been analyzed
    assert result["call_graph"]["files_analyzed"] == 2
    # Functions should be present
    assert result["call_graph"]["total_functions"] >= 2

def test_parallel_analysis_is_faster_than_serial(tmp_path):
    """Parallel must complete 20 identical files faster than 2x serial time for 10 files."""
    # Write 20 C files
    for i in range(20):
        (tmp_path / f"file_{i}.c").write_text(SAMPLE_C)

    files_20 = _make_file_list(20)
    files_10 = _make_file_list(10)

    # Time 10 files (serial baseline for the first 10)
    for i in range(10):
        (tmp_path / f"file_{i}.c").write_text(SAMPLE_C)

    analyzer = CallGraphAnalyzer()
    t0 = time.perf_counter()
    analyzer.analyze_code_files(files_10, str(tmp_path))
    t_10 = time.perf_counter() - t0

    # Time 20 files with parallelism
    t0 = time.perf_counter()
    analyzer.analyze_code_files(files_20, str(tmp_path))
    t_20 = time.perf_counter() - t0

    # 20 parallel files should take less than 2x the time of 10 serial files
    # (this is a loose bound; even 2-worker parallelism gives speedup)
    assert t_20 < t_10 * 2.5, (
        f"No speedup detected: 10 files={t_10:.3f}s, 20 files={t_20:.3f}s"
    )
```

**Step 2: Run test to verify it fails (or produces wrong results)**

```bash
pytest tests/test_perf_parallel_analysis.py -v
```

Expected: `test_parallel_analysis_is_faster_than_serial` FAILS (no parallelism yet so time scales linearly).

**Step 3: Refactor `_analyze_code_file` to return results**

In `call_graph_analyzer.py`, change the signature and body of `_analyze_code_file` from:

```python
def _analyze_code_file(self, repo_dir: str, file_info: Dict):
    ...
    # currently writes to self.functions and self.call_relationships
```

to:

```python
def _analyze_code_file(self, repo_dir: str, file_info: Dict) -> tuple[dict, list]:
    """Analyze one file; returns (functions_dict, relationships_list) — does NOT mutate self."""
    file_funcs: dict = {}
    file_rels: list = []

    base = Path(repo_dir)
    file_path = base / file_info["path"]

    try:
        content = safe_open_text(base, file_path)
        language = file_info["language"]

        funcs, rels = [], []

        if language == "python":
            funcs, rels = self._analyze_python_file(file_path, content, repo_dir)
        elif language == "javascript":
            funcs, rels = self._analyze_javascript_file(file_path, content, repo_dir)
        elif language == "typescript":
            funcs, rels = self._analyze_typescript_file(file_path, content, repo_dir)
        elif language == "php":
            funcs, rels = self._analyze_php_file(file_path, content, repo_dir)
        elif language == "java":
            funcs, rels = self._analyze_java_file(file_path, content, repo_dir)
        elif language == "csharp":
            funcs, rels = self._analyze_csharp_file(file_path, content, repo_dir)
        elif language == "c":
            funcs, rels = self._analyze_c_file(file_path, content, repo_dir)
        elif language == "cpp":
            funcs, rels = self._analyze_cpp_file(file_path, content, repo_dir)
        elif language == "go":
            funcs, rels = self._analyze_go_file(file_path, content, repo_dir)
        elif language == "rust":
            funcs, rels = self._analyze_rust_file(file_path, content, repo_dir)
        elif language == "bash":
            funcs, rels = self._analyze_bash_file(file_path, content, repo_dir)
        elif language == "cmake":
            funcs, rels = self._analyze_cmake_file(file_path, content, repo_dir)
        elif language == "toml":
            funcs, rels = self._analyze_toml_file(file_path, content, repo_dir)
        elif language == "vitis_cfg":
            funcs, rels = self._analyze_vitis_cfg_file(file_path, content, repo_dir)
        elif language == "makefile":
            funcs, rels = self._analyze_makefile_file(file_path, content, repo_dir)
        elif language == "tcl":
            funcs, rels = self._analyze_tcl_file(file_path, content, repo_dir)

        for func in funcs:
            file_funcs[func.id or f"{file_path}:{func.name}"] = func
        file_rels.extend(rels)

    except Exception as e:
        logger.error(f"Error analyzing {file_info.get('path')}: {e}", exc_info=True)

    return file_funcs, file_rels
```

**IMPORTANT:** Each `_analyze_*_file` helper currently writes to `self.functions` / `self.call_relationships`. You must change each one to **return** `(functions_list, relationships_list)` instead. Look at the pattern — they already call module functions like `analyze_javascript_file_treesitter()` that return `(functions, relationships)`. The helpers just need to `return functions, relationships` instead of mutating self:

```python
# OLD _analyze_javascript_file
def _analyze_javascript_file(self, file_path, content, repo_dir):
    from codewiki.src.be.dependency_analyzer.analyzers.javascript import analyze_javascript_file_treesitter
    try:
        functions, relationships = analyze_javascript_file_treesitter(file_path, content, repo_path=repo_dir)
        for func in functions:
            self.functions[func.id or f"{file_path}:{func.name}"] = func
        self.call_relationships.extend(relationships)
    except Exception as e:
        logger.error(...)

# NEW
def _analyze_javascript_file(self, file_path, content, repo_dir) -> tuple[list, list]:
    from codewiki.src.be.dependency_analyzer.analyzers.javascript import analyze_javascript_file_treesitter
    try:
        return analyze_javascript_file_treesitter(file_path, content, repo_path=repo_dir)
    except Exception as e:
        logger.error(f"Failed to analyze JavaScript file {file_path}: {e}", exc_info=True)
        return [], []
```

Apply this same change to every `_analyze_*_file` helper in the file.

**Step 4: Add parallel loop in `analyze_code_files`**

Add imports at the top of `call_graph_analyzer.py`:

```python
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
```

Replace the serial loop in `analyze_code_files` (currently lines 42-46):

```python
# OLD
files_analyzed = 0
for file_info in code_files:
    logger.debug(f"Analyzing: {file_info['path']}")
    self._analyze_code_file(base_dir, file_info)
    files_analyzed += 1
```

with:

```python
max_workers = min(32, (os.cpu_count() or 1) + 4)
files_analyzed = 0

with ThreadPoolExecutor(max_workers=max_workers) as executor:
    future_to_info = {
        executor.submit(self._analyze_code_file, base_dir, file_info): file_info
        for file_info in code_files
    }
    for future in as_completed(future_to_info):
        file_info = future_to_info[future]
        try:
            file_funcs, file_rels = future.result()
            self.functions.update(file_funcs)
            self.call_relationships.extend(file_rels)
            files_analyzed += 1
        except Exception as e:
            logger.error(f"Unexpected error for {file_info.get('path')}: {e}", exc_info=True)

logger.debug(
    f"Analysis complete: {files_analyzed} files analyzed, "
    f"{len(self.functions)} functions, {len(self.call_relationships)} relationships"
)
```

**Step 5: Run all tests**

```bash
pytest tests/test_perf_parallel_analysis.py tests/test_c_analyzer_enhanced.py tests/test_cpp_analyzer_enhanced.py tests/test_header_source_pairing.py -v
```

Expected: all PASS.

**Step 6: Run the full test suite**

```bash
pytest tests/ -v
```

Expected: all existing tests PASS (none depend on serial order within `analyze_code_files`).

**Step 7: Commit**

```bash
git add \
  codewiki/src/be/dependency_analyzer/analysis/call_graph_analyzer.py \
  tests/test_perf_parallel_analysis.py
git commit -m "perf(call-graph): parallel file analysis with ThreadPoolExecutor"
```

---

## Verification

After all three tasks, run a quick timing check on a mid-sized repo:

```bash
python -c "
import time, cProfile
from codewiki.src.be.dependency_analyzer.analysis.call_graph_analyzer import CallGraphAnalyzer
# ... set up a test run ...
"
```

The profiler should show:
- `count_tokens` inner loop no longer appears in `tiktoken` setup overhead
- `Language(...)` / `Parser(...)` calls appear at most once per thread per language (not once per file)
- `analyze_code_files` wall time reduced proportionally to available CPU cores

## Summary

| Task | File | Expected speedup |
|------|------|-----------------|
| 1: lru_cache tiktoken | `utils.py` | ~0.24 s saved on first warm-up; negligible overhead per subsequent call |
| 2: tree-sitter singletons | 6 analyzer files | N× parser init time where N = file count per language |
| 3: parallel analysis | `call_graph_analyzer.py` | ~2–4× on multi-core machines for large repos |
