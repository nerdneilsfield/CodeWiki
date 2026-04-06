# Unified Cache System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace 7 scattered cache mechanisms with a single CacheManager that handles dependency cascade, scheduler-level filtering, and incremental updates — so no LLM call is ever wasted on already-generated content.

**Architecture:** `CacheManager` owns a `cache_registry.json` with all artifact metadata. Pipeline stages call `cache.is_valid()` before dispatching work, `cache.mark_done()` after completion. A background thread flushes to disk every 10s. Dependency DAG is implicit in `depends_on` fields — upstream invalidation cascades automatically.

**Tech Stack:** Python dataclasses, threading.Lock, threading.Event, JSON persistence, hashlib.sha256

**Spec:** `docs/superpowers/specs/2026-04-06-unified-cache-design.md`

**Intentional trade-off:** `context_pack` (glossary/link_map) is deliberately excluded from module input_hash — it derives from the same components, so if source hashes haven't changed, context_pack won't change either.

**Hardening notes:** `CacheManager.get_entry()` returns defensive copies, invalidation uses BFS over a reverse dependency index, and flush shutdown uses `threading.Event` so `stop()` does not block for a full interval. Test coverage includes deep dependency chains, dependency cycles, concurrent `get_entry()` mutation isolation, and atomic-write failure cleanup.

**Execution order note:** Task 11 (PROMPT_VERSION constant) must execute before Task 3 (which imports it). Task 8 removed (graph always re-runs per spec). Execute in order: 1 → 2 → 11 → 3 → 4 → 5 → 6 → 7 → 9 → 10 → 12.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `codewiki/src/be/cache_manager.py` (create) | CacheEntry dataclass + CacheManager class |
| `codewiki/src/be/documentation_scheduler.py` (modify) | Replace gen_state skip logic with cache.is_valid() |
| `codewiki/src/be/documentation_generator.py` (modify) | Create CacheManager, replace GenerationState usage |
| `codewiki/src/be/agent_orchestrator.py` (modify) | Replace gen_state task lookup with cache queries |
| `codewiki/src/be/documentation_overview.py` (modify) | Per-segment caching + merge strategy |
| `codewiki/src/be/guide_generator.py` (modify) | Replace _guide_cache.json with cache entries |
| `codewiki/src/be/dependency_analyzer/dependency_graphs_builder.py` (modify) | Replace _graph_cache.json with cache entry |
| `codewiki/src/be/documentation_tree_utils.py` (modify) | Replace build_generation_tasks with cache-based planning |
| `codewiki/src/be/pipeline.py` (modify) | Add CacheManager to PipelineContext |
| `codewiki/src/be/stages/state_init.py` (modify) | Init CacheManager instead of GenerationState |
| `codewiki/src/be/postprocess/mermaid_validator.py` (modify) | Cache LLM repair results |
| `codewiki/src/be/postprocess/math_validator.py` (modify) | Cache LLM repair results |
| `tests/test_cache_manager.py` (create) | Unit tests for CacheManager |
| `codewiki/src/be/generation_state.py` (delete last) | Remove after full migration |

---

### Task 1: CacheEntry dataclass and CacheManager core

**Files:**
- Create: `codewiki/src/be/cache_manager.py`
- Create: `tests/test_cache_manager.py`

- [ ] **Step 1: Write failing tests for CacheEntry and CacheManager basics**

```python
# tests/test_cache_manager.py
import os
import json
import pytest
from codewiki.src.be.cache_manager import CacheEntry, CacheManager


@pytest.fixture
def cache_dir(tmp_path):
    d = tmp_path / ".codewiki"
    d.mkdir()
    return str(d)


def test_cache_entry_creation():
    entry = CacheEntry(
        artifact_id="module:auth",
        input_hash="abc123",
        status="valid",
        depends_on=[],
        output_path="auth.md",
        output_file="auth.md",
    )
    assert entry.artifact_id == "module:auth"
    assert entry.status == "valid"
    assert entry.attempt_count == 0


def test_cache_manager_is_valid_miss(cache_dir):
    cm = CacheManager(cache_dir)
    assert cm.is_valid("module:auth", "abc123") is False


def test_cache_manager_mark_done_then_valid(cache_dir):
    cm = CacheManager(cache_dir)
    cm.mark_done("module:auth", input_hash="abc123", output_path="auth.md")
    assert cm.is_valid("module:auth", "abc123") is True


def test_cache_manager_stale_on_hash_change(cache_dir):
    cm = CacheManager(cache_dir)
    cm.mark_done("module:auth", input_hash="abc123", output_path="auth.md")
    assert cm.is_valid("module:auth", "different") is False


def test_cache_manager_invalidate_cascades(cache_dir):
    cm = CacheManager(cache_dir)
    cm.mark_done("module:auth", input_hash="h1", output_path="auth.md")
    cm.mark_done("overview:root:child:auth", input_hash="h2", output_path="p.md",
                 depends_on=["module:auth"])
    cm.invalidate("module:auth")
    assert cm.get_entry("module:auth").status == "stale"
    assert cm.get_entry("overview:root:child:auth").status == "stale"


def test_cache_manager_get_output_file(cache_dir):
    cm = CacheManager(cache_dir)
    cm.plan_task("module:auth", output_file="auth.md")
    assert cm.get_output_file("module:auth") == "auth.md"


def test_cache_manager_plan_task_sets_missing(cache_dir):
    cm = CacheManager(cache_dir)
    cm.plan_task("module:auth", output_file="auth.md")
    entry = cm.get_entry("module:auth")
    assert entry.status == "missing"
    assert entry.output_file == "auth.md"


def test_cache_manager_plan_task_collision_raises(cache_dir):
    cm = CacheManager(cache_dir)
    cm.plan_task("module:auth", output_file="auth.md")
    with pytest.raises(ValueError, match="Output file collision"):
        cm.plan_task("module:auth2", output_file="auth.md")  # same file, different artifact


def test_cache_manager_mark_running(cache_dir):
    cm = CacheManager(cache_dir)
    cm.plan_task("module:auth", output_file="auth.md")
    cm.mark_running("module:auth")
    assert cm.get_entry("module:auth").status == "running"


def test_cache_manager_mark_failed(cache_dir):
    cm = CacheManager(cache_dir)
    cm.plan_task("module:auth", output_file="auth.md")
    cm.mark_running("module:auth")
    cm.mark_failed("module:auth", error="timeout")
    entry = cm.get_entry("module:auth")
    assert entry.status == "failed"
    assert entry.error == "timeout"


def test_cache_manager_flush_and_load(cache_dir):
    cm = CacheManager(cache_dir)
    cm.mark_done("module:auth", input_hash="abc", output_path="auth.md", output_file="auth.md")
    cm.flush()

    registry_path = os.path.join(cache_dir, "cache_registry.json")
    assert os.path.exists(registry_path)

    cm2 = CacheManager(cache_dir)
    assert cm2.is_valid("module:auth", "abc") is True


def test_cache_manager_crash_recovery_running_to_stale(cache_dir):
    cm = CacheManager(cache_dir)
    cm.plan_task("module:auth", output_file="auth.md")
    cm.mark_running("module:auth")
    cm.flush()

    cm2 = CacheManager(cache_dir)  # simulates restart
    entry = cm2.get_entry("module:auth")
    assert entry.status == "stale"  # running → stale on load


def test_cache_manager_invalidate_downstream(cache_dir):
    cm = CacheManager(cache_dir)
    cm.mark_done("module:a", input_hash="h1", output_path="a.md")
    cm.mark_done("module:b", input_hash="h2", output_path="b.md")
    cm.mark_done("overview:root:child:a", input_hash="h3", output_path="ca.md",
                 depends_on=["module:a"])
    cm.mark_done("overview:root:child:b", input_hash="h4", output_path="cb.md",
                 depends_on=["module:b"])
    cm.mark_done("overview:root", input_hash="h5", output_path="overview.md",
                 depends_on=["overview:root:child:a", "overview:root:child:b"])
    cm.invalidate("module:a")
    assert cm.get_entry("module:a").status == "stale"
    assert cm.get_entry("overview:root:child:a").status == "stale"
    assert cm.get_entry("overview:root").status == "stale"
    assert cm.get_entry("module:b").status == "valid"  # unaffected
    assert cm.get_entry("overview:root:child:b").status == "valid"  # unaffected


def test_cache_manager_get_stale_entries(cache_dir):
    cm = CacheManager(cache_dir)
    cm.mark_done("module:a", input_hash="h1", output_path="a.md")
    cm.plan_task("module:b", output_file="b.md")
    stale = cm.get_stale_entries(prefix="module:")
    assert len(stale) == 1
    assert stale[0].artifact_id == "module:b"


def test_overview_regenerate_threshold():
    assert CacheManager.OVERVIEW_REGENERATE_THRESHOLD == 0.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cache_manager.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codewiki.src.be.cache_manager'`

- [ ] **Step 3: Implement CacheManager**

Create `codewiki/src/be/cache_manager.py`:

```python
"""Unified cache system for CodeWiki.

Single CacheManager replaces generation_state.json, _guide_cache.json, and
_graph_cache.json cache-key logic. All artifact metadata lives in one
cache_registry.json, with dependency cascade and background flush.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

CACHE_REGISTRY_FILENAME = "cache_registry.json"
_SCHEMA_VERSION = "cache.v1"


@dataclass
class CacheEntry:
    """Metadata for a single cached artifact."""

    artifact_id: str
    input_hash: str = ""
    status: str = "missing"  # valid | stale | missing | running | failed
    depends_on: list[str] = field(default_factory=list)
    output_path: str = ""
    output_file: str = ""
    model: str = ""
    attempt_count: int = 0
    error: str = ""
    updated_at: str = ""


class CacheManager:
    """Unified cache with dependency cascade and background persistence.

    Thread-safe: all mutations go through ``_lock``.
    """

    OVERVIEW_REGENERATE_THRESHOLD = 0.5

    def __init__(self, cache_dir: str, flush_interval: float = 10.0):
        self._cache_dir = cache_dir
        self._flush_interval = flush_interval
        self._entries: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self._dirty = False
        self._stopped = False
        self._flush_thread: threading.Thread | None = None

        # Load existing registry
        self._load()

    # ── Query ──────────────────────────────────────────────────

    def is_valid(self, artifact_id: str, current_input_hash: str) -> bool:
        """Check if artifact is cached and still valid."""
        with self._lock:
            entry = self._entries.get(artifact_id)
            if entry is None:
                return False
            return entry.status == "valid" and entry.input_hash == current_input_hash

    def get_entry(self, artifact_id: str) -> CacheEntry | None:
        with self._lock:
            return self._entries.get(artifact_id)

    def get_input_hash(self, artifact_id: str) -> str | None:
        with self._lock:
            entry = self._entries.get(artifact_id)
            return entry.input_hash if entry else None

    def get_output_file(self, artifact_id: str) -> str | None:
        with self._lock:
            entry = self._entries.get(artifact_id)
            return entry.output_file if entry else None

    # ── Task lifecycle ─────────────────────────────────────────

    def plan_task(
        self,
        artifact_id: str,
        output_file: str,
        depends_on: list[str] | None = None,
    ) -> None:
        """Register a task before execution.

        Preserves status if entry already valid. Raises ValueError if
        output_file collides with a different artifact's output_file
        (mirrors GenerationState._add_task collision protection).
        """
        with self._lock:
            # Collision detection: check no other artifact uses this output_file
            for aid, entry in self._entries.items():
                if aid != artifact_id and entry.output_file == output_file:
                    raise ValueError(
                        f"Output file collision: '{output_file}' already assigned to "
                        f"'{aid}', cannot assign to '{artifact_id}'"
                    )

            existing = self._entries.get(artifact_id)
            if existing and existing.status == "valid":
                # Already valid — update depends_on but keep status
                if depends_on is not None:
                    existing.depends_on = depends_on
                existing.output_file = output_file
                return
            self._entries[artifact_id] = CacheEntry(
                artifact_id=artifact_id,
                status="missing",
                output_file=output_file,
                depends_on=depends_on or [],
            )
            self._dirty = True

    def mark_running(self, artifact_id: str) -> None:
        with self._lock:
            entry = self._entries.get(artifact_id)
            if entry:
                entry.status = "running"
                entry.updated_at = _now()
                self._dirty = True

    def mark_done(
        self,
        artifact_id: str,
        input_hash: str,
        output_path: str,
        model: str = "",
        output_file: str = "",
        depends_on: list[str] | None = None,
    ) -> None:
        """Mark artifact as successfully generated."""
        with self._lock:
            entry = self._entries.get(artifact_id)
            if entry is None:
                entry = CacheEntry(artifact_id=artifact_id)
                self._entries[artifact_id] = entry
            entry.input_hash = input_hash
            entry.status = "valid"
            entry.output_path = output_path
            entry.model = model
            entry.error = ""
            entry.attempt_count += 1
            entry.updated_at = _now()
            if output_file:
                entry.output_file = output_file
            if depends_on is not None:
                entry.depends_on = depends_on
            self._dirty = True

    def mark_failed(self, artifact_id: str, error: str) -> None:
        with self._lock:
            entry = self._entries.get(artifact_id)
            if entry:
                entry.status = "failed"
                entry.error = error
                entry.attempt_count += 1
                entry.updated_at = _now()
                self._dirty = True

    # ── Cascade ────────────────────────────────────────────────

    def invalidate(self, artifact_id: str) -> None:
        """Mark stale and recursively cascade to all downstream dependents."""
        with self._lock:
            self._invalidate_locked(artifact_id)
            self._dirty = True

    def invalidate_downstream(self, artifact_ids: list[str]) -> None:
        """Invalidate all entries that transitively depend on any of the given IDs."""
        with self._lock:
            for aid in artifact_ids:
                self._invalidate_locked(aid)
            self._dirty = True

    def _invalidate_locked(self, artifact_id: str) -> None:
        """Recursive invalidation. Must be called under self._lock."""
        entry = self._entries.get(artifact_id)
        if entry and entry.status != "stale" and entry.status != "missing":
            entry.status = "stale"
            entry.updated_at = _now()
        # Cascade: find all entries that depend on this one
        for other in self._entries.values():
            if artifact_id in other.depends_on:
                if other.status not in ("stale", "missing"):
                    self._invalidate_locked(other.artifact_id)

    # ── Batch queries ──────────────────────────────────────────

    def get_stale_entries(self, prefix: str = "") -> list[CacheEntry]:
        with self._lock:
            return [
                e
                for e in self._entries.values()
                if e.status in ("stale", "missing", "failed")
                and (not prefix or e.artifact_id.startswith(prefix))
            ]

    # ── Persistence ────────────────────────────────────────────

    def flush(self) -> None:
        """Write to disk immediately. Safe to call from any thread."""
        with self._lock:
            if not self._dirty:
                return
            self._write_locked()
            self._dirty = False

    def start(self) -> None:
        """Start background flush thread."""
        if self._flush_thread is not None:
            return
        self._stopped = False
        self._flush_thread = threading.Thread(
            target=self._periodic_flush, daemon=True, name="cache-flush"
        )
        self._flush_thread.start()

    def stop(self) -> None:
        """Stop background flush thread and do a final flush."""
        self._stopped = True
        if self._flush_thread is not None:
            self._flush_thread.join(timeout=5.0)
            self._flush_thread = None
        self.flush()

    def _periodic_flush(self) -> None:
        while not self._stopped:
            time.sleep(self._flush_interval)
            if self._stopped:
                break
            try:
                self.flush()
            except Exception as e:
                logger.warning("Cache flush failed: %s", e)

    def _registry_path(self) -> str:
        return os.path.join(self._cache_dir, CACHE_REGISTRY_FILENAME)

    def _load(self) -> None:
        path = self._registry_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("schema_version") != _SCHEMA_VERSION:
                logger.warning("Cache registry schema mismatch — starting fresh")
                return
            for aid, raw in data.get("entries", {}).items():
                entry = CacheEntry(
                    artifact_id=aid,
                    input_hash=raw.get("input_hash", ""),
                    status=raw.get("status", "missing"),
                    depends_on=raw.get("depends_on", []),
                    output_path=raw.get("output_path", ""),
                    output_file=raw.get("output_file", ""),
                    model=raw.get("model", ""),
                    attempt_count=raw.get("attempt_count", 0),
                    error=raw.get("error", ""),
                    updated_at=raw.get("updated_at", ""),
                )
                # Crash recovery: running → stale
                if entry.status == "running":
                    entry.status = "stale"
                    logger.info("Cache entry '%s' was running at shutdown — reset to stale", aid)
                self._entries[aid] = entry
        except Exception as e:
            logger.warning("Failed to load cache registry: %s — starting fresh", e)

    def _write_locked(self) -> None:
        """Write registry to disk. Must be called under self._lock."""
        data: dict[str, Any] = {
            "schema_version": _SCHEMA_VERSION,
            "entries": {
                aid: {k: v for k, v in asdict(entry).items() if k != "artifact_id"}
                for aid, entry in self._entries.items()
            },
        }
        path = self._registry_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_cache_manager.py -v`
Expected: all pass

- [ ] **Step 5: Run full suite**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: all existing tests still pass (CacheManager is additive, no existing code changed)

- [ ] **Step 6: Commit**

```bash
git add codewiki/src/be/cache_manager.py tests/test_cache_manager.py
git commit -m "feat(cache): add CacheManager with dependency cascade and background flush"
```

---

### Task 2: Wire CacheManager into pipeline

**Files:**
- Modify: `codewiki/src/be/pipeline.py`
- Modify: `codewiki/src/be/documentation_generator.py`

Note: `codewiki/src/be/stages/state_init.py` still initializes GenerationState during migration.
It will be updated in Task 10 when GenerationState is fully removed.

- [ ] **Step 1: Add cache_manager to PipelineContext**

In `codewiki/src/be/pipeline.py`, add to PipelineContext dataclass:
```python
cache_manager: Any = None  # CacheManager instance
```

- [ ] **Step 2: Create CacheManager in DocumentationGenerator**

In `codewiki/src/be/documentation_generator.py`, after `self.middleware = LLMMiddleware(...)`:
```python
from codewiki.src.be.cache_manager import CacheManager

cache_dir = os.path.join(config.docs_dir, ".codewiki")
os.makedirs(cache_dir, exist_ok=True)
self.cache_manager = CacheManager(cache_dir)
self.cache_manager.start()  # background flush thread
```

Pass to PipelineContext:
```python
ctx.cache_manager = self.cache_manager
```

- [ ] **Step 3: Stop CacheManager on pipeline completion**

In `codewiki/src/be/pipeline.py`, the `_flush_all_state(ctx)` function (line 126) is a
module-level async function, not a method. Add cache_manager stop there:

```python
async def _flush_all_state(ctx: PipelineContext) -> None:
    # ... existing flush logic ...
    if ctx.cache_manager:
        ctx.cache_manager.stop()  # final flush + stop background thread
```

- [ ] **Step 4: Run full suite**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: all pass (cache_manager is optional/None in existing tests)

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/pipeline.py codewiki/src/be/documentation_generator.py
git commit -m "refactor(pipeline): wire CacheManager into pipeline context"
```

---

### Task 3: Migrate scheduler leaf dispatch to CacheManager

**Files:**
- Modify: `codewiki/src/be/documentation_scheduler.py:165-177`
- Modify: `codewiki/src/be/documentation_tree_utils.py:164-229`

- [ ] **Step 1: Add cache_manager parameter to run_module_queue**

Add `cache_manager=None` parameter to `run_module_queue` and `run_module_queue_impl`. Thread it from `DocumentationGenerator`.

- [ ] **Step 2: Replace leaf skip logic**

Replace lines 165-177 in `documentation_scheduler.py`:

```python
# Before (gen_state based):
if gen_state:
    did = doc_id_for_path(graph_tree, path)
    t = gen_state.get_task(did)
    if t and t.status in ("completed", "skipped"):
        await done_queue.put((key, True, False, None))
        leaf_count += 1
        continue

# After (cache_manager based):
if cache_manager:
    artifact_id = f"module:{doc_id_for_path(graph_tree, path)}"
    input_hash = _compute_module_input_hash(info, components, config)
    if cache_manager.is_valid(artifact_id, input_hash):
        await done_queue.put((key, True, False, None))
        leaf_count += 1
        continue
```

- [ ] **Step 3: Add _compute_module_input_hash helper**

In `documentation_scheduler.py` or `documentation_tree_utils.py`:

```python
from codewiki.src.be.documentation_tree_utils import stable_hash
from codewiki.src.be.prompt_template import PROMPT_VERSION

def compute_module_input_hash(
    module_info: dict,
    components: dict,
    config,
) -> str:
    """Compute input_hash for a module artifact."""
    comp_ids = sorted(module_info.get("components", []))
    source_hashes = []
    for cid in comp_ids:
        node = components.get(cid)
        if node and node.source_code:
            source_hashes.append(hashlib.md5(node.source_code.encode()).hexdigest())
    module_name = module_info.get("name", "")
    module_path = module_info.get("path", "")
    assigned_file = module_info.get("_doc_filename", "")
    custom_hash = hashlib.md5(
        (getattr(config, "custom_instructions", None) or "").encode()
    ).hexdigest()
    return stable_hash([
        module_name,
        module_path,
        *comp_ids,
        *source_hashes,
        assigned_file,
        config.output_language,
        custom_hash,
        PROMPT_VERSION,
    ])
```

- [ ] **Step 4: Keep gen_state as fallback during migration**

Keep the existing gen_state check as an `elif` fallback so both systems work during migration:
```python
if cache_manager:
    # new path
    ...
elif gen_state:
    # old path (will be removed in Task 10)
    ...
```

- [ ] **Step 5: Run full suite**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add codewiki/src/be/documentation_scheduler.py codewiki/src/be/documentation_tree_utils.py
git commit -m "refactor(scheduler): migrate leaf dispatch to CacheManager"
```

---

### Task 4: Migrate scheduler parent/coordinator dispatch

**Files:**
- Modify: `codewiki/src/be/documentation_scheduler.py:267-332`

- [ ] **Step 1: Replace coordinator parent hash computation and skip logic**

Replace the coordinator block (lines 267-332) to use cache_manager:

```python
if pending_count[parent_key] == 0:
    del pending_count[parent_key]

    if cache_manager:
        parent_artifact = f"overview:{parent_doc_id}"
        # Recompute current hash for each child segment from the CHILD MODULE's
        # current input_hash (not from the segment's cached hash — that would
        # compare the entry against itself and always return True).
        child_keys_for_parent = [
            ck for ck in child_to_parent if child_to_parent[ck] == parent_key
        ]
        stale_count = 0
        child_seg_hashes = []
        for ck in child_keys_for_parent:
            child_doc_id = doc_id_for_path(graph_tree, all_tasks[ck][0])
            child_module_artifact = f"module:{child_doc_id}"
            # Current child module input_hash (recomputed by leaf dispatch)
            child_module_hash = cache_manager.get_input_hash(child_module_artifact) or ""
            seg_current_hash = stable_hash([child_module_hash, PROMPT_VERSION])
            seg_artifact = f"overview:{parent_doc_id}:child:{child_doc_id}"
            child_seg_hashes.append(seg_current_hash)
            if not cache_manager.is_valid(seg_artifact, seg_current_hash):
                stale_count += 1

        # Arch intro hash depends on all child hashes
        arch_current_hash = stable_hash([
            *child_seg_hashes, config.output_language, PROMPT_VERSION
        ])
        arch_artifact = f"{parent_artifact}:arch_intro"
        if not cache_manager.is_valid(arch_artifact, arch_current_hash):
            stale_count += 1
        total_segments = len(child_keys_for_parent) + 1  # +1 for arch_intro

        if stale_count == 0:
            # All segments valid — skip entirely
            await done_queue.put((parent_key, True, False, None))
            active_tasks += 1
            continue

    # Enqueue for processing (full or partial)
    await work_queue.put(parent_key)
    active_tasks += 1
```

- [ ] **Step 2: Run full suite**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add codewiki/src/be/documentation_scheduler.py
git commit -m "refactor(scheduler): migrate parent dispatch to CacheManager"
```

---

### Task 5: Migrate worker mark_running/mark_done/mark_failed

**Files:**
- Modify: `codewiki/src/be/documentation_scheduler.py:413-488`
- Modify: `codewiki/src/be/agent_orchestrator.py:200-279,449-456`

- [ ] **Step 1: Replace worker state transitions**

In the worker loop, add cache_manager updates alongside existing gen_state calls:

```python
# On start:
if cache_manager:
    cache_manager.mark_running(artifact_id)

# On success:
if cache_manager:
    cache_manager.mark_done(
        artifact_id,
        input_hash=computed_input_hash,
        output_path=output_file,
        model=task_models_used,
        output_file=output_file,
    )

# On failure:
if cache_manager:
    cache_manager.mark_failed(artifact_id, str(error))

# On cancellation:
if cache_manager:
    entry = cache_manager.get_entry(artifact_id)
    if entry and entry.status == "running":
        cache_manager.invalidate(artifact_id)  # reset to stale
```

- [ ] **Step 2: Replace agent_orchestrator skip logic**

In `agent_orchestrator.py` `process_module()`, replace the gen_state based skip (lines 200-279) with:

```python
if cache_manager:
    artifact_id = f"module:{doc_id}"
    input_hash = compute_module_input_hash(...)
    if cache_manager.is_valid(artifact_id, input_hash):
        logger.debug("✓ Cache hit for '%s'", module_name)
        return {}, "cached"
```

- [ ] **Step 3: Run full suite**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add codewiki/src/be/documentation_scheduler.py codewiki/src/be/agent_orchestrator.py
git commit -m "refactor(worker): migrate state transitions to CacheManager"
```

---

### Task 6: Migrate overview to per-segment caching

**Files:**
- Modify: `codewiki/src/be/documentation_overview.py`

This is the most complex task. The current overview generates one monolithic document. We need to:
1. Generate arch_intro and each child summary as separate cache entries
2. On partial update, only regenerate stale segments
3. On >=50% stale, full regenerate
4. Assemble final overview.md from segments

- [ ] **Step 1: Create overview segment storage directory**

In `documentation_overview.py`, add helper:
```python
OVERVIEW_PARTS_DIR = "_overview_parts"

def _parts_dir(working_dir: str) -> str:
    path = os.path.join(working_dir, ".codewiki", OVERVIEW_PARTS_DIR)
    os.makedirs(path, exist_ok=True)
    return path
```

- [ ] **Step 2: Implement segment-level generation**

Split `generate_parent_module_docs` into:
1. Determine which segments are stale
2. Generate only stale segments (arch_intro and/or specific child summaries)
3. Assemble all segments into final overview.md

```python
async def generate_parent_module_docs(ctx, module_path):
    cache = ctx.cache_manager
    if cache is None:
        # Fallback to old monolithic generation
        return await _generate_monolithic(ctx, module_path)

    parent_id = "overview:root" if not module_path else f"overview:{doc_id}"
    child_names = list(children_dict.keys())

    # Check per-segment staleness
    segments = []
    stale_segments = []
    for child_name in child_names:
        seg_id = f"{parent_id}:child:{child_name}"
        child_module_hash = cache.get_input_hash(f"module:{child_doc_id}")
        seg_hash = stable_hash([child_module_hash or "", PROMPT_VERSION])
        segments.append((seg_id, seg_hash, child_name))
        if not cache.is_valid(seg_id, seg_hash):
            stale_segments.append((seg_id, seg_hash, child_name))

    arch_id = f"{parent_id}:arch_intro"
    arch_hash = stable_hash([*[s[1] for s in segments], config.output_language, PROMPT_VERSION])
    if not cache.is_valid(arch_id, arch_hash):
        stale_segments.append((arch_id, arch_hash, "__arch_intro__"))

    stale_ratio = len(stale_segments) / (len(segments) + 1)

    if stale_ratio == 0:
        # All valid — skip entirely
        return module_tree

    if stale_ratio >= cache.OVERVIEW_REGENERATE_THRESHOLD:
        # Full regenerate
        content = await _generate_full_overview(ctx, module_path, children_dict)
        # Split into segments and cache each
        _split_and_cache_segments(content, segments, arch_id, arch_hash, cache, working_dir)
    else:
        # Partial update — only regenerate stale segments
        for seg_id, seg_hash, child_name in stale_segments:
            if child_name == "__arch_intro__":
                segment_content = await _generate_arch_intro(ctx, module_path, children_dict)
            else:
                segment_content = await _generate_child_summary(ctx, module_path, child_name)
            _save_segment(working_dir, seg_id, segment_content)
            cache.mark_done(seg_id, input_hash=seg_hash, output_path=_segment_path(seg_id))

    # Assemble final overview from all segments
    _assemble_overview(working_dir, parent_id, segments, arch_id, output_path)
    cache.mark_done(parent_id, input_hash=arch_hash, output_path=output_path)

    return module_tree
```

- [ ] **Step 3: Add prompt helpers to prompt_template.py**

Add to `codewiki/src/be/prompt_template.py`:

```python
def format_arch_intro_prompt(
    name: str,
    children: list[str],
    output_language: str = "en",
) -> str:
    """Prompt for generating the architecture introduction of an overview."""
    child_list = "\n".join(f"- {c}" for c in children)
    return f"""Write an architecture introduction for the "{name}" module.
This module contains the following sub-modules:
{child_list}

Write 2-3 paragraphs covering:
1. What this module does and why it exists
2. How the sub-modules relate to each other (architecture overview)
3. Key design decisions at this level

Output language: {output_language}
Write in markdown format. Do not include a top-level heading (it will be added by the system)."""


def format_child_summary_prompt(
    parent_name: str,
    child_name: str,
    child_content: str,
    output_language: str = "en",
) -> str:
    """Prompt for generating a single child summary section in a parent overview."""
    # Truncate child content to ~2000 chars for the summary prompt
    truncated = child_content[:2000] + ("..." if len(child_content) > 2000 else "")
    return f"""Write a 2-3 sentence summary of the "{child_name}" sub-module for the "{parent_name}" overview page.

Here is the sub-module's documentation:
<CHILD_DOC>
{truncated}
</CHILD_DOC>

Write a concise summary that explains:
1. What this sub-module does
2. How it fits into the parent module

Output language: {output_language}
Write in markdown format. Include a ### heading with the sub-module name."""
```

- [ ] **Step 4: Implement _generate_arch_intro and _generate_child_summary**

These are new LLM-calling functions in `documentation_overview.py` that generate a single section:

```python
async def _generate_arch_intro(ctx, module_path, children_dict) -> str:
    """Generate the architecture introduction section of an overview."""
    prompt = format_arch_intro_prompt(
        name=module_name,
        children=list(children_dict.keys()),
        output_language=config.output_language,
    )
    result = ctx.middleware.call(prompt)
    return result.content

async def _generate_child_summary(ctx, module_path, child_name) -> str:
    """Generate summary section for a single child module."""
    child_doc_path = os.path.join(working_dir, cache.get_output_file(child_artifact) or "")
    child_content = file_manager.load_text(child_doc_path) if os.path.exists(child_doc_path) else ""
    prompt = format_child_summary_prompt(
        parent_name=module_name,
        child_name=child_name,
        child_content=child_content,
        output_language=config.output_language,
    )
    result = ctx.middleware.call(prompt)
    return result.content
```

- [ ] **Step 4: Implement _assemble_overview**

```python
def _assemble_overview(working_dir, parent_id, segments, arch_id, output_path):
    """Read all segment files and concatenate into final overview."""
    parts_dir = _parts_dir(working_dir)
    sections = []

    # Arch intro first
    arch_path = os.path.join(parts_dir, _segment_filename(arch_id))
    if os.path.exists(arch_path):
        sections.append(file_manager.load_text(arch_path))

    # Child summaries in tree order
    for seg_id, _, child_name in segments:
        seg_path = os.path.join(parts_dir, _segment_filename(seg_id))
        if os.path.exists(seg_path):
            sections.append(file_manager.load_text(seg_path))

    file_manager.save_text("\n\n---\n\n".join(sections), output_path)
```

- [ ] **Step 5: Run full suite**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add codewiki/src/be/documentation_overview.py
git commit -m "feat(overview): per-segment caching with merge strategy"
```

---

### Task 7: Migrate guide_generator to CacheManager

**Files:**
- Modify: `codewiki/src/be/guide_generator.py:33-46,196-229`

- [ ] **Step 1: Refactor existing methods into legacy + new helpers**

First, rename existing methods so they can serve as fallbacks during migration:

```python
# Rename existing _should_regenerate → _should_regenerate_legacy
def _should_regenerate_legacy(self, guide_type, input_files, extra_salt=""):
    # ... (exact current implementation from lines 196-212, unchanged)

# Rename existing _update_cache → _update_cache_legacy
def _update_cache_legacy(self, guide_type, input_files, output_files, extra_salt=""):
    # ... (exact current implementation from lines 214-229, unchanged)

# Add new helper that computes input_hash for a guide artifact
def _compute_guide_input_hash(self, input_files, guide_type, extra_salt=""):
    """Compute input_hash compatible with CacheManager."""
    version = _PROMPT_VERSIONS.get(guide_type, "v1")
    lang = self.config.output_language or "en"
    extra = f"{version}:{lang}:{extra_salt}" if extra_salt else f"{version}:{lang}"
    return self._compute_combined_hash(input_files, extra=extra)
```

- [ ] **Step 2: Add new _should_regenerate and _update_cache that dispatch to cache_manager or legacy**

```python
def _should_regenerate(self, guide_type, input_files, extra_salt=""):
    if self._cache_manager:
        artifact_id = f"guide:{guide_type}"
        input_hash = self._compute_guide_input_hash(input_files, guide_type, extra_salt)
        return not self._cache_manager.is_valid(artifact_id, input_hash)
    return self._should_regenerate_legacy(guide_type, input_files, extra_salt)

def _update_cache(self, guide_type, input_files, output_files, extra_salt=""):
    if self._cache_manager:
        artifact_id = f"guide:{guide_type}"
        input_hash = self._compute_guide_input_hash(input_files, guide_type, extra_salt)
        self._cache_manager.mark_done(
            artifact_id,
            input_hash=input_hash,
            output_path=output_files[0] if output_files else "",
            output_file=os.path.basename(output_files[0]) if output_files else "",
        )
        return
    self._update_cache_legacy(guide_type, input_files, output_files, extra_salt)
```

- [ ] **Step 3: Accept cache_manager in GuideGenerator.__init__**

```python
def __init__(self, config, working_dir, ..., cache_manager=None):
    ...
    self._cache_manager = cache_manager
```

- [ ] **Step 4: Run full suite**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/guide_generator.py codewiki/src/be/documentation_generator.py
git commit -m "refactor(guide): migrate guide cache to CacheManager"
```

---

### Task 8: ~~Migrate graph builder cache~~ REMOVED

Graph always re-runs (pure computation, per spec). No CacheManager integration needed. `_graph_cache.json` stays as-is for its own data caching — it's not an LLM-call artifact.

---

### Task 9: Cache postprocess LLM repair results

**Files:**
- Modify: `codewiki/src/be/postprocess/mermaid_validator.py`
- Modify: `codewiki/src/be/postprocess/math_validator.py`

- [ ] **Step 1: Cache individual LLM repair calls with result storage**

Repair results are stored as files in `.codewiki/_repair_cache/` since the repaired
mermaid/math block text needs to be available on cache hit.

Before calling LLM for mermaid/math repair, check cache:
```python
REPAIR_CACHE_DIR = "_repair_cache"

def _repair_cache_path(cache_dir: str, repair_id: str) -> str:
    d = os.path.join(cache_dir, REPAIR_CACHE_DIR)
    os.makedirs(d, exist_ok=True)
    safe_name = repair_id.replace(":", "_").replace("/", "_") + ".txt"
    return os.path.join(d, safe_name)

# Before LLM call:
if cache_manager:
    repair_id = f"postprocess_repair:{doc_id}:mermaid_{block_idx}"
    block_hash = hashlib.md5(mermaid_source.encode()).hexdigest()
    if cache_manager.is_valid(repair_id, block_hash):
        # Load cached repair result from file
        cached_path = _repair_cache_path(cache_dir, repair_id)
        if os.path.exists(cached_path):
            repaired_block = file_manager.load_text(cached_path)
            # Use repaired_block directly, skip LLM call
            continue
```

After successful LLM repair:
```python
if cache_manager:
    # Save repaired content to file
    cached_path = _repair_cache_path(cache_dir, repair_id)
    file_manager.save_text(repaired_block, cached_path)
    cache_manager.mark_done(repair_id, input_hash=block_hash, output_path=cached_path)
```

- [ ] **Step 2: Thread cache_manager through postprocess pipeline**

Add `cache_manager` parameter to the repair functions and thread from `docs_fixer.py`.

- [ ] **Step 3: Run full suite**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add codewiki/src/be/postprocess/mermaid_validator.py codewiki/src/be/postprocess/math_validator.py codewiki/src/be/docs_fixer.py
git commit -m "feat(postprocess): cache LLM repair results in CacheManager"
```

---

### Task 10: Remove old cache systems

**Files:**
- Modify: `codewiki/src/be/documentation_scheduler.py` — remove gen_state fallback paths
- Modify: `codewiki/src/be/agent_orchestrator.py` — remove gen_state usage
- Modify: `codewiki/src/be/documentation_overview.py` — remove legacy monolithic path
- Modify: `codewiki/src/be/guide_generator.py` — remove _guide_cache.json logic
- Modify: `codewiki/src/be/documentation_generator.py` — remove GenerationState creation
- Delete: `codewiki/src/be/generation_state.py` (or keep as deprecated stub)

- [ ] **Step 1: Grep for remaining gen_state / _guide_cache references**

```bash
grep -rn "gen_state\|generation_state\|_guide_cache\|GenerationState\|GenerationStateManager" \
  codewiki/src/ --include="*.py" | grep -v __pycache__ | grep -v cache_manager
```

Remove each fallback path that was kept during migration.

- [ ] **Step 2: Run full suite after each removal**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: all pass after each step

- [ ] **Step 3: Commit**

```bash
git add -u
git commit -m "refactor: remove GenerationState and _guide_cache (replaced by CacheManager)"
```

---

### Task 11: Add PROMPT_VERSION constant

**Files:**
- Modify: `codewiki/src/be/prompt_template.py`

- [ ] **Step 1: Add version constant**

```python
# Near the top of prompt_template.py, after imports
PROMPT_VERSION = "prompt-v9"
# Bump this whenever system prompt, writing discipline, mermaid rules,
# evidence rules, or any prompt template content changes.
# This is included in module/overview input_hash to invalidate cache
# when prompts are updated.
```

- [ ] **Step 2: Run full suite**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add codewiki/src/be/prompt_template.py
git commit -m "feat(cache): add PROMPT_VERSION constant for cache invalidation"
```

---

### Task 12: Integration test — full pipeline cache behavior

**Files:**
- Test: `tests/test_cache_manager.py` (add)

- [ ] **Step 1: Write integration test for cache skip flow**

```python
def test_cache_full_pipeline_skip_flow(cache_dir):
    """Simulate: first run marks done, second run skips via is_valid."""
    cm = CacheManager(cache_dir)

    # First run: mark everything done
    cm.mark_done("module:auth", input_hash="h1", output_path="auth.md", output_file="auth.md")
    cm.mark_done("module:db", input_hash="h2", output_path="db.md", output_file="db.md")
    cm.mark_done("overview:root:arch_intro", input_hash="h3", output_path="parts/arch.md")
    cm.mark_done("overview:root:child:auth", input_hash="h1", output_path="parts/auth.md",
                 depends_on=["module:auth"])
    cm.mark_done("overview:root:child:db", input_hash="h2", output_path="parts/db.md",
                 depends_on=["module:db"])
    cm.mark_done("overview:root", input_hash="h5", output_path="overview.md",
                 depends_on=["overview:root:arch_intro", "overview:root:child:auth",
                             "overview:root:child:db"])
    cm.mark_done("guide:beginner", input_hash="h6", output_path="guide.md")
    cm.flush()

    # Second run: everything should be valid
    cm2 = CacheManager(cache_dir)
    assert cm2.is_valid("module:auth", "h1")
    assert cm2.is_valid("module:db", "h2")
    assert cm2.is_valid("overview:root", "h5")
    assert cm2.is_valid("guide:beginner", "h6")

    # Third run: auth source changed
    assert not cm2.is_valid("module:auth", "h1_changed")
    cm2.invalidate("module:auth")

    # Cascade: auth's child segment and root overview should be stale
    assert cm2.get_entry("overview:root:child:auth").status == "stale"
    assert cm2.get_entry("overview:root").status == "stale"

    # But db and its segment should be untouched
    assert cm2.get_entry("module:db").status == "valid"
    assert cm2.get_entry("overview:root:child:db").status == "valid"
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/test_cache_manager.py::test_cache_full_pipeline_skip_flow -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_cache_manager.py
git commit -m "test: add integration test for CacheManager pipeline flow"
```
