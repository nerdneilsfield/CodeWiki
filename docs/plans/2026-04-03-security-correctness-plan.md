# Security + Correctness Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 3 security vulnerabilities and 6 correctness bugs identified by the comprehensive audit, without changing system architecture.

**Architecture:** Surgical fixes in existing files. One new module (`html_sanitizer.py`). One scheduler refactor (coordinator coroutine model). All changes are backward-compatible — no public API signatures change except where the spec mandates it.

**Tech Stack:** Python 3.13, pytest, nh3, asyncio

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `codewiki/src/utils.py` | Modify | C2: atomic `save_text` + encoding; C3: `save_json` encoding |
| `codewiki/src/be/llm_services.py` | Modify | C1: guard empty/null LLM response; S3: add `validate_llm_credentials()` |
| `codewiki/src/be/generation_state.py` | Modify | C4: corrupt state file tolerance |
| `codewiki/src/be/agent_tools/str_replace_editor.py` | Modify | C6: raise on write failure |
| `codewiki/src/fe/routes.py` | Modify | S2: server-side commit_id validation |
| `codewiki/src/config.py` | Modify | S3: delete all `os.getenv()`, `load_dotenv()`, legacy constants |
| `codewiki/src/fe/html_sanitizer.py` | Create | S1: `sanitize_html()` with nh3 |
| `codewiki/src/fe/templates.py` | Modify | S1: call `sanitize_html()` before `| safe` |
| `codewiki/src/fe/visualise_docs.py` | Modify | S1: sanitize Mermaid content + rendered HTML |
| `codewiki/src/be/documentation_scheduler.py` | Modify | C5: coordinator coroutine model |
| `pyproject.toml` | Modify | S1: add `nh3` dependency |
| `docker/env.example` | Modify | S3: update for TOML-based config |
| `tests/test_file_io_safety.py` | Create | C2, C3 tests |
| `tests/test_llm_response_guard.py` | Create | C1 tests |
| `tests/test_state_load_resilience.py` | Create | C4 tests |
| `tests/test_write_failure_propagation.py` | Create | C6 tests |
| `tests/test_commit_id_validation.py` | Create | S2 tests |
| `tests/test_config_toml_only.py` | Create | S3 tests |
| `tests/test_html_sanitizer.py` | Create | S1 tests |
| `tests/test_scheduler_coordinator.py` | Create | C5 tests |

---

### Task 1: C2+C3 — File I/O encoding + atomic write

**Files:**
- Modify: `codewiki/src/utils.py:22-62`
- Create: `tests/test_file_io_safety.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_file_io_safety.py
import os
import json
import pytest


class TestSaveTextAtomicAndEncoding:
    def test_save_text_writes_utf8(self, tmp_path):
        from codewiki.src.utils import file_manager

        path = str(tmp_path / "test.md")
        file_manager.save_text("你好世界 🌍", path)
        with open(path, "r", encoding="utf-8") as f:
            assert f.read() == "你好世界 🌍"

    def test_save_text_atomic_no_partial_on_error(self, tmp_path):
        from codewiki.src.utils import file_manager

        path = str(tmp_path / "test.md")
        file_manager.save_text("original content", path)

        # Patch os.replace to simulate crash mid-write
        import unittest.mock as mock

        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                file_manager.save_text("new content that should not appear", path)

        # Original file must survive
        assert file_manager.load_text(path) == "original content"

    def test_load_text_reads_utf8(self, tmp_path):
        from codewiki.src.utils import file_manager

        path = str(tmp_path / "test.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("中文内容")
        assert file_manager.load_text(path) == "中文内容"


class TestSaveJsonEncoding:
    def test_save_json_writes_utf8(self, tmp_path):
        from codewiki.src.utils import file_manager

        path = str(tmp_path / "test.json")
        file_manager.save_json({"name": "模块名称"}, path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["name"] == "模块名称"

    def test_load_json_reads_utf8(self, tmp_path):
        from codewiki.src.utils import file_manager

        path = str(tmp_path / "test.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"key": "日本語"}, f, ensure_ascii=False)
        data = file_manager.load_json(path)
        assert data["key"] == "日本語"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_file_io_safety.py -v`
Expected: `test_save_text_atomic_no_partial_on_error` FAIL (current save_text is not atomic)

- [ ] **Step 3: Implement the fixes**

```python
# codewiki/src/utils.py — replace save_json, save_text, load_text, load_json

    @staticmethod
    def save_json(data: Any, filepath: str) -> None:
        """Save data as JSON to file (atomic, UTF-8)."""
        parent_dir = os.path.dirname(os.path.abspath(filepath)) or "."
        os.makedirs(parent_dir, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", dir=parent_dir)
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, filepath)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    @staticmethod
    def load_json(filepath: str) -> Optional[Dict[str, Any]]:
        """Load JSON from file, return None if file doesn't exist or is corrupt."""
        if not os.path.exists(filepath):
            return None
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def save_text(content: str, filepath: str) -> None:
        """Save text content to file (atomic, UTF-8)."""
        parent_dir = os.path.dirname(os.path.abspath(filepath)) or "."
        os.makedirs(parent_dir, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", dir=parent_dir)
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, filepath)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    @staticmethod
    def load_text(filepath: str) -> str:
        """Load text content from file (UTF-8)."""
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_file_io_safety.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/utils.py tests/test_file_io_safety.py
git commit -m "fix(io): add UTF-8 encoding and atomic writes to save_text/save_json"
```

---

### Task 2: C1 — Guard empty/null LLM response

**Files:**
- Modify: `codewiki/src/be/llm_services.py:359`
- Create: `tests/test_llm_response_guard.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_llm_response_guard.py
import pytest
from unittest.mock import MagicMock, patch


def _make_config():
    """Build a minimal Config-like mock that passes call_llm's checks."""
    config = MagicMock()
    config.main_model = "test-model"
    config.max_tokens = 1000
    config.long_context_model = None
    config.long_context_threshold = 200_000
    config.providers = None  # no provider registry → uses legacy client path
    config.llm_base_url = "http://localhost:4000"
    config.llm_api_key = "test-key"
    return config


class TestLlmResponseGuard:
    def test_empty_choices_raises_value_error(self):
        from codewiki.src.be.llm_services import call_llm

        config = _make_config()
        mock_response = MagicMock()
        mock_response.choices = []  # empty choices

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        # Patch _create_client_for_model — the actual entry point for client creation
        with patch(
            "codewiki.src.be.llm_services._create_client_for_model",
            return_value=(mock_client, "openai_compatible"),
        ):
            with pytest.raises(ValueError, match="empty choices"):
                call_llm("test prompt", config)

    def test_none_content_raises_value_error(self):
        from codewiki.src.be.llm_services import call_llm

        config = _make_config()
        mock_choice = MagicMock()
        mock_choice.message.content = None
        mock_choice.finish_reason = "length"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch(
            "codewiki.src.be.llm_services._create_client_for_model",
            return_value=(mock_client, "openai_compatible"),
        ):
            with pytest.raises(ValueError, match="null content"):
                call_llm("test prompt", config)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_response_guard.py -v`
Expected: FAIL (current code does not raise on empty/null)

- [ ] **Step 3: Implement the guard**

In `codewiki/src/be/llm_services.py`, after line 358 (`response = client.chat.completions.create(...)`) and before `content = response.choices[0].message.content`, add:

```python
                    if not response.choices:
                        raise ValueError(
                            f"LLM returned empty choices (model={resolved_model_name})"
                        )
                    content = response.choices[0].message.content
                    if content is None:
                        raise ValueError(
                            f"LLM returned null content (model={resolved_model_name}, "
                            f"finish_reason={response.choices[0].finish_reason!r})"
                        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm_response_guard.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/llm_services.py tests/test_llm_response_guard.py
git commit -m "fix(llm): guard against empty choices and null content from LLM response"
```

---

### Task 3: C4 — generation_state.json load resilience

**Files:**
- Modify: `codewiki/src/be/generation_state.py:181-206`
- Create: `tests/test_state_load_resilience.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_state_load_resilience.py
import json
import pytest


class TestGenerationStateLoadResilience:
    def test_corrupt_json_returns_empty_state(self, tmp_path):
        from codewiki.src.be.generation_state import GenerationState

        path = tmp_path / "generation_state.json"
        path.write_text("{invalid json content!!!", encoding="utf-8")
        state = GenerationState.load(str(path))
        assert len(state.tasks) == 0

    def test_truncated_json_returns_empty_state(self, tmp_path):
        from codewiki.src.be.generation_state import GenerationState

        path = tmp_path / "generation_state.json"
        path.write_text('{"schema_version": "v1", "tasks": [', encoding="utf-8")
        state = GenerationState.load(str(path))
        assert len(state.tasks) == 0

    def test_malformed_task_skipped(self, tmp_path):
        from codewiki.src.be.generation_state import GenerationState

        path = tmp_path / "generation_state.json"
        data = {
            "repo_commit": "abc",
            "tasks": [
                {"doc_id": "good", "kind": "module", "module_path": ["A"],
                 "output_file": "a.md", "status": "completed"},
                {"broken": "missing required fields"},
                {"doc_id": "also_good", "kind": "module", "module_path": ["B"],
                 "output_file": "b.md", "status": "planned"},
            ],
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        state = GenerationState.load(str(path))
        assert "good" in state.tasks
        assert "also_good" in state.tasks
        assert len(state.tasks) == 2  # bad one skipped

    def test_missing_file_returns_empty_state(self, tmp_path):
        from codewiki.src.be.generation_state import GenerationState

        state = GenerationState.load(str(tmp_path / "nonexistent.json"))
        assert len(state.tasks) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_state_load_resilience.py -v`
Expected: `test_corrupt_json_returns_empty_state` and `test_truncated_json_returns_empty_state` FAIL

- [ ] **Step 3: Implement the fix**

```python
# codewiki/src/be/generation_state.py — replace load() method

    @classmethod
    def load(cls, path: str) -> "GenerationState":
        if not os.path.exists(path):
            return cls()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Corrupt generation state at %s (%s) — starting fresh", path, exc)
            return cls()
        state = cls(
            repo_commit=data.get("repo_commit", ""),
            config_fingerprint=data.get("config_fingerprint", ""),
        )
        for raw_task in data.get("tasks", []):
            try:
                task_fields = {
                    key: value
                    for key, value in raw_task.items()
                    if key in DocTask.__dataclass_fields__
                }
                task = DocTask(**task_fields)
            except (TypeError, KeyError) as exc:
                logger.warning("Skipping malformed task record: %s", exc)
                continue
            existing_owner = state._output_file_index.get(task.output_file)
            if existing_owner and existing_owner != task.doc_id:
                logger.warning(
                    "Skipping task %s due to output_file collision with %s",
                    task.doc_id, existing_owner,
                )
                continue
            state.tasks[task.doc_id] = task
            state._output_file_index[task.output_file] = task.doc_id
        return state
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_state_load_resilience.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/generation_state.py tests/test_state_load_resilience.py
git commit -m "fix(state): tolerate corrupt generation_state.json on load"
```

---

### Task 4: C6 — str_replace_editor raise on write failure

**Files:**
- Modify: `codewiki/src/be/agent_tools/str_replace_editor.py:792-800`
- Create: `tests/test_write_failure_propagation.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_write_failure_propagation.py
import pytest
from pathlib import Path
from unittest.mock import patch


class TestWriteFailurePropagation:
    def test_write_file_raises_on_failure(self, tmp_path):
        from codewiki.src.be.agent_tools.str_replace_editor import EditTool

        # Real constructor: EditTool(REGISTRY, absolute_docs_path, allowed_base_path)
        tool = EditTool(REGISTRY={}, absolute_docs_path=str(tmp_path), allowed_base_path=tmp_path)
        target = tmp_path / "test.md"

        # Patch Path.write_text to simulate disk error — more reliable than chmod tricks
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            with pytest.raises(PermissionError, match="Could not write"):
                tool.write_file(target, "content")

    def test_write_file_succeeds_normally(self, tmp_path):
        from codewiki.src.be.agent_tools.str_replace_editor import EditTool

        tool = EditTool(REGISTRY={}, absolute_docs_path=str(tmp_path), allowed_base_path=tmp_path)
        target = tmp_path / "test.md"
        tool.write_file(target, "hello world")
        assert target.read_text() == "hello world"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_write_failure_propagation.py -v`
Expected: `test_write_file_raises_on_failure` FAIL (currently returns silently)

- [ ] **Step 3: Implement the fix**

```python
# codewiki/src/be/agent_tools/str_replace_editor.py — replace write_file method

    def write_file(self, path: Path, file: str):
        """Write the content of a file to a given path; raise on failure."""
        try:
            path.write_text(file, encoding=self._encoding or "utf-8")
        except Exception as e:
            self.logs.append(
                f"Write failed for {self._get_display_path(path)}: {e}"
            )
            raise PermissionError(
                f"Could not write {self._get_display_path(path)}: {e}"
            ) from e
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_write_failure_propagation.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/agent_tools/str_replace_editor.py tests/test_write_failure_propagation.py
git commit -m "fix(agent): raise on write failure instead of silent return"
```

---

### Task 5: S2 — Server-side commit_id validation

**Files:**
- Modify: `codewiki/src/fe/routes.py:53-64`
- Create: `tests/test_commit_id_validation.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_commit_id_validation.py
import re
import pytest

# Test the regex directly (avoid importing FastAPI app)
_COMMIT_RE = re.compile(r"^[a-f0-9]{4,40}$")


class TestCommitIdValidation:
    def test_valid_short_hash(self):
        assert _COMMIT_RE.match("abcd")

    def test_valid_full_hash(self):
        assert _COMMIT_RE.match("a" * 40)

    def test_empty_string_allowed(self):
        """Empty means 'no commit specified' — should pass validation."""
        # Empty strings are handled before regex: if not commit_id, skip check
        assert not _COMMIT_RE.match("")

    def test_rejects_uppercase(self):
        assert not _COMMIT_RE.match("ABCD1234")

    def test_rejects_git_flag_injection(self):
        assert not _COMMIT_RE.match("--upload-pack=malicious")

    def test_rejects_too_short(self):
        assert not _COMMIT_RE.match("abc")

    def test_rejects_too_long(self):
        assert not _COMMIT_RE.match("a" * 41)

    def test_rejects_special_characters(self):
        assert not _COMMIT_RE.match("abcd;rm -rf /")

    def test_rejects_branch_name(self):
        assert not _COMMIT_RE.match("main")

    def test_rejects_path_traversal(self):
        assert not _COMMIT_RE.match("../../etc/passwd")


class TestCommitIdRouteIntegration:
    """Verify the validation is actually wired into the route handler."""

    def test_route_has_commit_id_validation(self):
        """index_post must contain commit_id regex check."""
        import inspect
        from codewiki.src.fe.routes import WebRoutes

        source = inspect.getsource(WebRoutes.index_post)
        assert "_COMMIT_RE" in source or "re.match" in source, (
            "commit_id validation regex not found in index_post() — "
            "the regex exists but is not wired into the route"
        )
```

- [ ] **Step 2: Run tests to verify correct failure**

Run: `pytest tests/test_commit_id_validation.py -v`
Expected: `TestCommitIdValidation` tests all PASS (regex unit tests). `TestCommitIdRouteIntegration::test_route_has_commit_id_validation` FAILS (validation not yet in route).

- [ ] **Step 3: Implement the validation in routes.py**

In `codewiki/src/fe/routes.py`, add at module level:

```python
import re
_COMMIT_RE = re.compile(r"^[a-f0-9]{4,40}$")
```

In `index_post()`, after `commit_id = commit_id.strip() if commit_id else ""` (line 64), add an `elif` that raises HTTPException to actually block the request:

```python
        elif commit_id and not _COMMIT_RE.match(commit_id):
            raise HTTPException(
                status_code=400,
                detail="Invalid commit ID format (expected 4-40 lowercase hex characters)",
            )
```

This goes inside the existing `if not repo_url: ... elif not is_valid_github_url: ...` chain, before the job creation logic. `HTTPException` is already imported in this file.

- [ ] **Step 4: Commit**

```bash
git add codewiki/src/fe/routes.py tests/test_commit_id_validation.py
git commit -m "fix(security): add server-side commit_id validation"
```

---

### Task 6: S3 — TOML-only configuration

**Files:**
- Modify: `codewiki/src/config.py`
- Modify: `codewiki/src/be/llm_services.py`
- Modify: `codewiki/cli/commands/generate.py` (wire `validate_llm_credentials` into CLI startup)
- Modify: `docker/env.example`
- Create: `tests/test_config_toml_only.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config_toml_only.py
import os
import pytest


class TestNoEnvVarFallback:
    def test_config_module_has_no_getenv(self):
        """config.py must not call os.getenv() directly."""
        import inspect
        import codewiki.src.config as config_mod

        source = inspect.getsource(config_mod)
        assert "os.getenv" not in source, "config.py still contains os.getenv() calls"

    def test_config_module_has_no_load_dotenv(self):
        """config.py must not import or call load_dotenv."""
        import inspect
        import codewiki.src.config as config_mod

        source = inspect.getsource(config_mod)
        assert "load_dotenv" not in source, "config.py still references load_dotenv"

    def test_no_hardcoded_sk_1234(self):
        """The placeholder API key must be gone."""
        import inspect
        import codewiki.src.config as config_mod

        source = inspect.getsource(config_mod)
        assert "sk-1234" not in source


class TestValidateLlmCredentials:
    def test_raises_when_no_provider_has_key(self):
        from codewiki.src.be.llm_services import validate_llm_credentials
        from unittest.mock import MagicMock

        config = MagicMock()
        config.main_model = "nonexistent-model"
        config.providers = []
        config.llm_api_key = ""
        config.llm_base_url = ""

        with pytest.raises(RuntimeError, match="No API key"):
            validate_llm_credentials(config)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config_toml_only.py -v`
Expected: FAIL (config.py still has os.getenv)

- [ ] **Step 3: Clean up config.py**

```python
# codewiki/src/config.py — new version (top section only)

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import os

# Filename constants (not configuration — these are structural)
OUTPUT_BASE_DIR = "output"
DEPENDENCY_GRAPHS_DIR = "dependency_graphs"
DOCS_DIR = "docs"
FIRST_MODULE_TREE_FILENAME = "first_module_tree.json"
MODULE_TREE_FILENAME = "module_tree.json"
OVERVIEW_FILENAME = "overview.md"
GENERATION_STATE_FILENAME = "generation_state.json"
INTERNAL_SUBDIR = ".codewiki"

# Default values (used by Config dataclass, overridden by TOML)
MAX_DEPTH = 2
DEFAULT_MAX_TOKENS = 32_768
DEFAULT_MAX_TOKEN_PER_MODULE = 36_369
DEFAULT_MAX_TOKEN_PER_LEAF_MODULE = 16_000
DEFAULT_MAX_CONCURRENT = 3
DEFAULT_MAX_RETRIES = 2
DEFAULT_LONG_CONTEXT_THRESHOLD = 200_000


def internal_file_path(working_dir: str, filename: str) -> str:
    """Return the path for an internal/cache file inside the .codewiki subdir."""
    subdir = os.path.join(working_dir, INTERNAL_SUBDIR)
    os.makedirs(subdir, exist_ok=True)
    return os.path.join(subdir, filename)
```

Delete: `load_dotenv()`, `from dotenv import load_dotenv`, all `os.getenv()` lines (`MAIN_MODEL`, `FALLBACK_MODEL_1`, `CLUSTER_MODEL`, `LLM_BASE_URL`, `LLM_API_KEY`), legacy aliases (`MAX_TOKEN_PER_MODULE`, `MAX_TOKEN_PER_LEAF_MODULE`).

Keep (for now): `_CLI_CONTEXT`, `set_cli_context()`, `is_cli_context()` — still called by `codewiki/cli/adapters/doc_generator.py:185`. Will be removed in Architecture sub-project A1.

The `Config` dataclass stays as-is — its fields are populated by `config_loader.py` from TOML.

- [ ] **Step 4: Add `validate_llm_credentials()` to llm_services.py**

```python
# codewiki/src/be/llm_services.py — add after _get_provider_api_key (line ~83)

def validate_llm_credentials(config) -> None:
    """Raise RuntimeError if the main model cannot resolve to a provider with an API key.

    Called at startup (CLI generate, web app init) to fail fast.
    Uses the real provider resolution path: _has_provider_registry → _get_provider_config
    → _get_provider_api_key for TOML configs, or config.llm_api_key for legacy.
    """
    model = config.main_model
    if not model:
        raise RuntimeError("No main_model configured. Set it in config.toml.")
    try:
        if _has_provider_registry(config):
            provider_config, _ = _get_provider_config(config, model)
            api_key = _get_provider_api_key(provider_config)
            if not api_key:
                raise RuntimeError(
                    f"No API key for model {model!r} (provider={provider_config.name!r}). "
                    f"Set it in config.toml under [[providers]] api_keys."
                )
        else:
            if not config.llm_api_key:
                raise RuntimeError(
                    f"No API key configured for model {model!r}. "
                    f"Set it in config.toml under [[providers]] api_keys."
                )
    except (ValueError, AttributeError) as exc:
        raise RuntimeError(
            f"Cannot resolve provider for model {model!r}: {exc}"
        ) from exc
```

**Note on `set_cli_context` / `is_cli_context`:** These are still called by `codewiki/cli/adapters/doc_generator.py:185`. Do NOT delete them in this task — they will be removed when the config model is unified (Architecture sub-project A1). For now, keep them in `config.py` but remove their dependency on `os.getenv`.

- [ ] **Step 5: Wire `validate_llm_credentials()` into real startup paths**

In `codewiki/cli/commands/generate.py`, in the `generate` command function, after the `Config` object is built from TOML and before `DocumentationGenerator` is created, add:

```python
from codewiki.src.be.llm_services import validate_llm_credentials
validate_llm_credentials(config)
```

This ensures the CLI fails fast with a clear error if the TOML config has no valid provider+key for the main model.

Web app startup (`web_app.py`) is out of scope for this sub-project (user decision: "web 先不管"). It will be wired in when the web worker is addressed.

- [ ] **Step 6: Update docker/env.example**

```
# CodeWiki Docker Configuration
#
# CodeWiki uses TOML for all configuration.
# Mount your config.toml into the container:
#
#   docker run -v ./config.toml:/app/config.toml codewiki
#
# See config.example.toml for all available settings.
#
# Environment variables are NOT used directly.
# To reference env vars in TOML, use the env: prefix:
#   api_key = "env:MY_API_KEY"
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_config_toml_only.py -v`
Expected: All PASS

- [ ] **Step 8: Run existing tests to check for regressions**

Run: `pytest tests/ -q -k "not network" --timeout=30`
Expected: All existing tests still pass (config_loader tests may need updates if they relied on env var defaults)

- [ ] **Step 9: Commit**

```bash
git add codewiki/src/config.py codewiki/src/be/llm_services.py codewiki/cli/commands/generate.py docker/env.example tests/test_config_toml_only.py
git commit -m "fix(config): remove all os.getenv fallbacks, TOML is the only config source"
```

---

### Task 7: C5 — Scheduler coordinator coroutine model

**Files:**
- Modify: `codewiki/src/be/documentation_scheduler.py:86-294`
- Create: `tests/test_scheduler_coordinator.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scheduler_coordinator.py
import asyncio
import pytest


@pytest.mark.asyncio
async def test_coordinator_processes_leaves_before_parents():
    """Children must complete before their parent is dispatched."""
    from codewiki.src.be.documentation_scheduler import run_module_queue

    execution_order = []

    async def mock_process(name, components, core_ids, path, working_dir,
                           tree_manager, **kwargs):
        execution_order.append("/".join(path))
        return {}, "mock-model"

    async def mock_root_overview():
        execution_order.append("__root__")

    tree = {
        "Parent": {
            "components": ["a"],
            "children": {
                "Child1": {"components": ["b"], "children": {}},
                "Child2": {"components": ["c"], "children": {}},
            },
        },
    }

    class FakeConfig:
        max_concurrent = 2

    await run_module_queue(
        config=FakeConfig(),
        graph_tree=tree,
        components={"a": None, "b": None, "c": None},
        working_dir="/tmp/fake",
        tree_manager=None,
        process_module=mock_process,
        generate_root_overview=mock_root_overview,
        include_root=True,
        progress_factory=lambda **kw: _NoopProgress(),
    )

    # Children before parent, parent before root
    parent_idx = execution_order.index("Parent")
    child1_idx = execution_order.index("Parent/Child1")
    child2_idx = execution_order.index("Parent/Child2")
    root_idx = execution_order.index("__root__")
    assert child1_idx < parent_idx
    assert child2_idx < parent_idx
    assert parent_idx < root_idx


@pytest.mark.asyncio
async def test_coordinator_handles_failed_task():
    """A failed task must not block other tasks from completing."""
    from codewiki.src.be.documentation_scheduler import run_module_queue

    call_count = 0

    async def mock_process(name, components, core_ids, path, working_dir,
                           tree_manager, **kwargs):
        nonlocal call_count
        call_count += 1
        if name == "FailChild":
            raise RuntimeError("intentional failure")
        return {}, "mock"

    tree = {
        "Parent": {
            "components": [],
            "children": {
                "GoodChild": {"components": ["a"], "children": {}},
                "FailChild": {"components": ["b"], "children": {}},
            },
        },
    }

    class FakeConfig:
        max_concurrent = 2

    await run_module_queue(
        config=FakeConfig(),
        graph_tree=tree,
        components={"a": None, "b": None},
        working_dir="/tmp/fake",
        tree_manager=None,
        process_module=mock_process,
        include_root=False,
        progress_factory=lambda **kw: _NoopProgress(),
    )
    # GoodChild should still have been processed even though FailChild failed
    assert call_count >= 2


class _NoopProgress:
    def update(self, n=1): pass
    def set_postfix_str(self, s, refresh=False): pass
    def close(self): pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scheduler_coordinator.py -v`
Expected: May pass with current code (behavior is the same), or fail if coordinator API differs

- [ ] **Step 3: Refactor `run_module_queue` with coordinator model**

The key structural change: replace the worker-side `pending_count` mutation with a coordinator coroutine.

```python
# Inside run_module_queue, replace the worker + lock + pending_count pattern:

    # --- New: two queues ---
    work_queue: asyncio.Queue[str] = asyncio.Queue()
    done_queue: asyncio.Queue[tuple[str, bool]] = asyncio.Queue()  # (key, success)

    # Seed work_queue with leaf tasks
    for key, (_, _, _, is_leaf) in all_tasks.items():
        if is_leaf:
            await work_queue.put(key)

    # --- Coordinator: single coroutine, owns all dependency state ---
    async def _coordinator():
        completed_count = 0
        while completed_count < total_tasks:
            key, success = await done_queue.get()
            completed_count += 1
            progress.update(1)
            if not success:
                continue  # failed task — don't unblock parent
            parent_key = child_to_parent.get(key)
            if parent_key is not None:
                pending_count[parent_key] -= 1
                if pending_count[parent_key] == 0:
                    del pending_count[parent_key]
                    if parent_key == ROOT_KEY:
                        logger.info("🔓 All top-level modules done — enqueueing root overview")
                    else:
                        logger.info("🔓 Parent unblocked: %s", all_tasks[parent_key][1])
                    await work_queue.put(parent_key)

    # --- Worker: takes from work_queue, reports to done_queue ---
    async def _worker(_worker_id: int):
        while True:
            try:
                key = await work_queue.get()
            except asyncio.CancelledError:
                return
            label = "overview" if key == ROOT_KEY else all_tasks[key][1]
            success = False
            try:
                # ... existing retry/execute logic (unchanged) ...
                success = True
            except Exception as e:
                logger.error("✗ Failed '%s': %s", label, e)
                # ... existing error handling ...
            finally:
                await done_queue.put((key, success))
```

The retry logic, tqdm postfix, model tracking, state_mgr calls inside the worker all stay identical. Only the dependency coordination moves out.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scheduler_coordinator.py tests/test_documentation_scheduler.py tests/test_documentation_generator_worker_cleanup.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/documentation_scheduler.py tests/test_scheduler_coordinator.py
git commit -m "refactor(scheduler): replace lock+pending_count with coordinator coroutine"
```

---

### Task 8: S1 — HTML sanitization with nh3

**Files:**
- Create: `codewiki/src/fe/html_sanitizer.py`
- Modify: `codewiki/src/fe/templates.py:490`
- Modify: `codewiki/src/fe/visualise_docs.py:179-185`
- Modify: `pyproject.toml`
- Create: `tests/test_html_sanitizer.py`

- [ ] **Step 1: Add nh3 dependency**

In `pyproject.toml`, add to `[project] dependencies`:

```toml
"nh3>=0.2.15",
```

Run: `pip install nh3`

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_html_sanitizer.py
import pytest


class TestSanitizeHtml:
    def test_strips_script_tags(self):
        from codewiki.src.fe.html_sanitizer import sanitize_html

        dirty = '<p>Hello</p><script>alert("xss")</script>'
        clean = sanitize_html(dirty)
        assert "<script>" not in clean
        assert "alert" not in clean
        assert "<p>Hello</p>" in clean

    def test_strips_onerror_attribute(self):
        from codewiki.src.fe.html_sanitizer import sanitize_html

        dirty = '<img src="x" onerror="alert(1)">'
        clean = sanitize_html(dirty)
        assert "onerror" not in clean
        assert "<img" in clean  # tag preserved, attribute stripped

    def test_strips_javascript_url(self):
        from codewiki.src.fe.html_sanitizer import sanitize_html

        dirty = '<a href="javascript:alert(1)">click</a>'
        clean = sanitize_html(dirty)
        assert "javascript:" not in clean

    def test_strips_data_url(self):
        from codewiki.src.fe.html_sanitizer import sanitize_html

        dirty = '<a href="data:text/html,<script>alert(1)</script>">click</a>'
        clean = sanitize_html(dirty)
        assert "data:" not in clean

    def test_allows_safe_markdown_html(self):
        from codewiki.src.fe.html_sanitizer import sanitize_html

        safe = (
            '<h1>Title</h1><p>Text with <strong>bold</strong> and '
            '<a href="https://example.com">link</a></p>'
            '<pre><code class="language-python">print("hi")</code></pre>'
        )
        result = sanitize_html(safe)
        assert "<h1>Title</h1>" in result
        assert "<strong>bold</strong>" in result
        assert 'href="https://example.com"' in result
        assert 'class="language-python"' in result

    def test_allows_mermaid_div(self):
        from codewiki.src.fe.html_sanitizer import sanitize_html

        html = '<div class="mermaid">graph TD; A-->B</div>'
        result = sanitize_html(html)
        assert 'class="mermaid"' in result
        assert "graph TD" in result

    def test_strips_iframe(self):
        from codewiki.src.fe.html_sanitizer import sanitize_html

        dirty = '<iframe src="https://evil.com"></iframe>'
        clean = sanitize_html(dirty)
        assert "<iframe" not in clean

    def test_strips_style_attribute(self):
        from codewiki.src.fe.html_sanitizer import sanitize_html

        dirty = '<div style="background:url(javascript:alert(1))">hi</div>'
        clean = sanitize_html(dirty)
        assert "style=" not in clean

    def test_strips_svg_tags(self):
        from codewiki.src.fe.html_sanitizer import sanitize_html

        dirty = '<svg onload="alert(1)"><circle r="50"/></svg>'
        clean = sanitize_html(dirty)
        assert "<svg" not in clean

    def test_preserves_table_structure(self):
        from codewiki.src.fe.html_sanitizer import sanitize_html

        html = '<table><thead><tr><th>Col</th></tr></thead><tbody><tr><td>Val</td></tr></tbody></table>'
        result = sanitize_html(html)
        assert "<table>" in result
        assert "<th>Col</th>" in result
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_html_sanitizer.py -v`
Expected: FAIL (module does not exist yet)

- [ ] **Step 4: Create html_sanitizer.py**

```python
# codewiki/src/fe/html_sanitizer.py
"""HTML sanitization for LLM-generated documentation content.

Uses nh3 to strip dangerous tags/attributes while preserving
standard markdown-rendered HTML. Mermaid is rendered client-side
from text content, so no SVG tags are needed.
"""
import nh3

_ALLOWED_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "br", "hr",
    "ul", "ol", "li", "dl", "dt", "dd",
    "strong", "em", "b", "i", "u", "s", "del", "ins",
    "code", "pre", "blockquote", "kbd",
    "a", "img",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption",
    "div", "span", "sup", "sub",
    "details", "summary",
}

_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title", "id", "class"},
    "img": {"src", "alt", "title", "width", "height"},
    "div": {"class", "id", "data-nav", "data-nav-sub"},
    "span": {"class", "id"},
    "code": {"class"},
    "pre": {"class"},
    "td": {"align", "colspan", "rowspan"},
    "th": {"align", "colspan", "rowspan"},
}


def sanitize_html(html: str) -> str:
    """Remove dangerous tags/attributes while preserving markdown rendering."""
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        link_rel="noopener noreferrer",
        url_schemes={"http", "https", "mailto"},
    )
```

- [ ] **Step 5: Wire into templates.py**

In `codewiki/src/fe/templates.py`, find where `content` is passed to the template rendering context. Before the template render call, add:

```python
from codewiki.src.fe.html_sanitizer import sanitize_html
content = sanitize_html(content)
```

The `{{ content | safe }}` at line 490 stays — it is now safe because `content` has been sanitized.

- [ ] **Step 6: Wire into visualise_docs.py**

In `codewiki/src/fe/visualise_docs.py`, modify the Mermaid handler (line 179-185):

```python
    def replace_mermaid(match):
        mermaid_code = match.group(1)
        import html as html_mod
        mermaid_code = html_mod.unescape(mermaid_code)
        # Strip ALL HTML tags from Mermaid text content (it's diagram source, not HTML)
        mermaid_code = nh3.clean(mermaid_code, tags=set(), attributes={})
        return f'<div class="mermaid">{mermaid_code}</div>'
```

And sanitize the full rendered HTML before returning:

```python
    from codewiki.src.fe.html_sanitizer import sanitize_html
    # Sanitize after all markdown processing is done
    html_output = sanitize_html(html_output)
    return html_output
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_html_sanitizer.py -v`
Expected: All PASS

- [ ] **Step 8: Run existing tests**

Run: `pytest tests/test_static_generator_corner_cases.py -v`
Expected: PASS (static generator is separate from web renderer)

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml codewiki/src/fe/html_sanitizer.py codewiki/src/fe/templates.py codewiki/src/fe/visualise_docs.py tests/test_html_sanitizer.py
git commit -m "fix(security): add HTML sanitization with nh3 to prevent stored XSS"
```

---

### Task 9: Full regression verification

- [ ] **Step 1: Compile check all modified files**

```bash
python3 -m py_compile codewiki/src/utils.py codewiki/src/be/llm_services.py codewiki/src/be/generation_state.py codewiki/src/be/agent_tools/str_replace_editor.py codewiki/src/fe/routes.py codewiki/src/config.py codewiki/src/fe/html_sanitizer.py codewiki/src/fe/templates.py codewiki/src/fe/visualise_docs.py codewiki/src/be/documentation_scheduler.py
```

- [ ] **Step 2: Run all new tests**

```bash
pytest -v tests/test_file_io_safety.py tests/test_llm_response_guard.py tests/test_state_load_resilience.py tests/test_write_failure_propagation.py tests/test_commit_id_validation.py tests/test_config_toml_only.py tests/test_html_sanitizer.py tests/test_scheduler_coordinator.py
```

- [ ] **Step 3: Run full existing regression suite**

```bash
pytest -q tests/ -k "not network" --timeout=60
```

- [ ] **Step 4: Fix any regressions, commit**

```bash
git commit -m "test: verify security + correctness audit fixes"
```
