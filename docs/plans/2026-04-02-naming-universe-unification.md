# Naming Universe Unification — Implementation Plan (v7)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the "split-brain" where four independent naming universes produce incompatible filenames, causing 74% broken links, duplicate files, and language drift.

**Architecture:** Replace module/overview caching (file-exists checks, `_completed` flags, `_parent_doc_hashes.json`, `_tree_cache_meta.json`) with a single **`generation_state.json`** — an explicit task ledger where each doc unit has a stable `doc_id`, a frozen `output_file`, dependencies, status, and input fingerprint. Guide cache (`_guide_cache.json`) is NOT migrated in v7 — it is only relocated to `.codewiki/`. Canonical filenames are computed via a two-pass collision-aware algorithm and frozen in the ledger. All mutations go through `GenerationStateManager` (async-safe). All reads and writes go through the ledger, not through fuzzy filename lookups.

**Tech Stack:** Python 3.10+, pytest, codewiki internals

---

## v6 → v7 Changes

| # | v6 Gap | v7 Fix |
|---|--------|--------|
| 1 | Scattered implicit caching: `_completed` flags, `_parent_doc_hashes.json`, `_tree_cache_meta.json`, and 6+ "file exists + size > 100" checks, each with independent invalidation logic | **New Task 0: `generation_state.json`** — single explicit task ledger for modules/overviews. Each doc unit has stable `doc_id`, frozen `output_file`, status, input fingerprint. Replaces `_completed`, `_parent_doc_hashes.json`, `_tree_cache_meta.json`. Guide cache (`_guide_cache.json`) stays as-is, relocated to `.codewiki/` only. |
| 2 | `_doc_filename` was a field on tree nodes — mixing structural data (tree) with execution state (filename/completion) | Tree stays structural. `generation_state.json` owns execution state: `output_file`, `status`, `content_hash`, `model`, `language`. |
| 3 | `find_doc_by_node()` navigated the tree to find `_doc_filename` — complex for deeply nested modules | `GenerationState.get_task(doc_id).output_file` — flat O(1) lookup by stable ID. |

Prior fixes retained: two-pass filename freeze, read path migration, sub-agent timing, collision-aware dedup, link rewriter, language injection.

---

## Root Cause Analysis

The module_tree carries two identity systems:

| Field | Design intent | Stability | Uniqueness |
|-------|-------------|-----------|------------|
| **dict key** (title) | Display name, bilingual (`models.py:44`) | Low — LLM-generated, changes with naming/language | Unique per level |
| **`path`** | Document path (`models.py:45`) | High — derived from filesystem structure | Enforced unique by `validate_tree()` (`models.py:144`) |
| **`module_id`** | Stable identity (`models.py:43`) | Highest — SHA-256 of components | Globally unique (hash) |

Current file-writing uses dict keys. Link map uses `path`. Prompts teach a third rule. Agents write freely.

**Decision: `path` is the PREFERRED canonical source, but collisions are real and must be handled.**

Actual v1 tree data (cc-leaked, 139 nodes):
- 56 nodes have `path: ""` (40% — all depth-2 leaf modules)
- `utils` appears 14 times across different modules
- `cli/transports`, `services/mcp`, `components` each appear 3 times
- Only 60 unique paths across 139 nodes

So `path` alone is insufficient. The `_doc_filename` freeze algorithm is:

1. **Unique path** → `module_doc_filename([path])` — stable, filesystem-derived
2. **Colliding path** → `module_doc_filename([parent_frozen_stem, key])` — tree key used as ONE-TIME disambiguator, then frozen forever. Title drift doesn't matter because `_doc_filename` is never recomputed after first freeze.
3. **Empty path** → same as colliding: `module_doc_filename([parent_frozen_stem, key])`

Since we walk top-down, the parent's `_doc_filename` is always set before children are processed.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `codewiki/src/be/generation_state.py` | Create | `GenerationState` class: task ledger model, atomic persistence, status transitions, staleness detection |
| `codewiki/src/utils.py` | Modify | Aggressive normalization in `module_doc_filename()` |
| `codewiki/src/be/documentation_generator.py` | Modify | Build ledger from tree, replace all cache checks with ledger lookups, remove `_parent_doc_hashes.json` / `_tree_cache_meta.json` |
| `codewiki/src/be/generation/glossary.py` | Modify | `build_link_map()` reads `output_file` from ledger tasks |
| `codewiki/src/be/prompt_template.py` | Modify | Include canonical filename in prompt; strengthen overview language |
| `codewiki/src/be/agent_orchestrator.py` | Modify | Check ledger status instead of file-exists; set `assigned_doc_filename` on deps |
| `codewiki/src/be/agent_tools/str_replace_editor.py` | Modify | Validate agent writes match `assigned_doc_filename` |
| `codewiki/src/be/agent_tools/generate_sub_module_documentations.py` | Modify | Check ledger for sub-module status; set `assigned_doc_filename` |
| `codewiki/src/be/agent_tools/deps.py` | Modify | Add `assigned_doc_filename` field to `CodeWikiDeps` |
| `codewiki/src/be/module_tree_manager.py` | Modify | Remove `mark_completed()` / `_completed` flag (moved to ledger) |
| `codewiki/src/be/guide_generator.py` | Modify | Move `_guide_cache.json` to `.codewiki/` (guide NOT migrated to ledger in v7) |
| `codewiki/src/be/postprocess/link_rewriter.py` | Create | Pre-validation link fixer |
| `codewiki/src/be/docs_fixer.py` | Modify | Phase 4a rewrite → Phase 4b validate |
| `codewiki/src/config.py` | Modify | Add `GENERATION_STATE_FILENAME`, `postprocess_fix_links`, `internal_file_path()` |
| `codewiki/cli/static_generator.py` | Modify | Read `output_file` from ledger for nav; exclude internal files |
| `tests/test_generation_state.py` | Create | Ledger model, staleness, atomic write, status transition tests |
| `tests/test_module_doc_filename.py` | Create | Normalization tests |
| `tests/test_link_rewriter.py` | Create | Link fixer tests |
| `tests/test_overview_language.py` | Create | Language injection tests |

---

### Task 0: Create `generation_state.json` ledger model

**Files:**
- Create: `codewiki/src/be/generation_state.py`
- Modify: `codewiki/src/config.py`
- Create: `tests/test_generation_state.py`

This is the foundation. `generation_state.json` replaces module/overview caching:
- `_parent_doc_hashes.json` (parent doc staleness)
- `_tree_cache_meta.json` (tree invalidation)
- `_completed` flag on tree nodes
- All "file exists + size > 100" implicit cache checks for modules/overviews

**Scope boundary:** Guide cache (`_guide_cache.json`) is NOT migrated in v7. Guide tasks will be added to the ledger in a future iteration. For now, `_guide_cache.json` continues to work as-is (moved to `.codewiki/` directory only).

**Concurrency model:** `GenerationState` is a **pure data container** — it exposes only read/query methods. ALL mutations (add, update, promote, register) go through `GenerationStateManager`, which holds an `asyncio.Lock` for every read-modify-write-save cycle. No code path may call `GenerationState.add_task()`, `update_task_status()`, `mark_stale_tasks()`, `promote_ready()`, or `save()` directly — these are only called from inside the manager. This mirrors `ModuleTreeManager` (`module_tree_manager.py:24-62`).

Each doc unit gets a stable `doc_id`, a frozen `output_file`, and explicit 7-state lifecycle.

**Status lifecycle:**
```
planned → ready → running → completed
                          → failed → ready (retry)
                completed → stale → ready (re-run)
          planned|ready → skipped
```
- `planned`: registered in ledger, dependencies not yet satisfied
- `ready`: dependencies satisfied, available for worker pickup
- `running`: worker has claimed it, prevents concurrent duplication
- `completed`: generated successfully, output verified
- `failed`: generation attempt failed, can be retried
- `stale`: was completed, but input_hash / config / prompt_version changed
- `skipped`: explicitly excluded this run (strategy decision)

`blocked` is NOT a stored status — it's inferred as `planned` where `depends_on` has unfinished tasks.

**Task sources:**
- `source = "manifest"`: known from clustering/tree at init time
- `source = "discovered"`: registered at runtime by a parent task (e.g., agent splits into sub-modules, guide outline expands into sections)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_generation_state.py
import json
import os
import pytest
from codewiki.src.be.generation_state import GenerationState, DocTask


class TestDocTask:
    def test_create_with_planned_status(self):
        task = DocTask(
            doc_id="module:cli",
            kind="module",
            module_path=["CLI Transport"],
            output_file="cli.md",
        )
        assert task.status == "planned"
        assert task.source == "manifest"

    def test_discovered_task(self):
        task = DocTask(
            doc_id="module:cli-io",
            kind="module",
            module_path=["CLI Transport", "io_abstractions"],
            output_file="cli-io_abstractions.md",
            source="discovered",
            parent_doc_id="module:cli",
        )
        assert task.source == "discovered"
        assert task.parent_doc_id == "module:cli"

    def test_mark_completed(self):
        task = DocTask(doc_id="module:cli", kind="module",
                       module_path=["CLI"], output_file="cli.md",
                       status="running")
        task.mark_completed(content_hash="sha256:abc", model="gpt-4o")
        assert task.status == "completed"
        assert task.content_hash == "sha256:abc"
        assert task.attempt_count == 1

    def test_mark_failed_increments_attempts(self):
        task = DocTask(doc_id="module:cli", kind="module",
                       module_path=["CLI"], output_file="cli.md",
                       status="running")
        task.mark_failed("timeout")
        assert task.status == "failed"
        assert task.attempt_count == 1
        assert task.last_error == "timeout"

    def test_is_stale_on_input_change(self):
        task = DocTask(doc_id="module:cli", kind="module",
                       module_path=["CLI"], output_file="cli.md",
                       status="completed", input_hash="old")
        assert task.is_stale(current_input_hash="new")
        assert not task.is_stale(current_input_hash="old")

    def test_is_stale_ignores_non_completed(self):
        task = DocTask(doc_id="module:cli", kind="module",
                       module_path=["CLI"], output_file="cli.md",
                       status="failed", input_hash="old")
        assert not task.is_stale(current_input_hash="new")


class TestGenerationState:
    def test_add_and_get(self):
        state = GenerationState()
        state._add_task(DocTask(doc_id="module:cli", kind="module",
                               module_path=["CLI"], output_file="cli.md"))
        assert state.get_task("module:cli").output_file == "cli.md"
        assert state.get_output_file("module:cli") == "cli.md"

    def test_no_duplicate_output_files(self):
        state = GenerationState()
        state._add_task(DocTask(doc_id="a", kind="module",
                               module_path=["A"], output_file="cli.md"))
        with pytest.raises(ValueError, match="output_file.*already assigned"):
            state._add_task(DocTask(doc_id="b", kind="module",
                                   module_path=["B"], output_file="cli.md"))

    def test_actionable_tasks(self):
        state = GenerationState()
        state._add_task(DocTask(doc_id="a", kind="module", module_path=["A"],
                               output_file="a.md", status="completed"))
        state._add_task(DocTask(doc_id="b", kind="module", module_path=["B"],
                               output_file="b.md", status="ready"))
        state._add_task(DocTask(doc_id="c", kind="module", module_path=["C"],
                               output_file="c.md", status="failed"))
        state._add_task(DocTask(doc_id="d", kind="module", module_path=["D"],
                               output_file="d.md", status="stale"))
        state._add_task(DocTask(doc_id="e", kind="module", module_path=["E"],
                               output_file="e.md", status="skipped"))
        actionable = state.actionable_task_ids()
        assert set(actionable) == {"b", "c", "d"}

    def test_ready_tasks_respects_deps(self):
        state = GenerationState()
        state._add_task(DocTask(doc_id="parent", kind="overview", module_path=["P"],
                               output_file="p.md", status="planned",
                               depends_on=["child1", "child2"]))
        state._add_task(DocTask(doc_id="child1", kind="module", module_path=["C1"],
                               output_file="c1.md", status="completed"))
        state._add_task(DocTask(doc_id="child2", kind="module", module_path=["C2"],
                               output_file="c2.md", status="planned"))
        ready = state.ready_task_ids()
        assert "parent" not in ready  # child2 not done
        assert "child2" in ready     # no deps

    def test_promote_to_ready(self):
        state = GenerationState()
        state._add_task(DocTask(doc_id="a", kind="module", module_path=["A"],
                               output_file="a.md", status="planned",
                               depends_on=["b"]))
        state._add_task(DocTask(doc_id="b", kind="module", module_path=["B"],
                               output_file="b.md", status="planned"))
        state._promote_ready()
        assert state.get_task("b").status == "ready"  # no deps → ready
        assert state.get_task("a").status == "planned"  # b not done

    def test_mark_stale(self):
        state = GenerationState()
        state._add_task(DocTask(doc_id="a", kind="module", module_path=["A"],
                               output_file="a.md", status="completed",
                               input_hash="old"))
        state._mark_stale_tasks({"a": "new"})
        assert state.get_task("a").status == "stale"

    def test_register_discovered_task(self):
        state = GenerationState()
        state._add_task(DocTask(doc_id="parent", kind="module", module_path=["P"],
                               output_file="p.md", status="running"))
        state._register_discovered_task(DocTask(
            doc_id="child", kind="module", module_path=["P", "C"],
            output_file="p-c.md", source="discovered",
            parent_doc_id="parent",
        ))
        child = state.get_task("child")
        assert child.source == "discovered"
        assert child.status == "planned"

    def test_save_and_load(self, tmp_path):
        state = GenerationState(repo_commit="abc123")
        state._add_task(DocTask(doc_id="module:cli", kind="module",
                               module_path=["CLI"], output_file="cli.md",
                               status="completed", content_hash="sha256:xyz",
                               source="manifest"))
        path = tmp_path / "generation_state.json"
        state._save(str(path))
        loaded = GenerationState.load(str(path))
        assert loaded.repo_commit == "abc123"
        t = loaded.get_task("module:cli")
        assert t.content_hash == "sha256:xyz"
        assert t.source == "manifest"
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `pytest tests/test_generation_state.py -v`

- [ ] **Step 3: Implement `GenerationState` and `DocTask`**

```python
# codewiki/src/be/generation_state.py
"""Generation state ledger — single source of truth for doc generation status.

Replaces: _parent_doc_hashes.json, _tree_cache_meta.json, _guide_cache.json,
_completed flags, and all implicit "file exists + size > 100" cache checks.

Status lifecycle:
    planned → ready → running → completed
                              → failed → ready (retry)
                    completed → stale → ready (re-run)
              planned|ready → skipped
"""
import json
import os
import tempfile
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

GENERATION_STATE_FILENAME = "generation_state.json"
SCHEMA_VERSION = "codewiki.generation_state.v1"

_ACTIONABLE = frozenset({"ready", "failed", "stale"})
_TERMINAL = frozenset({"completed", "skipped"})


@dataclass
class DocTask:
    """A single documentation generation unit."""
    doc_id: str                    # stable ID: "module:<stem>" or "guide:<slug>"
    kind: str                      # "module", "overview", "guide", "guide_section"
    module_path: list[str]         # tree key path (for context/display, not naming)
    output_file: str               # frozen canonical filename
    depends_on: list[str] = field(default_factory=list)
    status: str = "planned"        # planned|ready|running|completed|failed|stale|skipped
    source: str = "manifest"       # "manifest" or "discovered"
    parent_doc_id: str = ""        # for discovered tasks: who spawned me
    input_hash: str = ""           # hash of component content + prompt version
    content_hash: str = ""         # hash of generated output
    prompt_version: str = ""
    language: str = "en"
    model: str = ""
    attempt_count: int = 0
    last_error: str = ""
    updated_at: str = ""

    def mark_running(self) -> None:
        self.status = "running"
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def mark_completed(self, content_hash: str, model: str = "") -> None:
        self.status = "completed"
        self.content_hash = content_hash
        self.model = model
        self.last_error = ""
        self.attempt_count += 1
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def mark_failed(self, error: str) -> None:
        self.status = "failed"
        self.last_error = error
        self.attempt_count += 1
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def is_stale(self, current_input_hash: str = "") -> bool:
        if self.status != "completed":
            return False
        return bool(current_input_hash and current_input_hash != self.input_hash)


class GenerationState:
    """Task ledger for documentation generation."""

    def __init__(self, repo_commit: str = "", config_fingerprint: str = ""):
        self.schema_version = SCHEMA_VERSION
        self.repo_commit = repo_commit
        self.config_fingerprint = config_fingerprint
        self.tasks: dict[str, DocTask] = {}
        self._output_file_index: dict[str, str] = {}

    # ── Mutation methods (call ONLY from GenerationStateManager) ────────

    def _add_task(self, task: DocTask) -> None:
        existing_owner = self._output_file_index.get(task.output_file)
        if existing_owner and existing_owner != task.doc_id:
            raise ValueError(
                f"output_file {task.output_file!r} already assigned to "
                f"{existing_owner!r}, cannot assign to {task.doc_id!r}"
            )
        self.tasks[task.doc_id] = task
        self._output_file_index[task.output_file] = task.doc_id

    def _register_discovered_task(self, task: DocTask) -> None:
        task.source = "discovered"
        if task.status not in ("planned", "ready"):
            task.status = "planned"
        self._add_task(task)

    # ── Query methods (safe to call without lock) ─────────────────────

    def get_task(self, doc_id: str) -> Optional[DocTask]:
        return self.tasks.get(doc_id)

    def get_output_file(self, doc_id: str) -> Optional[str]:
        task = self.tasks.get(doc_id)
        return task.output_file if task else None

    def actionable_task_ids(self) -> list[str]:
        return [tid for tid, t in self.tasks.items() if t.status in _ACTIONABLE]

    def ready_task_ids(self) -> list[str]:
        """Return doc_ids whose deps are all satisfied and status allows execution.

        A task is ready if:
        - status is 'planned' and all depends_on are completed/skipped, OR
        - status is 'ready' (already promoted), OR
        - status is 'failed' or 'stale' (retriable)
        """
        result = []
        for tid, t in self.tasks.items():
            if t.status in ("ready", "failed", "stale"):
                result.append(tid)
            elif t.status == "planned":
                if all(
                    (dep_t := self.tasks.get(dep)) and dep_t.status in _TERMINAL
                    for dep in t.depends_on
                ):
                    result.append(tid)
        return result

    def _promote_ready(self) -> int:
        """Promote planned tasks whose deps are satisfied to ready. Returns count."""
        promoted = 0
        for tid in list(self.tasks):
            t = self.tasks[tid]
            if t.status != "planned":
                continue
            if all(
                (dep_t := self.tasks.get(dep)) and dep_t.status in _TERMINAL
                for dep in t.depends_on
            ):
                t.status = "ready"
                promoted += 1
        return promoted

    def _update_task_status(self, doc_id: str, status: str, **kwargs) -> None:
        task = self.tasks.get(doc_id)
        if not task:
            raise KeyError(f"Unknown doc_id: {doc_id}")
        task.status = status
        for k, v in kwargs.items():
            if hasattr(task, k):
                setattr(task, k, v)
        task.updated_at = datetime.now(timezone.utc).isoformat()

    def _mark_stale_tasks(self, current_input_hashes: dict[str, str]) -> None:
        for doc_id, current_hash in current_input_hashes.items():
            task = self.tasks.get(doc_id)
            if task and task.is_stale(current_hash):
                task.status = "stale"
                logger.info(f"Task {doc_id} marked stale (input changed)")

    def _save(self, path: str) -> None:
        """Atomic save: write to temp file, then os.replace."""
        data = {
            "schema_version": self.schema_version,
            "repo_commit": self.repo_commit,
            "config_fingerprint": self.config_fingerprint,
            "tasks": [asdict(t) for t in self.tasks.values()],
        }
        dir_name = os.path.dirname(path) or "."
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, path: str) -> "GenerationState":
        if not os.path.exists(path):
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        state = cls(
            repo_commit=data.get("repo_commit", ""),
            config_fingerprint=data.get("config_fingerprint", ""),
        )
        for td in data.get("tasks", []):
            task = DocTask(**{k: v for k, v in td.items()
                              if k in DocTask.__dataclass_fields__})
            state.tasks[task.doc_id] = task
            state._output_file_index[task.output_file] = task.doc_id
        return state
```

- [ ] **Step 4: Add `GenerationStateManager` (async-safe wrapper)**

```python
# Add to codewiki/src/be/generation_state.py

import asyncio

class GenerationStateManager:
    """Async-safe wrapper around GenerationState.

    All state mutations go through this manager, which holds an asyncio.Lock
    for the full read-modify-write cycle. Same pattern as ModuleTreeManager.
    """

    def __init__(self, state: GenerationState, persist_path: str):
        self._state = state
        self._persist_path = persist_path
        self._lock = asyncio.Lock()

    @property
    def state(self) -> GenerationState:
        """Read-only access to the underlying state (no lock needed for reads)."""
        return self._state

    async def add_task(self, task: DocTask) -> None:
        async with self._lock:
            self._state._add_task(task)
            self._state._save(self._persist_path)

    async def mark_running(self, doc_id: str) -> None:
        async with self._lock:
            self._state._update_task_status(doc_id, "running")
            self._state._save(self._persist_path)

    async def mark_completed(self, doc_id: str, content_hash: str,
                              model: str = "") -> None:
        async with self._lock:
            task = self._state.get_task(doc_id)
            if task:
                task.mark_completed(content_hash=content_hash, model=model)
            self._state._save(self._persist_path)

    async def mark_failed(self, doc_id: str, error: str) -> None:
        async with self._lock:
            task = self._state.get_task(doc_id)
            if task:
                task.mark_failed(error)
            self._state._save(self._persist_path)

    async def register_discovered_task(self, task: DocTask) -> None:
        async with self._lock:
            self._state._register_discovered_task(task)
            self._state._save(self._persist_path)

    async def promote_ready(self) -> int:
        async with self._lock:
            count = self._state._promote_ready()
            if count:
                self._state._save(self._persist_path)
            return count

    async def mark_stale(self, current_input_hashes: dict[str, str]) -> None:
        async with self._lock:
            self._state._mark_stale_tasks(current_input_hashes)
            self._state._save(self._persist_path)

    async def bulk_add_tasks(self, tasks: list[DocTask]) -> None:
        """Add multiple tasks in one locked cycle (for initial manifest build)."""
        async with self._lock:
            for task in tasks:
                self._state._add_task(task)
            self._state._save(self._persist_path)
```

- [ ] **Step 5: Add constant to config.py**

```python
# codewiki/src/config.py
GENERATION_STATE_FILENAME = 'generation_state.json'
```

- [ ] **Step 6: Run tests — expect PASS**

Run: `pytest tests/test_generation_state.py -v`

- [ ] **Step 6: Commit**

```bash
git add codewiki/src/be/generation_state.py codewiki/src/config.py tests/test_generation_state.py
git commit -m "feat(state): add generation_state.json ledger model"
```

---

### Task 1: Normalize `module_doc_filename()` — lowercase + strip special chars

**Files:**
- Modify: `codewiki/src/utils.py:64-91`
- Create: `tests/test_module_doc_filename.py`

`module_doc_filename()` becomes the pure normalization function. It does NOT choose what to normalize — callers pass the right input (the `path` field, not the title). `find_module_doc()` returns the matching file path but does NOT rename (dedup is a separate step).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_module_doc_filename.py
import pytest
from codewiki.src.utils import module_doc_filename, _normalize_for_match


class TestModuleDocFilename:
    def test_empty_path_returns_overview(self):
        assert module_doc_filename([]) == "overview.md"

    def test_single_part_lowercase(self):
        assert module_doc_filename(["AuthManager"]) == "authmanager.md"

    def test_spaces_become_underscores(self):
        assert module_doc_filename(["Auth Manager"]) == "auth_manager.md"

    def test_hyphens_become_underscores(self):
        assert module_doc_filename(["auth-manager"]) == "auth_manager.md"

    def test_slashes_become_underscores(self):
        assert module_doc_filename(["src/auth"]) == "src_auth.md"

    def test_ampersand_becomes_and(self):
        assert module_doc_filename(["Media & Data"]) == "media_and_data.md"

    def test_comma_stripped(self):
        assert module_doc_filename(["Query, Context"]) == "query_context.md"

    def test_multi_part_joined_by_hyphen(self):
        assert module_doc_filename(["cli", "transports"]) == "cli-transports.md"

    def test_consecutive_underscores_collapsed(self):
        assert module_doc_filename(["a  &  b"]) == "a_and_b.md"

    def test_path_style_input(self):
        """Path-field inputs like 'cli/transports' are typical."""
        assert module_doc_filename(["cli/transports"]) == "cli_transports.md"

    def test_path_with_parent(self):
        assert module_doc_filename(["services/mcp", "connection_mgr"]) == "services_mcp-connection_mgr.md"


class TestNormalizeForMatch:
    def test_ampersand_normalized(self):
        a = _normalize_for_match("Media_&_Data.md")
        b = _normalize_for_match("media_and_data.md")
        assert a == b

    def test_comma_normalized(self):
        a = _normalize_for_match("Query,_Context.md")
        b = _normalize_for_match("Query_Context.md")
        assert a == b

    def test_case_insensitive(self):
        a = _normalize_for_match("Auth-Manager.md")
        b = _normalize_for_match("auth_manager.md")
        assert a == b
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `pytest tests/test_module_doc_filename.py -v`

- [ ] **Step 3: Implement**

```python
# codewiki/src/utils.py — replace lines 64-91
import re as _re

def module_doc_filename(module_path: List[str]) -> str:
    """Build a stable, canonical markdown filename for a module path.

    Normalisation per part: & → _and_, strip non-alnum, spaces/hyphens/slashes → _,
    collapse underscores, lowercase.  Parts joined with ``-``.
    """
    parts = [p for p in module_path if p]
    if not parts:
        return "overview.md"
    safe_parts = []
    for p in parts:
        s = p.strip().replace("&", "_and_")
        s = _re.sub(r"[^\w\s-]", "", s)
        s = s.replace(" ", "_").replace("/", "_").replace("-", "_")
        s = s.lower()
        s = _re.sub(r"_+", "_", s).strip("_")
        safe_parts.append(s)
    safe_parts = [p for p in safe_parts if p]
    return f"{'-'.join(safe_parts)}.md" if safe_parts else "overview.md"

def _normalize_for_match(filename: str) -> str:
    """Normalise a filename for fuzzy comparison."""
    name = filename.lower().replace("&", "_and_")
    name = _re.sub(r"[^\w\s._-]", "", name)
    name = name.replace("-", "_").replace(" ", "_")
    return _re.sub(r"_+", "_", name)
```

`find_module_doc()` remains a lookup — it **does NOT rename**. Rename/dedup is Task 2.

- [ ] **Step 4: Run tests — expect PASS**
- [ ] **Step 5: Commit**

```bash
git add codewiki/src/utils.py tests/test_module_doc_filename.py
git commit -m "fix(naming): normalize filenames to lowercase with & → and, strip special chars"
```

---

### Task 2: Build ledger from tree + pre-generation dedup

**Files:**
- Modify: `codewiki/src/be/documentation_generator.py`
- Create: `tests/test_doc_filename_freeze.py`

Two mechanisms:

**A. Build ledger from module tree (two-pass filename computation):**

`build_generation_state(tree, existing_state)` walks the module tree and populates `GenerationState` with one `DocTask` per node. Filename computation uses the same two-pass collision-aware algorithm:

Pass 1: Collect all `path` values, build collision set (`path` appearing >1 time or empty).

Pass 2: Walk top-down. For each node:
- If existing ledger already has a task for this `doc_id` → reuse its `output_file` (frozen).
- If `path` is non-empty AND not in collision set → `module_doc_filename([path])`.
- Otherwise → `module_doc_filename([parent_frozen_stem, key])` where `parent_frozen_stem` is the parent task's `output_file` without `.md`, and `key` is the tree dict key (one-time disambiguator — frozen in ledger, title drift doesn't affect it).

The `doc_id` is derived from the `module_id` field (SHA-256 of components) when available, else `kind:` + the frozen output filename stem. This ensures `doc_id` is stable across title changes.

The ledger enforces: **one `doc_id` → one `output_file`**. Duplicates are impossible by construction.

**B. Pre-generation dedup (safe):** Same as v6 — scan output directory, group by normalized name, auto-delete similar content, warn on divergent content.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_doc_filename_freeze.py
import os
import json
import pytest
from codewiki.src.utils import module_doc_filename
from codewiki.src.be.documentation_generator import (
    freeze_doc_filenames,
    dedup_docs_directory,
)


class TestFreezeDocFilenames:
    def test_top_level_uses_path_field(self):
        tree = {
            "CLI Transport & Event Streaming": {
                "path": "cli",
                "components": ["a"],
                "children": {},
            }
        }
        freeze_doc_filenames(tree)
        node = tree["CLI Transport & Event Streaming"]
        assert node["_doc_filename"] == "cli.md"

    def test_child_with_unique_path_uses_it_directly(self):
        """Child whose path is globally unique → use path directly, no parent prefix."""
        tree = {
            "Parent Module": {
                "path": "services/mcp",
                "components": [],
                "children": {
                    "connection_mgr": {
                        "path": "services/mcp/conn",  # unique across tree
                        "components": ["x"],
                        "children": {},
                    }
                },
            }
        }
        freeze_doc_filenames(tree)
        child = tree["Parent Module"]["children"]["connection_mgr"]
        assert child["_doc_filename"] == "services_mcp_conn.md"

    def test_child_with_colliding_path_uses_parent_stem_plus_key(self):
        """Child whose path collides with another node → parent_stem + key."""
        tree = {
            "Parent A": {
                "path": "services/mcp",
                "components": [],
                "children": {
                    "child_x": {
                        "path": "utils",  # collides with child_y below
                        "components": ["a"],
                        "children": {},
                    }
                },
            },
            "Parent B": {
                "path": "query",
                "components": [],
                "children": {
                    "child_y": {
                        "path": "utils",  # collides with child_x above
                        "components": ["b"],
                        "children": {},
                    }
                },
            },
        }
        freeze_doc_filenames(tree)
        child_x = tree["Parent A"]["children"]["child_x"]
        child_y = tree["Parent B"]["children"]["child_y"]
        # Both have path="utils" → disambiguated by parent_stem + key
        assert child_x["_doc_filename"] == "services_mcp-child_x.md"
        assert child_y["_doc_filename"] == "query-child_y.md"
        assert child_x["_doc_filename"] != child_y["_doc_filename"]

    def test_child_without_path_uses_parent_stem_plus_key(self):
        """v1 format children lack path field — use parent stem + child key."""
        tree = {
            "Parent": {
                "path": "cli",
                "components": [],
                "children": {
                    "io_abstractions": {
                        "components": ["x"],
                        "children": {},
                    }
                },
            }
        }
        freeze_doc_filenames(tree)
        child = tree["Parent"]["children"]["io_abstractions"]
        assert child["_doc_filename"] == "cli-io_abstractions.md"

    def test_top_level_with_colliding_path_uses_path_plus_key(self):
        """Top-level nodes sharing a path get disambiguated by key."""
        tree = {
            "Query Intelligence": {
                "path": "utils",
                "components": ["a"],
                "children": {},
            },
            "Media Utilities": {
                "path": "utils",
                "components": ["b"],
                "children": {},
            },
        }
        freeze_doc_filenames(tree)
        assert tree["Query Intelligence"]["_doc_filename"] == "utils-query_intelligence.md"
        assert tree["Media Utilities"]["_doc_filename"] == "utils-media_utilities.md"

    def test_existing_doc_filename_preserved(self):
        """Once frozen, _doc_filename is not recomputed."""
        tree = {
            "Module": {
                "path": "old_path",
                "_doc_filename": "already_frozen.md",
                "components": [],
                "children": {},
            }
        }
        freeze_doc_filenames(tree)
        assert tree["Module"]["_doc_filename"] == "already_frozen.md"

    def test_top_level_without_path_uses_key(self):
        tree = {
            "Media & Data Utilities": {
                "components": ["x"],
                "children": {},
            }
        }
        freeze_doc_filenames(tree)
        assert tree["Media & Data Utilities"]["_doc_filename"] == "media_and_data_utilities.md"


class TestDedupDocsDirectory:
    def test_no_duplicates_no_changes(self, tmp_path):
        (tmp_path / "auth.md").write_text("# Auth\n" * 10)
        (tmp_path / "cli.md").write_text("# CLI\n" * 10)
        result = dedup_docs_directory(str(tmp_path))
        assert result["removed"] == []
        assert result["skipped_conflicts"] == []

    def test_similar_content_keeps_largest(self, tmp_path):
        """When duplicate files have similar content (>80% overlap), keep largest."""
        shared = "# Media Utils\n\nThis module handles image compression.\n" * 20
        (tmp_path / "Media-&-Data-Utilities.md").write_text(shared[:50])
        (tmp_path / "media-and-data-utilities.md").write_text(shared)
        result = dedup_docs_directory(str(tmp_path))
        assert len(result["removed"]) == 1
        survivors = [f for f in os.listdir(tmp_path) if f.endswith(".md")]
        assert len(survivors) == 1

    def test_different_content_skips_not_deletes(self, tmp_path):
        """When content differs significantly, do NOT delete — flag for review."""
        (tmp_path / "A-B.md").write_text("# English Version\n\nCompletely different content about auth.")
        (tmp_path / "a_b.md").write_text("# 中文版本\n\n完全不同的关于认证的内容。")
        result = dedup_docs_directory(str(tmp_path))
        assert result["removed"] == []
        assert len(result["skipped_conflicts"]) == 1
        # Both files still exist
        survivors = [f for f in os.listdir(tmp_path) if f.endswith(".md")]
        assert len(survivors) == 2
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `pytest tests/test_doc_filename_freeze.py -v`

- [ ] **Step 3: Implement `freeze_doc_filenames()` and `dedup_docs_directory()`**

Add to `codewiki/src/be/documentation_generator.py`:

```python
from collections import Counter
from codewiki.src.utils import module_doc_filename, _normalize_for_match


def _collect_all_paths(tree: dict) -> list[str]:
    """Recursively collect all path values from every node in the tree."""
    paths = []
    for info in tree.values():
        if not isinstance(info, dict):
            continue
        paths.append(info.get("path", ""))
        children = info.get("children", {})
        if children and isinstance(children, dict):
            paths.extend(_collect_all_paths(children))
    return paths


def freeze_doc_filenames(tree: dict, _collision_set: set[str] | None = None,
                         _parent_frozen_stem: str = "") -> None:
    """Walk tree top-down and set ``_doc_filename`` on every node that lacks one.

    Two-pass algorithm:
    - Pass 1 (on first call): collect all path values, build collision set.
    - Pass 2 (recursive): for each node:
      - Already has ``_doc_filename`` → skip (frozen from previous run).
      - Path is non-empty AND unique (not in collision set) → ``module_doc_filename([path])``.
      - Path collides or is empty → ``module_doc_filename([parent_frozen_stem, key])``.
        Tree key is a one-time disambiguator; after freeze it's never recomputed.

    Top-down traversal guarantees parent's ``_doc_filename`` is set before children.
    """
    # Pass 1: build collision set on first (root) call
    if _collision_set is None:
        all_paths = _collect_all_paths(tree)
        path_counts = Counter(all_paths)
        # A path is "colliding" if it appears more than once OR is empty
        _collision_set = {p for p, count in path_counts.items() if count > 1 or not p}

    for key, info in tree.items():
        if not isinstance(info, dict):
            continue

        if "_doc_filename" not in info:
            path = info.get("path", "")
            if path and path not in _collision_set:
                # Unique path → use directly (most stable)
                info["_doc_filename"] = module_doc_filename([path])
            elif _parent_frozen_stem:
                # Colliding/empty path → parent stem + tree key (one-time disambiguator)
                info["_doc_filename"] = module_doc_filename([_parent_frozen_stem, key])
            elif path:
                # Top-level with colliding path → path + tree key
                info["_doc_filename"] = module_doc_filename([path, key])
            else:
                # Top-level without path → tree key only
                info["_doc_filename"] = module_doc_filename([key])

        # Recurse into children with this node's frozen stem as parent context
        children = info.get("children", {})
        if children and isinstance(children, dict):
            stem = info["_doc_filename"].removesuffix(".md")
            freeze_doc_filenames(children, _collision_set, stem)


def _content_similarity(text_a: str, text_b: str) -> float:
    """Compute normalized line-level overlap between two texts (0.0 to 1.0)."""
    lines_a = set(text_a.strip().splitlines())
    lines_b = set(text_b.strip().splitlines())
    if not lines_a and not lines_b:
        return 1.0
    union = lines_a | lines_b
    if not union:
        return 1.0
    return len(lines_a & lines_b) / len(union)


def dedup_docs_directory(working_dir: str) -> dict[str, list[str]]:
    """Resolve duplicate .md files that normalize to the same canonical name.

    Groups files by ``_normalize_for_match()`` result. For each group:
    - Similar content (>80% line overlap) → keep largest, delete rest.
    - Different content (<80% overlap) → log warning, skip. No deletion.

    Returns: {"removed": [...], "skipped_conflicts": [...]}
    """
    groups: dict[str, list[str]] = {}
    for fname in os.listdir(working_dir):
        if not fname.endswith(".md") or fname.startswith("_"):
            continue
        normed = _normalize_for_match(fname)
        groups.setdefault(normed, []).append(fname)

    removed = []
    skipped_conflicts = []

    for normed, files in groups.items():
        if len(files) <= 1:
            continue

        # Read content for similarity check
        contents = {}
        for f in files:
            try:
                contents[f] = open(os.path.join(working_dir, f), "r", encoding="utf-8").read()
            except Exception:
                contents[f] = ""

        # Check pairwise similarity against the largest file
        files.sort(key=lambda f: len(contents.get(f, "")), reverse=True)
        winner = files[0]
        all_similar = all(
            _content_similarity(contents[winner], contents[f]) > 0.8
            for f in files[1:]
        )

        if not all_similar:
            logger.warning(
                f"Dedup conflict: {files} normalize to the same name but have "
                f"different content — skipping (manual review required)"
            )
            skipped_conflicts.append(files)
            continue

        for loser in files[1:]:
            loser_path = os.path.join(working_dir, loser)
            logger.info(f"Dedup: removing {loser!r} (similar to {winner!r})")
            os.remove(loser_path)
            removed.append(loser)

    return {"removed": removed, "skipped_conflicts": skipped_conflicts}
```

- [ ] **Step 4: Integrate into `generate_module_documentation()`**

In `documentation_generator.py`, after loading the module tree (around line 283), add:

```python
        from codewiki.src.be.generation_state import (
            GenerationState, GenerationStateManager, DocTask, GENERATION_STATE_FILENAME,
        )
        from codewiki.src.config import internal_file_path

        # 1. Freeze canonical filenames on tree nodes
        freeze_doc_filenames(module_tree)
        file_manager.save_json(module_tree, module_tree_path)

        # 2. Load existing ledger (or create empty), wrap in manager
        state_path = internal_file_path(working_dir, GENERATION_STATE_FILENAME)
        gen_state = GenerationState.load(state_path)
        gen_state.repo_commit = self._get_repo_commit()
        self._state_mgr = GenerationStateManager(gen_state, state_path)

        # 3. Build/update tasks from frozen tree (all mutations go through manager)
        new_tasks = collect_tasks_from_tree(module_tree, gen_state, self.config)
        await self._state_mgr.bulk_add_tasks(new_tasks)
        await self._state_mgr.promote_ready()

        # 4. Dedup existing docs
        dedup_docs_directory(working_dir)
```

Workers use `self._state_mgr` for all state mutations:
```python
# In worker coroutine:
await self._state_mgr.mark_running(doc_id)
try:
    # ... generate doc ...
    await self._state_mgr.mark_completed(doc_id, content_hash=hash, model=model)
except Exception as e:
    await self._state_mgr.mark_failed(doc_id, str(e))

# After each batch, promote newly-ready tasks:
await self._state_mgr.promote_ready()
```

Add `collect_tasks_from_tree()` (pure function — returns tasks, doesn't mutate state):

```python
def collect_tasks_from_tree(tree: dict, state: GenerationState, config,
                             parent_path: list[str] | None = None) -> list[DocTask]:
    """Walk frozen tree and return new DocTask entries not yet in the ledger."""
    parent_path = parent_path or []
    new_tasks = []
    for key, info in tree.items():
        if not isinstance(info, dict):
            continue
        output_file = info.get("_doc_filename", module_doc_filename([key]))
        module_path = parent_path + [key]

        mid = info.get("module_id", "")
        doc_id = f"module:{mid}" if mid else f"module:{output_file.removesuffix('.md')}"

        if not state.get_task(doc_id):
            new_tasks.append(DocTask(
                doc_id=doc_id,
                kind="module",
                module_path=module_path,
                output_file=output_file,
                source="manifest",
                language=getattr(config, "output_language", "en"),
            ))

        children = info.get("children", {})
        if children and isinstance(children, dict):
            new_tasks.extend(collect_tasks_from_tree(children, state, config, module_path))
    return new_tasks
```

- [ ] **Step 5: Update file-writing to use `_doc_filename`**

In `generate_parent_module_docs()` (line 644), change:

```python
# Old:
output_path = os.path.join(working_dir, module_doc_filename(module_path))

# New:
# Look up _doc_filename from tree node
doc_filename = self._get_doc_filename(module_tree, module_path)
output_path = os.path.join(working_dir, doc_filename)
```

Add helper:

```python
@staticmethod
def _get_doc_filename(tree: dict, module_path: list[str]) -> str:
    """Look up _doc_filename for a module_path in the tree."""
    node = tree
    for key in module_path:
        if key in node:
            node = node[key]
        else:
            # Navigate into children
            children = node.get("children", {})
            if key in children:
                node = children[key]
            else:
                return module_doc_filename(module_path)
    return node.get("_doc_filename", module_doc_filename(module_path))
```

- [ ] **Step 6: Run tests — expect PASS**
- [ ] **Step 7: Commit**

```bash
git add codewiki/src/be/documentation_generator.py tests/test_doc_filename_freeze.py
git commit -m "feat(naming): add _doc_filename freeze + pre-generation dedup"
```

---

### Task 2.5: Migrate all read/skip paths to use the ledger

**Files:**
- Modify: `codewiki/src/be/documentation_generator.py:224,255,272,621`
- Modify: `codewiki/src/be/agent_orchestrator.py:159-175`
- Modify: `codewiki/src/be/agent_tools/generate_sub_module_documentations.py:148-151,245`
- Modify: `codewiki/src/be/module_tree_manager.py` (remove `mark_completed`)
- Modify: `codewiki/cli/static_generator.py:321`

After Task 2 populates the ledger, all 6 read paths that currently use `find_module_doc(working_dir, tree_key_path)` must be migrated to use ledger lookups. The `_completed` flag on tree nodes is replaced by `status: "completed"` in the ledger. `_parent_doc_hashes.json` is replaced by `content_hash` in ledger tasks.

**Strategy:** Each call site gets the `output_file` from the ledger via `gen_state.get_output_file(doc_id)`, then checks if the file exists. The `doc_id` is derived from the tree node's `_doc_filename` stem (since `_doc_filename` is frozen and unique, it's a valid stable ID).

Helper (add to `documentation_generator.py`):

```python
def _doc_id_for_path(tree: dict, module_path: list[str]) -> str:
    """Derive the doc_id for a module_path by reading the frozen _doc_filename."""
    node = tree
    try:
        for i, key in enumerate(module_path):
            if i < len(module_path) - 1:
                node = node[key]["children"]
            else:
                node = node[key]
    except (KeyError, TypeError):
        return f"module:{module_doc_filename(module_path).removesuffix('.md')}"
    mid = node.get("module_id", "")
    if mid:
        return f"module:{mid}"
    fname = node.get("_doc_filename", module_doc_filename(module_path))
    return f"module:{fname.removesuffix('.md')}"
```

- [ ] **Step 1: Migrate `documentation_generator.py` read paths**

**Line 224** — `build_overview_structure()` top-level child lookup:
```python
# Old:
child_path = find_module_doc(working_dir, [name])
# New:
child_doc_id = _doc_id_for_path(module_tree, [name])
child_file = self._gen_state.get_output_file(child_doc_id)
child_path = os.path.join(working_dir, child_file) if child_file and os.path.exists(os.path.join(working_dir, child_file)) else None
```

**Line 255** — sub-module child lookup: same pattern with `module_path + [child_name]`.

**Line 272** — `_module_doc_exists()`: replace with ledger check:
```python
def _module_doc_exists(self, working_dir: str, module_path: list[str]) -> bool:
    doc_id = _doc_id_for_path(self._module_tree_snapshot, module_path)
    task = self._gen_state.get_task(doc_id)
    if task and task.status == "completed":
        fpath = os.path.join(working_dir, task.output_file)
        return os.path.exists(fpath) and os.path.getsize(fpath) > 100
    return False
```

**Line 621** — `_collect_child_doc_hashes()`: use ledger `content_hash` instead of reading files:
```python
for child_name in children_dict:
    child_doc_id = _doc_id_for_path(module_tree, module_path + [child_name])
    task = self._gen_state.get_task(child_doc_id)
    hashes[child_name] = task.content_hash if task else ""
```

- [ ] **Step 2: Migrate `agent_orchestrator.py:159-175` cache/skip check**

Replace `find_module_doc` + `_completed` flag with ledger status:
```python
# Old:
doc_path_parts = module_path if module_path else [module_name]
docs_path = find_module_doc(working_dir, doc_path_parts)
if docs_path and os.path.getsize(docs_path) > 100:
    ...
    completed = node.get(module_path[-1], {}).get("_completed", False)

# New:
doc_id = _doc_id_for_path(module_tree, module_path if module_path else [module_name])
task = gen_state.get_task(doc_id)
if task and task.status == "completed":
    fpath = os.path.join(working_dir, task.output_file)
    if os.path.exists(fpath) and os.path.getsize(fpath) > 100:
        return {}, "cached"
```

Pass `gen_state` to `process_module()` via parameter or deps.

- [ ] **Step 3: Migrate `generate_sub_module_documentations.py:148-151` skip check**

```python
# Old:
docs_path = find_module_doc(deps.absolute_docs_path, deps.path_to_current_module + [sub_module_name])
if docs_path and os.path.getsize(docs_path) > 100: continue

# New:
_sub_doc_id = _doc_id_for_path(deps.module_tree, deps.path_to_current_module + [sub_module_name])
_sub_task = deps.gen_state.get_task(_sub_doc_id) if deps.gen_state else None
if _sub_task and _sub_task.status == "completed":
    _sub_fpath = os.path.join(deps.absolute_docs_path, _sub_task.output_file)
    if os.path.exists(_sub_fpath) and os.path.getsize(_sub_fpath) > 100:
        continue
```

**Line 245** — Replace `mark_completed()` with ledger update:
```python
# Old:
await deps.module_tree_manager.mark_completed(list(deps.path_to_current_module))

# New — use async-safe manager:
if deps.state_mgr:
    await deps.state_mgr.mark_completed(
        _sub_doc_id,
        content_hash=_file_hash(os.path.join(deps.absolute_docs_path, _assigned)),
        model=_sub_models_str,
    )
```

- [ ] **Step 4: Migrate `static_generator.py:321` sidebar resolution**

```python
# Old:
found = find_module_doc(docs_dir, module_path)

# New: read _doc_filename from tree node (still available on frozen tree)
node = data  # already iterating over tree items
doc_filename = node.get("_doc_filename")
if doc_filename:
    found_path = os.path.join(docs_dir, doc_filename)
    result[map_key] = doc_filename.replace(".md", ".html") if os.path.exists(found_path) else None
else:
    found = find_module_doc(docs_dir, module_path)
    result[map_key] = os.path.basename(found).replace(".md", ".html") if found else None
```

- [ ] **Step 5: Remove `_completed` flag and `mark_completed()` from `module_tree_manager.py`**

Delete `mark_completed()` method (lines 54-62). Remove references to `_completed` in `update_children()`. These are now handled by the ledger.

- [ ] **Step 6: Add `gen_state` to `CodeWikiDeps`**

In `deps.py`:
```python
    state_mgr: Optional[Any] = None  # GenerationStateManager instance
```

- [ ] **Step 7: Commit**

```bash
git add codewiki/src/be/documentation_generator.py codewiki/src/be/agent_orchestrator.py codewiki/src/be/agent_tools/generate_sub_module_documentations.py codewiki/src/be/agent_tools/deps.py codewiki/src/be/module_tree_manager.py codewiki/cli/static_generator.py
git commit -m "fix(naming): migrate all read/skip/complete paths to generation_state ledger"
```

---

### Task 3: Unify `build_link_map()` to read `_doc_filename`

**Files:**
- Modify: `codewiki/src/be/generation/glossary.py:69-98`
- Create: `tests/test_link_map_unification.py`
- Modify: `tests/test_generation_glossary.py`

After Task 2 freezes `_doc_filename` on every node, `build_link_map()` simply reads it. No more independent filename computation. Keys are slash-joined tree key paths (for prompt reference), values are the frozen `_doc_filename`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_link_map_unification.py
import pytest
from codewiki.src.be.generation.glossary import build_link_map


SAMPLE_TREE = {
    "CLI Transport": {
        "path": "cli",
        "_doc_filename": "cli.md",  # unique path → used directly
        "components": ["a"],
        "children": {
            "io_abstractions": {
                "path": "",
                "_doc_filename": "cli-io_abstractions.md",  # empty path → parent_stem + key
                "components": ["b"],
                "children": {},
            },
        },
    },
    "Media Utils": {
        "path": "utils/media",
        "_doc_filename": "utils_media.md",  # unique path → used directly
        "components": ["c"],
        "children": {},
    },
}


class TestLinkMapReadsDocFilename:
    def test_uses_frozen_doc_filename(self):
        lm = build_link_map(SAMPLE_TREE)
        assert lm["CLI Transport"] == "cli.md"
        assert lm["CLI Transport/io_abstractions"] == "cli-io_abstractions.md"
        assert lm["Media Utils"] == "utils_media.md"

    def test_all_nodes_present(self):
        lm = build_link_map(SAMPLE_TREE)
        assert len(lm) == 3

    def test_no_duplicate_filenames(self):
        lm = build_link_map(SAMPLE_TREE)
        vals = list(lm.values())
        assert len(vals) == len(set(vals))

    def test_fallback_without_doc_filename(self):
        """Nodes without _doc_filename fall back to module_doc_filename()."""
        tree = {"Mod": {"path": "src", "components": ["x"], "children": {}}}
        lm = build_link_map(tree)
        assert "Mod" in lm
        assert lm["Mod"].endswith(".md")
```

- [ ] **Step 2: Run tests — expect FAIL**
- [ ] **Step 3: Rewrite `_walk_tree()` to read `_doc_filename`**

```python
# codewiki/src/be/generation/glossary.py — replace _walk_tree

def _walk_tree(tree: dict, parent_path: list[str], link_map: dict[str, str]) -> None:
    """Walk module tree, reading ``_doc_filename`` from each node.

    Falls back to ``module_doc_filename()`` if ``_doc_filename`` is not set.
    Keys are slash-joined tree key paths (title-based, for prompt reference).
    """
    from codewiki.src.utils import module_doc_filename

    for title, info in tree.items():
        if not isinstance(info, dict):
            continue

        key_path = parent_path + [title]
        key_str = "/".join(key_path) if len(key_path) > 1 else title

        doc_filename = info.get("_doc_filename")
        if not doc_filename:
            path = info.get("path", "")
            doc_filename = module_doc_filename([path] if path else key_path)

        link_map[key_str] = doc_filename

        children = info.get("children", {})
        if children and isinstance(children, dict):
            _walk_tree(children, key_path, link_map)
```

- [ ] **Step 4: Run tests — expect PASS**
- [ ] **Step 5: Update existing tests in `test_generation_glossary.py`**
- [ ] **Step 6: Commit**

```bash
git add codewiki/src/be/generation/glossary.py tests/test_link_map_unification.py tests/test_generation_glossary.py
git commit -m "fix(linking): link_map reads _doc_filename instead of computing independently"
```

---

### Task 4: Pass canonical filename to ALL agents (top-level + sub-agents)

**Files:**
- Modify: `codewiki/src/be/prompt_template.py:71-79,209-212`
- Modify: `codewiki/src/be/agent_orchestrator.py:228-272` (top-level agent path)
- Modify: `codewiki/src/be/agent_tools/generate_sub_module_documentations.py` (sub-agent path)
- Modify: `codewiki/src/be/agent_tools/str_replace_editor.py`
- Modify: `codewiki/src/be/agent_tools/deps.py` (add `assigned_doc_filename` field)

Four changes:
1. **Prompt instructions:** Replace wrong filename rules with "write to the assigned filename."
2. **Top-level agent (`process_module()`):** Look up `_doc_filename` from tree, set on deps, append to user prompt.
3. **Sub-agent (`generate_sub_module_documentations`):** Same — look up `_doc_filename`, set on deps, append to user prompt.
4. **str_replace_editor:** Auto-correct writes to match assigned filename.

- [ ] **Step 1: Add `assigned_doc_filename` to CodeWikiDeps**

In `codewiki/src/be/agent_tools/deps.py`, add field:

```python
    assigned_doc_filename: str = ""  # Canonical filename for this agent's output
```

- [ ] **Step 2: Update prompt template (lines 71, 79, 212)**

Line 71: `"1. **Main Documentation File** (write to the filename specified below):"`
Line 79: `"   - Each sub-module gets its own doc file (filename assigned by the system)"`
Line 212: `"* NOTE: Cross-reference other modules using ONLY the filenames from the <LINK_MAP>. All docs are flat in the same folder. Never use ../. If a module is not in the link map, use plain text without a link."`

- [ ] **Step 3: Pass `_doc_filename` in top-level `process_module()` (CRITICAL — this was missing in v3)**

In `codewiki/src/be/agent_orchestrator.py`, in `process_module()` around line 255 (deps creation), look up the frozen filename:

```python
        # Look up frozen filename from tree
        try:
            _node = module_tree
            for _k in module_path:
                _node = _node[_k] if _k in _node else _node.get("children", {})[_k]
            assigned_filename = _node.get("_doc_filename", module_doc_filename(module_path))
        except (KeyError, TypeError):
            assigned_filename = module_doc_filename(module_path)
```

Set it on deps (line ~260):
```python
        deps = CodeWikiDeps(
            ...
            assigned_doc_filename=assigned_filename,
        )
```

Append to user_prompt (after line 247):
```python
        user_prompt += f"\n\nWrite your documentation to the file: {assigned_filename}"
```

- [ ] **Step 4: Pass `_doc_filename` in sub-agent path (TIMING IS CRITICAL)**

In `generate_sub_module_documentations.py`, the current code at lines 184-186 is:
```python
        deps.current_module_name = sub_module_name      # line 184
        deps.path_to_current_module.append(sub_module_name)  # line 185
        deps.current_depth += 1                          # line 186
```

The filename lookup must happen **BEFORE line 184** (before the append changes the path).
At this point, `deps.path_to_current_module` still points to the PARENT module.

Insert **before line 184** (after the skip checks around line 154, before the agent creation):

```python
        # ── Look up frozen filename for this sub-module ───────────────
        # MUST happen BEFORE deps.path_to_current_module.append() below.
        # At this point path_to_current_module = parent path.
        # Navigate: tree → parent → children → sub_module_name
        try:
            _nav = deps.module_tree
            for _k in deps.path_to_current_module:
                _nav = _nav[_k]["children"]
            # _nav is now the children dict of the parent module
            _sub_node = _nav.get(sub_module_name, {})
            _assigned = _sub_node.get(
                "_doc_filename",
                module_doc_filename(deps.path_to_current_module + [sub_module_name])
            )
        except (KeyError, TypeError):
            _assigned = module_doc_filename(deps.path_to_current_module + [sub_module_name])
```

Then **after line 186** (after the append and depth increment):

```python
        deps.assigned_doc_filename = _assigned
```

And in the `format_user_prompt()` call (lines 202-207), append to the prompt:
```python
        user_prompt = format_user_prompt(...) + f"\n\nWrite your documentation to the file: {_assigned}"
```

This ensures:
- Lookup uses `path_to_current_module` BEFORE append → navigates to parent → finds child node
- `assigned_doc_filename` is set AFTER append → available during agent execution
- No ambiguity about which node we're reading from

- [ ] **Step 5: Validate agent writes in str_replace_editor**

In `str_replace_editor.py`, after line 791 (boundary check), when `working_dir == "docs"` and `command == "create"`:

```python
    if working_dir == "docs" and command == "create":
        assigned = getattr(ctx.deps, "assigned_doc_filename", None)
        if assigned and resolved.name != assigned:
            logger.info(f"Agent wrote {resolved.name!r}, correcting to assigned {assigned!r}")
            absolute_path = str(resolved.parent / assigned)
            resolved = Path(absolute_path).resolve()
            if not resolved.is_relative_to(base_path):
                raise ValueError(f"Corrected path {absolute_path} is outside allowed directory")
```

- [ ] **Step 4: Commit**

```bash
git add codewiki/src/be/prompt_template.py codewiki/src/be/agent_tools/generate_sub_module_documentations.py codewiki/src/be/agent_tools/str_replace_editor.py
git commit -m "fix(agent): pass canonical _doc_filename to agents, validate on write"
```

---

### Task 5: Strengthen overview/parent language injection

**Files:**
- Modify: `codewiki/src/be/prompt_template.py:1306-1332`
- Create: `tests/test_overview_language.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_overview_language.py
import pytest
from codewiki.src.be.prompt_template import format_overview_prompt

class TestOverviewLanguageInjection:
    def test_chinese_uses_xml_tags(self):
        prompt = format_overview_prompt("test", "{}", is_repo=True, output_language="zh")
        assert "<OUTPUT_LANGUAGE>" in prompt
        assert "Chinese (Simplified)" in prompt

    def test_english_no_language_block(self):
        prompt = format_overview_prompt("test", "{}", is_repo=True, output_language="en")
        assert "<OUTPUT_LANGUAGE>" not in prompt

    def test_language_before_template_body(self):
        prompt = format_overview_prompt("test", '{"k":"v"}', is_repo=True, output_language="zh")
        lang_pos = prompt.index("<OUTPUT_LANGUAGE>")
        body_pos = prompt.index("senior architect")
        assert lang_pos < body_pos

    def test_module_overview_also_gets_language(self):
        prompt = format_overview_prompt("test", "{}", is_repo=False, output_language="ja")
        assert "<OUTPUT_LANGUAGE>" in prompt
```

- [ ] **Step 2: Run — expect FAIL**
- [ ] **Step 3: Rewrite `format_overview_prompt()`**

```python
def format_overview_prompt(name: str, repo_structure: str, is_repo: bool = True, output_language: str = "en") -> str:
    lang_block = ""
    if output_language and output_language.lower() != "en":
        lang_name = LANGUAGE_NAMES.get(output_language.lower(), output_language)
        lang_block = (
            f"<OUTPUT_LANGUAGE>\n"
            f"Write ALL documentation content in {lang_name}. "
            f"Keep code snippets, file names, identifiers, and technical keywords "
            f"in their original language.\n"
            f"</OUTPUT_LANGUAGE>\n\n"
        )
    if is_repo:
        prompt = REPO_OVERVIEW_PROMPT.format(repo_name=name, repo_structure=repo_structure)
    else:
        prompt = MODULE_OVERVIEW_PROMPT.format(module_name=name, repo_structure=repo_structure)
    return lang_block + prompt + EVIDENCE_RULES_BLOCK
```

- [ ] **Step 4: Run — expect PASS**
- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/prompt_template.py tests/test_overview_language.py
git commit -m "fix(language): prepend structured XML language tags in overview prompts"
```

---

### Task 6: Add link rewriter *before* validation

**Files:**
- Create: `codewiki/src/be/postprocess/link_rewriter.py`
- Modify: `codewiki/src/be/docs_fixer.py:636-664`
- Modify: `codewiki/src/config.py`
- Create: `tests/test_link_rewriter.py`

Runs as **Phase 4a** before validation **Phase 4b**, so lint report reflects post-fix state.

The `fuzzy_index` must handle the case where multiple files normalize to the same name (pre-dedup artifacts). When building the index, if a collision is detected, skip the entry (dedup should have already run; if it didn't, don't make it worse by arbitrarily picking one).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_link_rewriter.py
import pytest
from codewiki.src.be.postprocess.link_rewriter import rewrite_broken_links

@pytest.fixture
def docs_dir(tmp_path):
    (tmp_path / "auth_manager.md").write_text("# Auth Manager\nContent.")
    (tmp_path / "cli.md").write_text("# CLI\nContent.")
    (tmp_path / "cli-io_abstractions.md").write_text("# IO\nContent.")
    return tmp_path

class TestRewriteBrokenLinks:
    def test_correct_link_unchanged(self, docs_dir):
        md = docs_dir / "test.md"
        md.write_text("See [Auth](auth_manager.md).")
        rewrite_broken_links(str(docs_dir))
        assert md.read_text() == "See [Auth](auth_manager.md)."

    def test_fuzzy_match_rewrites(self, docs_dir):
        md = docs_dir / "test.md"
        md.write_text("See [Auth](Auth-Manager.md).")
        stats = rewrite_broken_links(str(docs_dir))
        assert "auth_manager.md" in md.read_text()
        assert stats["rewritten"] == 1

    def test_relative_path_stripped(self, docs_dir):
        md = docs_dir / "test.md"
        md.write_text("See [IO](../cli/io_abstractions.md).")
        rewrite_broken_links(str(docs_dir))
        assert "../" not in md.read_text()

    def test_nonexistent_becomes_plain_text(self, docs_dir):
        md = docs_dir / "test.md"
        md.write_text("See [Ghost](nonexistent.md).")
        stats = rewrite_broken_links(str(docs_dir))
        assert "nonexistent.md" not in md.read_text()
        assert "Ghost" in md.read_text()
        assert stats["removed"] == 1

    def test_external_untouched(self, docs_dir):
        md = docs_dir / "test.md"
        orig = "See [X](https://example.com)."
        md.write_text(orig)
        rewrite_broken_links(str(docs_dir))
        assert md.read_text() == orig

    def test_code_block_untouched(self, docs_dir):
        md = docs_dir / "test.md"
        orig = "```\n[link](broken.md)\n```"
        md.write_text(orig)
        rewrite_broken_links(str(docs_dir))
        assert md.read_text() == orig

    def test_collision_in_index_skips(self, docs_dir):
        """When two files normalize to same name, don't rewrite to either."""
        (docs_dir / "A-B.md").write_text("content1")
        (docs_dir / "a_b.md").write_text("content2")
        md = docs_dir / "test.md"
        md.write_text("See [X](A--B.md).")  # would normalize to a_b
        stats = rewrite_broken_links(str(docs_dir))
        # Should be removed (ambiguous), not rewritten
        assert stats["removed"] == 1
```

- [ ] **Step 2: Run — expect FAIL**
- [ ] **Step 3: Implement link rewriter**

```python
# codewiki/src/be/postprocess/link_rewriter.py
"""Post-generation link rewriter."""
import os, re, logging
from codewiki.src.utils import _normalize_for_match

logger = logging.getLogger(__name__)
_LINK_RE = re.compile(r'\[([^\]]*)\]\(([^)]+)\)')
_AMBIGUOUS = object()  # sentinel

def rewrite_broken_links(docs_dir: str) -> dict[str, int]:
    stats = {"rewritten": 0, "removed": 0, "total_scanned": 0}

    # Build index with collision detection
    fuzzy_index: dict[str, str | object] = {}
    actual_files: set[str] = set()
    for fname in os.listdir(docs_dir):
        if fname.endswith(".md"):
            actual_files.add(fname)
            normed = _normalize_for_match(fname)
            if normed in fuzzy_index:
                fuzzy_index[normed] = _AMBIGUOUS  # collision — don't rewrite to either
            else:
                fuzzy_index[normed] = fname

    for md_file in sorted(actual_files):
        filepath = os.path.join(docs_dir, md_file)
        try:
            content = open(filepath, "r", encoding="utf-8").read()
        except Exception:
            continue
        stats["total_scanned"] += 1
        in_code = False
        lines = content.split("\n")
        new_lines = []
        changed = False

        for line in lines:
            if line.strip().startswith("```"):
                in_code = not in_code
                new_lines.append(line)
                continue
            if in_code:
                new_lines.append(line)
                continue

            def _repl(m):
                nonlocal changed
                text, target = m.group(1), m.group(2)
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    return m.group(0)
                anchor = ""
                if "#" in target:
                    target_file, anchor = target.rsplit("#", 1)
                    anchor = "#" + anchor
                else:
                    target_file = target
                basename = os.path.basename(target_file)
                if not basename:
                    return m.group(0)
                try:
                    from urllib.parse import unquote
                    basename = unquote(basename)
                except Exception:
                    pass
                if basename in actual_files:
                    if basename != target_file:
                        changed = True
                        stats["rewritten"] += 1
                        return f"[{text}]({basename}{anchor})"
                    return m.group(0)
                normed = _normalize_for_match(basename)
                matched = fuzzy_index.get(normed)
                if matched is not None and matched is not _AMBIGUOUS:
                    changed = True
                    stats["rewritten"] += 1
                    return f"[{text}]({matched}{anchor})"
                # No match or ambiguous — remove link, keep text
                changed = True
                stats["removed"] += 1
                return text

            new_lines.append(_LINK_RE.sub(_repl, line))

        if changed:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("\n".join(new_lines))
    return stats
```

- [ ] **Step 4: Integrate into docs_fixer as Phase 4a before Phase 4b**

In `codewiki/src/be/docs_fixer.py`, replace lines 636-664 with:

```python
    # ── Phase 4a: Link rewriting (auto-fix broken links) ──────────────────
    if getattr(config, "postprocess_fix_links", True):
        try:
            from codewiki.src.be.postprocess.link_rewriter import rewrite_broken_links
            rewrite_stats = rewrite_broken_links(working_dir)
            if rewrite_stats["rewritten"] or rewrite_stats["removed"]:
                logger.info(
                    f"  \U0001f517 Links: rewrote {rewrite_stats['rewritten']}, "
                    f"removed {rewrite_stats['removed']} broken link(s)"
                )
        except Exception as exc:
            logger.warning(f"Link rewriting failed: {exc}")

    # ── Phase 4b: Link validation (report on remaining issues) ────────────
    try:
        from codewiki.src.be.postprocess.link_validator import validate_links
        link_issues = validate_links(working_dir)
        for issue in link_issues:
            report.link_issues.append({
                "file": issue.source_file,
                "line": issue.line_number,
                "target": issue.target,
                "issue_type": issue.issue_type,
            })
    except Exception as exc:
        logger.warning(f"Link validation failed: {exc}")
```

- [ ] **Step 5: Add config default**

```python
# codewiki/src/config.py
    postprocess_fix_links: bool = True
```

- [ ] **Step 6: Run tests — expect PASS**
- [ ] **Step 7: Commit**

```bash
git add codewiki/src/be/postprocess/link_rewriter.py codewiki/src/be/docs_fixer.py codewiki/src/config.py tests/test_link_rewriter.py
git commit -m "feat(postprocess): add link rewriter as Phase 4a before validation"
```

---

### Task 7: Move cache files to `.codewiki/` + exclude from static gen

**Files:**
- Modify: `codewiki/src/config.py`
- Modify: `codewiki/src/be/documentation_generator.py`
- Modify: `codewiki/src/be/guide_generator.py`
- Modify: `codewiki/cli/static_generator.py`

- [ ] **Step 1: Add `internal_file_path()` helper to config.py**

```python
INTERNAL_SUBDIR = ".codewiki"

def internal_file_path(working_dir: str, filename: str) -> str:
    """Path for internal/cache files in the .codewiki subdir."""
    subdir = os.path.join(working_dir, INTERNAL_SUBDIR)
    os.makedirs(subdir, exist_ok=True)
    return os.path.join(subdir, filename)
```

- [ ] **Step 2: Update documentation_generator.py hash file paths**

Replace `os.path.join(working_dir, PARENT_DOC_HASHES_FILENAME)` with `internal_file_path(working_dir, PARENT_DOC_HASHES_FILENAME)`. Add migration: if old path exists and new path doesn't, move it.

- [ ] **Step 3: Update guide_generator.py cache path**

Replace `_cache_path()` to use `internal_file_path()`.

- [ ] **Step 4: Filter static generation**

```python
# codewiki/cli/static_generator.py line 600
md_files = sorted(f for f in docs_dir.glob("*.md") if not f.name.startswith("_"))
```

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/config.py codewiki/src/be/documentation_generator.py codewiki/src/be/guide_generator.py codewiki/cli/static_generator.py
git commit -m "fix(output): move cache files to .codewiki/ subdir, exclude from static gen"
```

---

### Task 8: Update existing tests

**Files:**
- Modify: `tests/test_generation_glossary.py`

- [ ] **Step 1: Update `TestBuildLinkMap` to expect `_doc_filename`-based values**
- [ ] **Step 2: Run full suite**

Run: `pytest tests/ -v --timeout=60 -x`

- [ ] **Step 3: Commit**

```bash
git add tests/test_generation_glossary.py
git commit -m "test: update glossary tests for _doc_filename-based link_map"
```

---

## Dependency Order

```
Task 0 (generation_state.json model) ← foundation
  ↓
Task 1 (normalize module_doc_filename) ← independent of Task 0 but needed by Task 2
  ↓
Task 2 (build ledger from tree + dedup) ← depends on Tasks 0 + 1
  ↓
Task 2.5 (migrate all read/skip/complete paths to ledger) ← depends on Task 2
  ↓
Task 3 (link_map reads output_file from ledger) ← depends on Task 2
Task 4 (agent gets filename from ledger) ← depends on Task 2.5
  ↓
Task 5 (language injection) ← independent
Task 6 (link rewriter before validation) ← depends on Task 1
Task 7 (cache file relocation → .codewiki/) ← independent
  ↓
Task 8 (test updates + remove old cache files) ← depends on Tasks 0-3
```

Tasks 0 and 1 can run in parallel. After Task 2.5: {3, 4, 5, 6, 7} are parallelizable.

---

## Verification Checklist

- [ ] **V1:** Generate docs with `output_language=zh` — all filenames lowercase, no `&`/`,`/spaces
- [ ] **V2:** No NEW duplicate files created by this run. Existing content-divergent duplicates flagged with warnings (not silently deleted). **Scope note:** v6 guarantees "no new drift + no new broken links" but does NOT auto-resolve historical content-fork duplicates — those require manual review or a clean regeneration.
- [ ] **V3:** Internal links resolve (grep for `[.*](.*\.md)` and check targets exist)
- [ ] **V4:** Parent/overview docs in Chinese when `output_language=zh`
- [ ] **V5:** No `_guide_cache.json` or `_parent_doc_hashes.json` in docs root
- [ ] **V6:** `_lint_report.json` reflects post-rewrite state
- [ ] **V7:** `generation_state.json` exists in `.codewiki/`, has one task per tree node, all `status: "completed"` after successful run
- [ ] **V8:** Changing a module title in re-clustering does NOT change its `output_file` (ledger freeze works)
- [ ] **V9:** No `_parent_doc_hashes.json`, `_tree_cache_meta.json`, or `_completed` flags used — replaced by ledger. `_guide_cache.json` relocated to `.codewiki/` (not migrated to ledger in v7).

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| `path` field empty in 40% of v1 nodes | Two-pass freeze handles this: empty/colliding paths use `parent_stem + key` as disambiguator |
| `path` not unique in v1 trees (e.g. `utils` x14) | Two-pass collision detection: colliding paths get `parent_stem + key` (or `path + key` for top-level) |
| Tree key used as disambiguator drifts in future runs | Freeze is one-time: `_doc_filename` computed once, persisted, never recomputed. Title changes after freeze have zero effect. |
| Dedup deletes a "better" file | Content-similarity check: only auto-deletes when >80% overlap. Different content → warning + skip, no deletion. |
| Agents ignore assigned filename | `str_replace_editor` auto-corrects to `assigned_doc_filename` from deps |
| `_doc_filename` freeze prevents intentional renames | Freeze only prevents AUTOMATIC recomputation. Manual tree edits can change `_doc_filename`. |
| Link rewriter encounters ambiguous normalized names | Collision detection: when two files normalize the same, rewriter removes the link rather than guessing |
| Sub-agent gets wrong filename due to timing | Lookup happens BEFORE `path_to_current_module.append()`, using parent path to navigate to child node. Assignment happens AFTER append. Timing is explicit. |
| Read paths miss files after write-path migration | Ledger `output_file` is the single source of truth. `find_module_doc()` only used as fallback for pre-ledger trees. |
| Concurrent agents corrupt `generation_state.json` | `GenerationStateManager` wraps all mutations in `asyncio.Lock` (same pattern as `ModuleTreeManager`). Workers call `await mgr.mark_completed(...)` — lock held for the full read-modify-write + atomic save cycle. Single event loop guarantees no interleaving. |
| Migration from old cache files (`_completed`, `_parent_doc_hashes.json`) | Task 2 builds ledger from tree. First run with ledger: all tasks start as `pending`. Old cache files ignored (can be deleted in Task 8). No backward compatibility needed — one clean run resets everything. |
| `doc_id` stability when `module_id` is absent (v1 trees) | Falls back to `module:{output_file_stem}` which is frozen. Stable as long as output_file doesn't change (which it won't — it's frozen in the ledger). |
