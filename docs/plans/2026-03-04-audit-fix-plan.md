# CodeWiki Audit Fix Plan

Date: 2026-03-04
Status: Draft — reviewed by Codex, updated with additional findings

## Background

A full-repository static audit was performed on the CodeWiki codebase. The audit
identified 22 issues across security, functionality, dependency-analysis correctness,
and performance. Each claim was independently verified against the actual source code.

**Verification summary:** 17 confirmed, 3 partially true, 2 false.

An independent Codex review identified 19 items (2 critical, 7 high, 10 medium).
Most overlap with the original audit. Two new findings were accepted and added below.

---

## Verified Issue Registry

### Category A — Security (5 real, 1 partial)

| ID | File:Line | Severity | Issue | Verified? |
|----|-----------|----------|-------|-----------|
| A1 | `codewiki/src/fe/routes.py:243` | Critical | Path traversal: `docs_path / filename` has zero boundary validation — no `resolve()`, no `is_relative_to()`. `../` directly reads arbitrary files. | REAL |
| A2 | `codewiki/src/fe/visualise_docs.py:212` | Medium | `startswith()` path check can be spoofed by sibling directories with matching prefix (e.g. `/docs2` passes check for `/docs`). Already has `resolve()` so `../` is blocked. | PARTIAL |
| A3 | `codewiki/src/be/agent_tools/str_replace_editor.py:775,447` | High | `validate_path()` checks existence and absoluteness but NOT containment within docs/repo root. Path can escape to arbitrary locations. | REAL |
| A4 | `codewiki/src/be/agent_tools/str_replace_editor.py:215,487` | High | `shell=True` with string interpolation (`cmd.format(file_path=...)` and `rf"find {path} ..."`). Shell metacharacters in paths execute arbitrary commands. | REAL |
| A5 | `codewiki/src/fe/routes.py:135,276` | Medium | `format_exc()` returned in HTTP responses, exposing file paths, library versions, and internal logic. | REAL |
| A6 | `codewiki/src/be/docs_fixer.py:243` | High | `_find_mmdc()` prefers `Path.cwd()/node_modules/.bin/mmdc`. When processing untrusted repos with CWD set to repo root, a malicious binary could execute. *(New — from Codex review)* | REAL |

### Category B — Functionality & Consistency (4 real, 1 partial)

| ID | File:Line | Severity | Issue | Verified? |
|----|-----------|----------|-------|-----------|
| B1 | `codewiki/cli/commands/generate.py:418-446` | High | `CLIDocumentationGenerator()` constructor call is missing `no_cache=no_cache`. Parameter defaults to `False`, so `--no-cache` CLI flag silently does nothing. | REAL |
| B2 | `codewiki/src/fe/cache_manager.py:61` | High | Cache key = `sha256(repo_url)`. No commit dimension. Same repo at different commits returns stale cached docs. | REAL |
| B3 | `codewiki/src/fe/background_worker.py:53` | Low | Blocking `put()` on bounded queue. Only triggers when queue is full under high concurrency — not a current concern for this project's usage pattern. | PARTIAL |
| B4 | `codewiki/cli/utils/repo_validator.py:127` | Medium | `is_git_repository()` requires `.git` to be a directory. Git worktrees and some submodule configs use `.git` as a file. | REAL |
| B5 | `codewiki/cli/commands/generate.py:310` | Medium | `check_writable_output(output_dir.parent)` checks parent instead of target directory. | REAL |

### Category C — Dependency Analysis Correctness (4 real, 1 overstated, 1 false)

| ID | File:Line | Severity | Issue | Verified? |
|----|-----------|----------|-------|-----------|
| C1 | `codewiki/src/be/dependency_analyzer/analyzers/javascript.py:173` | High | Method nodes added to `top_level_nodes` but not `self.nodes`. Methods missing from returned analysis results. | REAL |
| C2 | `codewiki/src/be/dependency_analyzer/analyzers/cpp.py:212` | High | Same issue — append condition explicitly excludes `"method"` type. | REAL |
| C3 | `codewiki/src/be/dependency_analyzer/analysis/call_graph_analyzer.py:427-435` | High | Bare function names used as global lookup keys. `process()` in ClassA and ClassB both map to the same key — last one wins. | REAL |
| C4 | `codewiki/src/be/dependency_analyzer/analysis/call_graph_analyzer.py:468` | Medium | Dedup key is `(caller, callee)` only. Different relationship types (calls, inherits, uses) between same pair are collapsed. Line numbers lost. | REAL |
| C5 | `codewiki/src/be/dependency_analyzer/ast_parser.py:120` | Low | Claimed "linear scan" is actually dict lookup O(1). The fallback-to-bare-name strategy does increase mismatches on large repos, but performance is fine. | OVERSTATED |
| C6 | `codewiki/src/be/dependency_analyzer/analysis/analysis_service.py:166` | — | `"temp_dir" in locals()` guard is correct. No crash risk. | FALSE |

### Category D — Performance & Stability (4 real, 1 partial)

| ID | File:Line | Severity | Issue | Verified? |
|----|-----------|----------|-------|-----------|
| D1 | `guide_generator.py:135`, `documentation_generator.py:45` | Low | Full-file `read_bytes()` / `f.read()` for hashing. Wastes memory on large files. | REAL |
| D2 | `codewiki/cli/utils/validation.py:197` | Medium | `rglob()` called once per extension per language. N extensions = N full tree scans. | REAL |
| D3 | `codewiki/src/be/utils.py:190` | Medium | Global `sys.stderr = open(devnull)` is not thread-safe. Concurrent tasks lose stderr. | REAL |
| D4 | `codewiki/src/utils.py:50` | Low | `save_text()` does direct write, not atomic (write-to-temp + rename). Risk only on crash. | PARTIAL |
| D5 | `codewiki/src/be/documentation_generator.py:162` | Low | Mutable default `parent_path: List[str] = []`. Current code doesn't mutate it, but pattern is fragile. | REAL |

---

## Proposed Fix Plan

### Phase 0 — Security Hardening (Priority: immediate)

**Goal:** Eliminate all exploitable security vulnerabilities.

| Task | Issue | Change | Effort |
|------|-------|--------|--------|
| 0.1 | A1 | `routes.py`: After constructing `file_path`, add `file_path = file_path.resolve()` and `if not file_path.is_relative_to(docs_path.resolve()): raise 403`. | 5 min |
| 0.2 | A2 | `visualise_docs.py`: Replace `str(file_path).startswith(str(docs_folder))` with `file_path.is_relative_to(docs_folder_resolved)`. | 5 min |
| 0.3 | A4 | `str_replace_editor.py:215`: Change `subprocess.run(cmd.format(...), shell=True)` to `subprocess.run(shlex.split(cmd.format(...)))` or list form. Same for line 487: replace f-string find command with `subprocess.run(["find", str(path), "-maxdepth", "2", ...])`. | 15 min |
| 0.4 | A3 | `str_replace_editor.py:775`: After path construction, add `resolved = Path(absolute_path).resolve(); if not resolved.is_relative_to(base_path): raise ValueError(...)`. Update `validate_path()` to include containment check. | 15 min |
| 0.5 | A5 | `routes.py:135,276`: Replace `format_exc()` in HTTP responses with generic error message. Add `logger.error(msg, exc_info=True)` to keep server-side diagnostics. | 10 min |
| 0.6 | B1 | `generate.py:418`: Add `no_cache=no_cache` to CLIDocumentationGenerator constructor call. One-line fix. | 2 min |
| 0.7 | A6 | `docs_fixer.py:243`: Remove `Path.cwd()` local mmdc preference. Only use `shutil.which("mmdc")` which finds system-installed binaries on PATH. Never auto-execute repo-local binaries. | 5 min |

**Estimated total: ~1 hour of code changes + testing.**

### Phase 1 — Functional Correctness (Priority: high)

**Goal:** Fix bugs that produce wrong results for users.

| Task | Issue | Change | Effort |
|------|-------|--------|--------|
| 1.1 | B2 | `cache_manager.py`: Change `get_repo_hash()` to accept and include `commit_id` in the hash. Update callers (`routes.py`, etc.) to pass commit info. Requires checking how `CacheEntry` is structured. | 1-2 hr |
| 1.2 | C1 | `javascript.py:173`: After `self.top_level_nodes[method_key] = method_node`, add `self.nodes.append(method_node)`. Same for arrow function fields at line 181. | 10 min |
| 1.3 | C2 | `cpp.py:212`: Add `"method"` to the condition on the append line, or add a separate `self.nodes.append(node_obj)` for methods. | 10 min |
| 1.4 | C4 | `call_graph_analyzer.py:468`: Change dedup key from `(rel.caller, rel.callee)` to `(rel.caller, rel.callee, rel.relationship_type)`. | 10 min |
| 1.5 | B4 | `repo_validator.py:127`: Change `git_dir.is_dir()` to `git_dir.exists()` (accepts both file and directory forms). | 5 min |
| 1.6 | B5 | `generate.py:310`: Change `check_writable_output(output_dir.parent)` to `check_writable_output(output_dir)`. | 5 min |

**Estimated total: ~2-3 hours.**

### Phase 2 — Performance & Code Quality (Priority: when convenient)

These are real issues but have low user impact. Fix opportunistically.

| Task | Issue | Change | Effort |
|------|-------|--------|--------|
| 2.1 | D2 | `validation.py:197`: Replace per-extension `rglob()` loop with single `os.walk()` pass that classifies files by extension. | 30 min |
| 2.2 | D3 | `utils.py:190`: Replace global `sys.stderr` redirect with `subprocess.DEVNULL` or `contextlib.redirect_stderr()` scoped to the specific call. | 15 min |
| 2.3 | D1 | `guide_generator.py:135`, `documentation_generator.py:45`: Use chunked read loop (`while chunk := f.read(8192): h.update(chunk)`). | 15 min |
| 2.4 | D5 | `documentation_generator.py:162`: Change `parent_path: List[str] = []` to `parent_path: Optional[List[str]] = None` with `if parent_path is None: parent_path = []` inside. | 5 min |
| 2.5 | C3 | `call_graph_analyzer.py:427-435`: Add scope-aware resolution — prefer same-module/same-class matches before falling back to bare name lookup. This is the most complex change. | 2-4 hr |
| 2.6 | — | `prompt_template.py:1119`: Move `// ... (truncated)` marker outside the code fence, or use language-aware comment syntax. Prevents LLM from treating truncation markers as code. *(From Codex review)* | 10 min |

**Estimated total: ~4-5 hours.**

### Not Planned (rejected or deferred indefinitely)

**From original audit:**

| Issue | Reason |
|-------|--------|
| B3 (queue blocking) | Low concurrency project. Bounded queue with blocking put is fine. If needed later, trivially switch to `put_nowait()` + 503 response. |
| C6 (exception crash) | False claim. Guard clause is correct. |
| D4 (atomic write) | Risk only on process crash mid-write. Current usage (small markdown files) makes this negligible. |
| C5 (linear scan) | Overstated. Dict lookup is O(1). The mismatch issue is partially addressed by C3 scope-aware fix. |
| "Comprehensive CLI/FE test suite" | Agree testing is thin, but building a test suite is a separate initiative. Each Phase 0/1 fix should include a targeted regression test for the specific bug. |

**From Codex review (items skipped with reasoning):**

| Codex # | Item | Reason for skipping |
|---------|------|---------------------|
| 4 | Config consolidation (pydantic-settings) | Refactoring suggestion, not a bug. Current CLI/BE config split works and matches the project's layered architecture. |
| 7 | Tighten mypy strictness | Long-term code quality initiative. Not actionable in a targeted fix plan. |
| 8 | Standardize structured logging | Subjective. Current logging works for project's scale. |
| 9 | Packaging/doc drift (version, URLs) | Low-impact housekeeping. Not an audit fix. |
| 10 | Prompt context leaks code snippets | This IS the feature — caller snippets come from the user's own repo being documented. Not a leak. |
| 12 | Mermaid mmdc broken-runtime fallback | Recently built and tested. Low priority improvement. |
| 14 | AGENTS.md / CLAUDE.md | Not needed for this project's workflow. |
| 15 | Env template `KEY = value` spacing | Works fine with python-dotenv. |
| 16 | Entry-point sys.path mutation | Dev convenience script, not production entrypoint. |
| 18 | Dependency management split | requirements.txt + pyproject.toml is standard Python pattern for deployment vs development. |

---

## Testing Strategy

Each fix includes a minimal regression test:

- **Phase 0 security fixes**: Add tests that attempt `../` traversal, shell metacharacter injection, and verify 403/error responses. These go in `tests/test_security.py`.
- **Phase 1 correctness fixes**: Add tests verifying method nodes appear in analysis output, cache keys differ by commit, git worktree detection works. These extend existing test files.
- **Phase 2**: Tests optional but recommended for D2 (language detection) and C3 (scope-aware resolution).

---

## Execution Order

```
Phase 0 (0.1 → 0.7) — all independent, can parallelize
    ↓
Phase 1 (1.1 → 1.6) — all independent, can parallelize
    ↓
Phase 2 (2.1 → 2.6 as capacity allows)
```

All Phase 0 tasks are independent of each other. Same for Phase 1. Within each phase,
tasks can be executed in parallel by multiple agents.
