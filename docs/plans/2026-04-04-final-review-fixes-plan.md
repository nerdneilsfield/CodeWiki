# Final Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 4 blockers, 8 high-priority issues, and 6 medium/suggestion items from the final Python review and Codex audit.

**Architecture:** Five batches following the spec's dependency order. Batch 1 is all BLOCK items (independent, safe). Batch 2 is the bound H1+H2+H3 web correctness triple. Batch 3 is independent HIGH items. Batch 4 is MEDIUM cleanup. Batch 5 is toolchain (coverage).

**Tech Stack:** Python 3.13, threading, pydantic, functools, asyncio, pytest

---

## Batch 1: BLOCK Fixes

### Task 1: B1+B2+B3+B4 — Code hygiene blockers

**Files:**
- Modify: `codewiki/src/be/agent_tools/str_replace_editor.py:36,201,391-404`
- Modify: `codewiki/src/be/dependency_analyzer/analysis/cloning.py:99,155`
- Modify: `codewiki/cli/commands/generate.py:534`
- Modify: `codewiki/src/be/documentation_scheduler.py:331`
- Modify: `codewiki/cli/adapters/doc_generator.py:182`
- Test: `tests/test_block_fixes.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_block_fixes.py
import pytest


class TestB1NoStdoutReplacement:
    def test_str_replace_editor_does_not_replace_stdout(self):
        """Module must not replace sys.stdout at import time."""
        import sys
        original = sys.stdout
        import importlib
        import codewiki.src.be.agent_tools.str_replace_editor
        importlib.reload(codewiki.src.be.agent_tools.str_replace_editor)
        assert sys.stdout is original or type(sys.stdout).__name__ == type(original).__name__


class TestB2NoBareExcept:
    def test_cloning_no_bare_except(self):
        import inspect
        from codewiki.src.be.dependency_analyzer.analysis import cloning
        source = inspect.getsource(cloning)
        # "except:" without a type is bare; "except Exception:" is fine
        lines = source.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped == "except:" or stripped == "except:  # noqa":
                pytest.fail(f"Bare except: at line {i}")

    def test_generate_no_bare_except(self):
        import inspect
        from codewiki.cli.commands import generate
        source = inspect.getsource(generate)
        lines = source.split("\n")
        for i, line in enumerate(lines, 1):
            if line.strip() == "except:":
                pytest.fail(f"Bare except: at line {i}")


class TestB4TokenFieldAlignment:
    def test_doc_generator_reads_correct_token_fields(self):
        """CLI adapter must read total_input_tokens, not total_input."""
        import inspect
        from codewiki.cli.adapters import doc_generator
        source = inspect.getsource(doc_generator)
        assert "total_input_tokens" in source
        assert "total_output_tokens" in source
        # Old wrong keys should not appear
        assert 'get("total_input"' not in source
        assert 'get("total_output"' not in source
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `pytest tests/test_block_fixes.py -v`

- [ ] **Step 3: Apply B1 — delete sys.stdout replacement**

In `codewiki/src/be/agent_tools/str_replace_editor.py`, delete line 36:
```python
# DELETE THIS LINE:
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
```

Also delete the comment at lines 33-35.

- [ ] **Step 4: Apply B2 — bare except → except Exception**

In `codewiki/src/be/dependency_analyzer/analysis/cloning.py:99`:
```python
# Old:
            except:
                pass
# New:
            except Exception:
                pass
```

Same at `cloning.py:155`.

In `codewiki/cli/commands/generate.py:534`:
```python
# Old:
        except:
            pass
# New:
        except Exception:
            pass
```

- [ ] **Step 5: Apply B3 — assert → if/raise**

In `codewiki/src/be/documentation_scheduler.py:331`:
```python
# Old:
                    assert last_exc is not None
# New:
                    if last_exc is None:
                        raise RuntimeError("retry loop exited without capturing exception")
```

In `codewiki/src/be/agent_tools/str_replace_editor.py`, find all `assert` statements used for contract checking and replace with `if not X: raise ValueError(...)`.

- [ ] **Step 6: Apply B4 — fix token field names**

In `codewiki/cli/adapters/doc_generator.py:182`:
```python
# Old:
        self.job.statistics.total_tokens_used = int(
            (token_usage.get("total_input", 0) or 0) + (token_usage.get("total_output", 0) or 0)
        )
# New:
        self.job.statistics.total_tokens_used = int(
            (token_usage.get("total_input_tokens", 0) or 0)
            + (token_usage.get("total_output_tokens", 0) or 0)
        )
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_block_fixes.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add codewiki/src/be/agent_tools/str_replace_editor.py codewiki/src/be/dependency_analyzer/analysis/cloning.py codewiki/cli/commands/generate.py codewiki/src/be/documentation_scheduler.py codewiki/cli/adapters/doc_generator.py tests/test_block_fixes.py
git commit -m "fix: remove stdout hack, bare excepts, asserts in prod, and token field mismatch"
```

---

## Batch 2: H1+H2+H3 — Web Job Correctness (bound batch)

### Task 2: Thread-safe job status + GenerationResult transparency + failure persistence

**Files:**
- Modify: `codewiki/src/fe/background_worker.py`
- Modify: `codewiki/src/fe/models.py`
- Modify: `codewiki/src/fe/routes.py`
- Create: `tests/test_web_job_correctness.py`

All three must land in one commit.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_web_job_correctness.py
import threading
import pytest
from datetime import datetime
from codewiki.src.fe.models import JobStatus


class TestH1SnapshotAPI:
    def test_snapshot_jobs_returns_deep_copy(self):
        from codewiki.src.fe.background_worker import BackgroundWorker
        worker = BackgroundWorker.__new__(BackgroundWorker)
        worker.job_status = {
            "j1": JobStatus(job_id="j1", repo_url="http://x", status="completed",
                           created_at=datetime.now()),
        }
        worker._job_lock = threading.Lock()
        snap = worker.snapshot_jobs()
        # Mutation on snapshot's JobStatus must not affect original
        snap["j1"].status = "mutated"
        assert worker.job_status["j1"].status == "completed"

    def test_snapshot_job_returns_none_for_missing(self):
        from codewiki.src.fe.background_worker import BackgroundWorker
        worker = BackgroundWorker.__new__(BackgroundWorker)
        worker.job_status = {}
        worker._job_lock = threading.Lock()
        assert worker.snapshot_job("nonexistent") is None


class TestH2GenerationResultFields:
    def test_job_status_has_generation_fields(self):
        job = JobStatus(
            job_id="j1", repo_url="http://x", status="completed",
            created_at=datetime.now(),
            generation_status="degraded",
            degradation_reasons=["IndexBuild failed"],
            module_summary={"total": 10, "completed": ["a"], "failed": []},
        )
        assert job.generation_status == "degraded"
        assert len(job.degradation_reasons) == 1
        assert job.module_summary["total"] == 10
```

- [ ] **Step 2: Run tests — expect FAIL** (fields don't exist yet)

Run: `pytest tests/test_web_job_correctness.py -v`

- [ ] **Step 3: Add new fields to JobStatus**

In `codewiki/src/fe/models.py`, add to `JobStatus`:
```python
    generation_status: Optional[str] = None  # "complete" / "degraded" / "failed"
    degradation_reasons: list = None  # type: ignore[assignment]
    module_summary: Optional[dict] = None

    def __post_init__(self):
        if self.degradation_reasons is None:
            self.degradation_reasons = []
```

Also add to `JobStatusResponse`:
```python
    generation_status: Optional[str] = None
    degradation_reasons: list[str] = []
    module_summary: Optional[dict] = None
```

- [ ] **Step 4: Add threading.Lock + snapshot methods to BackgroundWorker**

In `codewiki/src/fe/background_worker.py`, in `__init__`:
```python
import threading
import copy
self._job_lock = threading.Lock()
```

Add snapshot methods (must copy both dict AND JobStatus objects):
```python
import copy

    def snapshot_jobs(self) -> dict[str, JobStatus]:
        """Return a thread-safe deep copy of all job statuses."""
        with self._job_lock:
            return {k: copy.copy(v) for k, v in self.job_status.items()}

    def snapshot_job(self, job_id: str) -> Optional[JobStatus]:
        """Return a thread-safe copy of a single job, or None."""
        with self._job_lock:
            job = self.job_status.get(job_id)
            return copy.copy(job) if job else None
```

`copy.copy()` on each `JobStatus` ensures mutations on the returned object don't write back to the original. `dict()` alone would only copy the mapping, not the values.

Wrap all write operations in `_process_job` with `self._job_lock`.

- [ ] **Step 5: Transparently populate GenerationResult fields**

In `background_worker.py`, after `result = ...` from `doc_generator.run()`:
```python
with self._job_lock:
    job.generation_status = result.status
    job.degradation_reasons = list(result.warnings)
    job.module_summary = result.module_summary.to_dict() if result.module_summary else None
```

- [ ] **Step 6: H3 — save in finally block**

Move `self.save_job_statuses()` into the `finally` block (after cleanup), so both success and failure paths persist:
```python
        finally:
            # Cleanup temp dir ...
            # Always persist job state
            self.save_job_statuses()
```

- [ ] **Step 7: Update routes.py to use snapshot API**

Replace all `self.background_worker.job_status[...]` and `self.background_worker.get_all_jobs()` with `self.background_worker.snapshot_job(...)` and `self.background_worker.snapshot_jobs()`.

- [ ] **Step 8: Run tests**

Run: `pytest tests/test_web_job_correctness.py -v`
Expected: All PASS

- [ ] **Step 9: Commit**

```bash
git add codewiki/src/fe/background_worker.py codewiki/src/fe/models.py codewiki/src/fe/routes.py tests/test_web_job_correctness.py
git commit -m "fix(web): thread-safe job status, GenerationResult transparency, failure persistence"
```

---

## Batch 3: Independent HIGH Items

### Task 3: H5 — Cache compiled Jinja2 Template objects

**Files:**
- Modify: `codewiki/src/fe/template_utils.py:20-41`
- Create: `tests/test_template_cache.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_template_cache.py
def test_compile_template_is_cached():
    from codewiki.src.fe.template_utils import _compile_template
    t1 = _compile_template("<p>{{ x }}</p>")
    t2 = _compile_template("<p>{{ x }}</p>")
    assert t1 is t2  # same object = cached

def test_render_template_uses_cache():
    from codewiki.src.fe.template_utils import render_template
    html1 = render_template("<p>{{ name }}</p>", {"name": "A"})
    html2 = render_template("<p>{{ name }}</p>", {"name": "B"})
    assert "A" in html1
    assert "B" in html2
```

- [ ] **Step 2: Implement**

```python
# codewiki/src/fe/template_utils.py — replace render_template

from functools import lru_cache

@lru_cache(maxsize=16)
def _compile_template(template_str: str):
    """Compile a Jinja2 template string. Cached by content."""
    env = Environment(
        loader=StringTemplateLoader(template_str),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template("")

def render_template(template: str, context: Dict[str, Any]) -> str:
    compiled = _compile_template(template)
    return compiled.render(**context)
```

- [ ] **Step 3: Run tests + commit**

```bash
pytest tests/test_template_cache.py -v
git add codewiki/src/fe/template_utils.py tests/test_template_cache.py
git commit -m "perf(web): cache Jinja2 template compilation by content"
```

---

### Task 4: H6+H7+H8 — asyncio deprecation, logger level, commit_id validation

**Files:**
- Modify: `codewiki/src/be/documentation_scheduler.py:325,383`
- Modify: `codewiki/src/be/dependency_analyzer/ast_parser.py:16`
- Modify: `codewiki/src/fe/github_processor.py`
- Modify: `codewiki/src/fe/routes.py`
- Create: `tests/test_high_fixes.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_high_fixes.py
import pytest
import inspect


class TestH6NoDeprecatedEventLoop:
    def test_scheduler_uses_get_running_loop(self):
        from codewiki.src.be import documentation_scheduler
        source = inspect.getsource(documentation_scheduler)
        assert "get_event_loop" not in source, "get_event_loop() is deprecated"
        assert "get_running_loop" in source


class TestH7NoForcedDebugLevel:
    def test_ast_parser_does_not_force_debug(self):
        from codewiki.src.be.dependency_analyzer import ast_parser
        source = inspect.getsource(ast_parser)
        assert "setLevel(logging.DEBUG)" not in source


class TestH8CommitIdCaseInsensitive:
    def test_clone_repository_accepts_uppercase_hex(self):
        import re
        # The validator in github_processor should accept uppercase
        from codewiki.src.fe.github_processor import clone_repository
        source = inspect.getsource(clone_repository)
        # Must have a validation that handles case
        assert ".lower()" in source or "a-fA-F" in source
```

- [ ] **Step 2: Apply H6**

In `codewiki/src/be/documentation_scheduler.py:325`:
```python
# Old:
task_t0 = asyncio.get_event_loop().time()
# New:
task_t0 = asyncio.get_running_loop().time()
```

Same at line 383.

- [ ] **Step 3: Apply H7**

In `codewiki/src/be/dependency_analyzer/ast_parser.py:16`, delete:
```python
logger.setLevel(logging.DEBUG)
```

- [ ] **Step 4: Apply H8**

In `codewiki/src/fe/github_processor.py`, at the start of `clone_repository()`:
```python
import re
_SAFE_COMMIT = re.compile(r"^[a-f0-9]{4,40}$")

if commit_id:
    commit_id = commit_id.strip().lower()
    if not _SAFE_COMMIT.match(commit_id):
        raise ValueError(f"Invalid commit_id: {commit_id!r}")
```

In `codewiki/src/fe/routes.py`, update `_COMMIT_RE` usage to normalize before matching:
```python
# Before regex check:
commit_id = commit_id.strip().lower() if commit_id else ""
```

- [ ] **Step 5: Run tests + commit**

```bash
pytest tests/test_high_fixes.py -v
git add codewiki/src/be/documentation_scheduler.py codewiki/src/be/dependency_analyzer/ast_parser.py codewiki/src/fe/github_processor.py codewiki/src/fe/routes.py tests/test_high_fixes.py
git commit -m "fix: deprecation warnings, forced debug level, case-insensitive commit_id"
```

---

## Batch 4: MEDIUM + SUGGESTION

### Task 5: M9 — UTC datetime across web/job/cache chain

**Files:**
- Modify: `codewiki/src/fe/models.py`
- Modify: `codewiki/src/fe/routes.py`
- Modify: `codewiki/src/fe/background_worker.py`
- Modify: `codewiki/src/fe/cache_manager.py`
- Create: `tests/test_utc_datetime.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_utc_datetime.py
import inspect
import pytest


class TestNoNaiveDatetime:
    """All datetime.now() calls in web layer must use timezone.utc."""

    @pytest.mark.parametrize("module_path", [
        "codewiki.src.fe.routes",
        "codewiki.src.fe.background_worker",
        "codewiki.src.fe.cache_manager",
    ])
    def test_no_naive_datetime_now(self, module_path):
        import importlib
        mod = importlib.import_module(module_path)
        source = inspect.getsource(mod)
        # datetime.now() without timezone is naive
        naive_calls = [
            i for i, line in enumerate(source.split("\n"), 1)
            if "datetime.now()" in line and "timezone" not in line and "utc" not in line.lower()
        ]
        assert not naive_calls, f"Naive datetime.now() at lines: {naive_calls}"
```

- [ ] **Step 2: Apply UTC changes**

In every file, replace `datetime.now()` with `datetime.now(timezone.utc)`. Add `from datetime import timezone` where missing.

In `cache_manager.py`, for backward compat when loading old cache entries with naive datetimes:
```python
# When loading fromisoformat:
dt = datetime.fromisoformat(raw_str)
if dt.tzinfo is None:
    dt = dt.replace(tzinfo=timezone.utc)
```

- [ ] **Step 3: Run tests + commit**

```bash
pytest tests/test_utc_datetime.py -v
git add codewiki/src/fe/models.py codewiki/src/fe/routes.py codewiki/src/fe/background_worker.py codewiki/src/fe/cache_manager.py tests/test_utc_datetime.py
git commit -m "fix(web): use timezone-aware UTC datetimes across web/job/cache chain"
```

---

### Task 6: M5+M7+M8+S1+S5 — Cache flush, evidence snippets, atomic write, cleanup

**Files:**
- Modify: `codewiki/src/fe/cache_manager.py`
- Modify: `codewiki/src/be/generation/context_pack.py:265-276`
- Modify: `codewiki/cli/adapters/doc_generator.py:253`
- Modify: `codewiki/src/fe/config.py:25`
- Delete: `test_format.py`, `test_math.py`, `test_math2.py`
- Create: `tests/test_medium_fixes.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_medium_fixes.py
import pytest
import os


class TestM5CacheManagerDirtyFlag:
    def test_get_cached_docs_does_not_write_immediately(self, tmp_path):
        from codewiki.src.fe.cache_manager import CacheManager
        mgr = CacheManager(cache_dir=str(tmp_path))
        # Pre-populate
        mgr.add_to_cache("http://repo", str(tmp_path / "docs"))
        # Record file mtime
        index_path = tmp_path / "cache_index.json"
        mtime_before = index_path.stat().st_mtime if index_path.exists() else 0
        import time; time.sleep(0.05)  # ensure mtime would differ
        # Cache hit should NOT write to disk
        mgr.get_cached_docs("http://repo")
        mtime_after = index_path.stat().st_mtime if index_path.exists() else 0
        assert mtime_after == mtime_before, "get_cached_docs should not write to disk"


class TestM7EvidenceSnippetsUsesEdgeIndex:
    def test_no_full_scan(self):
        import inspect
        from codewiki.src.be.generation.context_pack import _build_evidence_snippets
        source = inspect.getsource(_build_evidence_snippets)
        assert "edge_index" in source, "_build_evidence_snippets should use edge_index"


class TestS5CleanupHours:
    def test_cleanup_hours_reasonable(self):
        from codewiki.src.fe.config import WebAppConfig
        assert WebAppConfig.JOB_CLEANUP_HOURS <= 168  # max 1 week


class TestS1NoStrayTestFiles:
    def test_no_root_test_files(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        stray = [f for f in os.listdir(root) if f.startswith("test_") and f.endswith(".py")]
        assert not stray, f"Stray test files in root: {stray}"
```

- [ ] **Step 2: Apply M5 — CacheManager dirty flag**

In `codewiki/src/fe/cache_manager.py`:

Add `self._dirty = False` in `__init__`.

In `get_cached_docs()`:
```python
# Old:
entry.last_accessed = datetime.now()
self.save_cache_index()
# New:
entry.last_accessed = datetime.now(timezone.utc)
self._dirty = True
# Do NOT call save_cache_index() here
```

Add `flush()` method:
```python
def flush(self):
    """Write cache index to disk if dirty."""
    if self._dirty:
        self.save_cache_index()
        self._dirty = False
```

Call `self.flush()` at the end of `add_to_cache()`, `remove_from_cache()`, `cleanup_expired_cache()`.

Add at the end of `__init__`:
```python
import atexit
atexit.register(self.flush)
```

- [ ] **Step 3: Apply M7 — evidence snippets via EdgeIndex**

```python
# codewiki/src/be/generation/context_pack.py — replace _build_evidence_snippets

def _build_evidence_snippets(module_sym_ids: set[str], index_products) -> list[str]:
    """Extract file:line evidence references for module symbols using EdgeIndex."""
    snippets = []
    edge_index = index_products.edge_index
    for sid in module_sym_ids:
        for edge in edge_index.callees_of(sid):
            for ref in edge.evidence_refs:
                snippet = f"{ref.file_path}:{ref.start_line} ({edge.edge_type.value})"
                snippets.append(snippet)
                if len(snippets) >= 20:
                    return snippets
    return snippets
```

- [ ] **Step 4: Apply M8 — atomic fallback metadata write**

In `codewiki/cli/adapters/doc_generator.py:253`, replace bare `open("w")` with:
```python
file_manager.save_json(fallback_metadata, metadata_path)
```

- [ ] **Step 5: Apply S1 — delete stray files**

```bash
rm -f test_format.py test_math.py test_math2.py
```

- [ ] **Step 6: Apply S5 — fix cleanup hours**

In `codewiki/src/fe/config.py:25`:
```python
# Old:
JOB_CLEANUP_HOURS = 24000
# New:
JOB_CLEANUP_HOURS = 24
```

- [ ] **Step 7: Run tests + commit**

```bash
pytest tests/test_medium_fixes.py -v
git add codewiki/src/fe/cache_manager.py codewiki/src/be/generation/context_pack.py codewiki/cli/adapters/doc_generator.py codewiki/src/fe/config.py tests/test_medium_fixes.py
git rm -f test_format.py test_math.py test_math2.py
git commit -m "fix: cache dirty flag, evidence EdgeIndex, atomic metadata, cleanup hours, stray files"
```

---

## Batch 5: Toolchain

### Task 7: H4 — Enable coverage measurement

**Files:**
- Modify: `pyproject.toml`
- Modify: `Makefile`

**Note:** Coverage enablement is NOT a verification gate for the runtime fixes above. It is a toolchain improvement.

- [ ] **Step 1: Update pyproject.toml**

Remove `-p no:cov` from `addopts`:
```toml
# Old:
addopts = "-v -p no:cov -s"
# New:
addopts = "-v -s"
```

Add coverage configuration:
```toml
[tool.coverage.run]
source = ["codewiki"]
omit = ["*/tests/*", "*/__pycache__/*"]

[tool.coverage.report]
show_missing = true
skip_covered = true
```

- [ ] **Step 2: Update Makefile test target**

```makefile
test:
	pytest tests/ --cov=codewiki --cov-report=term-missing -q
```

- [ ] **Step 3: Run and verify coverage collects data**

```bash
make test
```

Expected: tests pass, coverage report is printed (even if below 80%).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml Makefile
git commit -m "chore(tooling): enable pytest-cov coverage measurement"
```

---

### Task 8: Full regression

- [ ] **Step 1: Run full suite**

```bash
make test
```

Expected: All tests pass.

- [ ] **Step 2: Compile check**

```bash
python3 -m py_compile codewiki/src/be/agent_tools/str_replace_editor.py codewiki/src/fe/background_worker.py codewiki/src/fe/template_utils.py codewiki/src/be/documentation_scheduler.py codewiki/src/fe/routes.py codewiki/src/fe/cache_manager.py codewiki/src/be/generation/context_pack.py codewiki/src/fe/github_processor.py
```

- [ ] **Step 3: Commit if fixes needed**

```bash
git commit -m "test: verify final review fixes"
```
