# Unified Cache System Design

## Background

CodeWiki currently has 7 independent cache/persistence mechanisms scattered across 6+ files, each with its own invalidation logic:

- `_graph_cache.json` — dependency graph (commit + patterns)
- `first_module_tree.json` — clustering result (config_fingerprint)
- `generation_state.json` — task completion state (input_hash per task)
- `_guide_cache.json` — guide generation (input file hash + version)
- Postprocess — no file cache, LLM repair results not cached
- Index products — not persisted, rebuilt every run
- Module tree — persisted but no cache key

**Problems:**
1. No single entry point — cache reads/writes scattered across files with `file_manager.save_json`/`load_json`
2. No dependency cascade — cluster cache invalidated but generation_state kept stale task IDs
3. Scheduler doesn't filter at dispatch — tasks enter worker before discovering they can be skipped
4. Cascade amplification — one file change triggers regeneration of entire parent chain
5. LLM non-determinism — using output content hash for cascade causes unnecessary downstream regeneration

## Goal

A single `CacheManager` that:
1. **Single entry point** for all cache operations
2. **Dependency cascade** — upstream invalidation automatically propagates downstream
3. **Scheduler integration** — tasks filtered before entering work queue, no wasted LLM calls
4. **Incremental updates** — commit changes only regenerate modules whose component source actually changed
5. **Smart parent updates** — overview docs updated per-child-segment, not regenerated wholesale

## Design

### CacheEntry Model

```python
@dataclass
class CacheEntry:
    artifact_id: str        # e.g. "module:auth", "overview:root:child:db", "guide:beginner"
    input_hash: str         # determines if regeneration needed
    status: str             # "valid" | "stale" | "missing" | "running" | "failed"
    depends_on: list[str]   # upstream artifact_ids
    output_path: str        # relative path to output file (or None for virtual entries)

    # ── Task state fields (replaces GenerationState responsibilities) ──
    output_file: str = ""   # assigned output filename (e.g. "auth.md") — used by
                            # overview generator to locate child docs via get_output_file()
    model: str = ""         # which LLM model produced this result
    attempt_count: int = 0  # retry tracking
    error: str = ""         # last error message (when status == "failed")
    updated_at: str = ""    # ISO timestamp of last status change
```

**Status lifecycle:** `missing → running → valid` (success) or `missing → running → failed` (error).
On crash recovery: all `running` entries reset to `stale` on next startup (same as current GenerationState behavior).

**Output file assignment:** `output_file` is set when the task is first planned (before execution), same as current `DocTask.output_file`. `CacheManager.plan_task()` handles collision-free filename assignment, replacing `GenerationState.plan_task()`.

### Artifact ID Convention

```
graph                               — dependency graph (pure computation)
index                               — symbol table + edges (pure computation)
cluster                             — Leiden graph clustering (pure computation)
cluster_llm:{group_name}            — LLM semantic refinement per group
module:{doc_id}                     — leaf module documentation
overview:{doc_id}                   — assembled parent/overview document
overview:{doc_id}:arch_intro        — architecture intro section of overview
overview:{doc_id}:child:{child_id}  — per-child summary section of overview
guide:{guide_type}                  — guide document (beginner, algorithm, etc.)
postprocess_repair:{doc_id}:{idx}   — LLM mermaid/math repair result
```

### Dependency DAG

```
graph (pure computation, always re-run)
  → index (pure computation, always re-run)
    → cluster (pure computation, always re-run — Leiden is seconds even for 12K nodes)
        → cluster_llm:* (only re-run if Leiden group membership changed)

module:auth    input_hash = hash(module_name + module_path + sorted component IDs
                               + component source hashes + assigned_filename
                               + language + custom_instructions_hash + PROMPT_VERSION)
module:db      input_hash = hash(same structure)
  depends_on: [] (no explicit dependency on cluster — but module_name/path/component set
  come FROM the cluster result, so if clustering changes them, input_hash changes naturally
  without needing a cascade edge. This avoids "recluster → all modules stale" while still
  catching real changes like module renaming or component reassignment.)

overview:auth:arch_intro      input_hash = hash(child module ID list + all child module input_hashes
                                              + language + PROMPT_VERSION)
                              — changes when modules added/removed OR any child's inputs change
                              OR prompt template updated
overview:auth:child:jwt       input_hash = hash(module:auth/jwt input_hash + PROMPT_VERSION)
                              — child inputs didn't change AND prompt unchanged → summary still valid
overview:auth:child:session   input_hash = hash(module:auth/session input_hash + PROMPT_VERSION)
overview:auth                 input_hash = hash(all segment input_hashes)
  merge_strategy: if <50% segments stale → patch segments; >=50% → full regenerate
  (threshold is CacheManager.OVERVIEW_REGENERATE_THRESHOLD, not per-entry)

guide:beginner   input_hash = hash(input file contents) + prompt_version + language
guide:algorithm  input_hash = hash(module doc contents) + prompt_version + language

postprocess_repair:auth:mermaid_0   input_hash = hash(md file content at that block)
```

### Key Design Decisions

**input_hash (not output_hash) for cascade.**
LLM output is non-deterministic. Same input → slightly different output → different output_hash → unnecessary downstream cascade. Using input_hash: if upstream inputs haven't changed, downstream considers upstream output unchanged regardless of actual content differences.

**graph/index always re-run, module-level caching is where money is saved.**
Graph parsing and index building are pure computation (seconds). Module doc generation is LLM calls (dollars). The cache precision is concentrated where the cost is.

**Cluster split: pure computation vs LLM refinement.**
Leiden clustering is cheap. LLM semantic refinement (merge/split/name clusters) is expensive. Cache them separately — Leiden re-runs freely, LLM only re-runs when group membership actually changes.

**Overview per-child segment caching with regeneration threshold.**
Each child's summary paragraph in an overview is a separate cache entry. When a child changes, only that paragraph is regenerated. But if >50% of paragraphs need updating, the whole overview is regenerated for better coherence. The threshold is `CacheManager.OVERVIEW_REGENERATE_THRESHOLD` (class constant, default 0.5).

### CacheManager API

```python
class CacheManager:
    # Policy constant: if more than this fraction of overview child segments are stale,
    # regenerate the entire overview instead of patching segments.
    # Can be overridden via config if needed in the future.
    OVERVIEW_REGENERATE_THRESHOLD = 0.5

    def __init__(self, cache_dir: str, flush_interval: float = 10.0):
        self._entries: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self._dirty = False
        self._flush_thread: threading.Thread  # background flush thread

    # ── Query ──────────────────────────────────────────────────

    def is_valid(self, artifact_id: str, current_input_hash: str) -> bool:
        """Scheduler calls before dispatching a task.
        Returns True if entry exists, status == 'valid', and input_hash matches."""

    def get_entry(self, artifact_id: str) -> CacheEntry | None:
        """Get entry metadata (for computing downstream input_hash)."""

    def get_input_hash(self, artifact_id: str) -> str | None:
        """Shortcut: get upstream's input_hash for downstream dependency calculation."""

    def get_output_file(self, artifact_id: str) -> str | None:
        """Get the assigned output filename. Used by overview generator to locate child docs."""

    # ── Task lifecycle ─────────────────────────────────────────

    def plan_task(self, artifact_id: str, output_file: str, depends_on: list[str] | None = None):
        """Register a task before execution. Assigns output_file with collision avoidance.
        Sets status to 'missing' if new, preserves status if entry already exists."""

    def mark_running(self, artifact_id: str):
        """Worker starting execution. For crash recovery detection."""

    def mark_done(self, artifact_id: str, input_hash: str, output_path: str,
                  model: str = "", depends_on: list[str] | None = None):
        """Worker completed successfully. Sets status to 'valid'."""

    def mark_failed(self, artifact_id: str, error: str):
        """Worker failed. Sets status to 'failed', records error message."""

    def invalidate(self, artifact_id: str):
        """Mark stale and recursively cascade to all downstream dependents."""

    # ── Batch operations ───────────────────────────────────────

    def invalidate_downstream(self, artifact_ids: list[str]):
        """Invalidate all entries that transitively depend on any of the given IDs."""

    def get_stale_entries(self, prefix: str = "") -> list[CacheEntry]:
        """Get all stale/missing entries, optionally filtered by prefix (e.g. 'module:')."""

    # ── Persistence ────────────────────────────────────────────

    def flush(self):
        """Write to disk immediately. Safe to call from any thread."""

    def start(self):
        """Start background flush thread."""

    def stop(self):
        """Stop background flush thread and final flush."""
```

### Storage

Single file: `{docs_dir}/.codewiki/cache_registry.json`

```json
{
  "schema_version": "cache.v1",
  "entries": {
    "cluster_llm:auth_group": {
      "input_hash": "abc123",
      "status": "valid",
      "depends_on": [],
      "output_path": "first_module_tree.json"
    },
    "module:auth": {
      "input_hash": "ghi789",
      "status": "valid",
      "depends_on": [],
      "output_path": "auth.md",
      "output_file": "auth.md"
    },
    "overview:root:child:auth": {
      "input_hash": "ghi789",
      "status": "valid",
      "depends_on": ["module:auth"],
      "output_path": ".codewiki/_overview_parts/root_child_auth.md"
    },
    "overview:root": {
      "input_hash": "mno345",
      "status": "valid",
      "depends_on": ["overview:root:arch_intro", "overview:root:child:auth", "overview:root:child:db"],
      "output_path": "overview.md"
    }
  }
}
```

**Replaces:**
- `generation_state.json` — task status + input_hash + content_hash merged into cache entries
- `_guide_cache.json` — guide entries merged
- `_graph_cache.json` cache key logic — graph entry merged

**Actual output files unchanged** — `*.md`, `module_tree.json`, `first_module_tree.json`, `metadata.json` stay where they are.

**Overview segment intermediate files** stored in `{docs_dir}/.codewiki/_overview_parts/`.

### Persistence Strategy

**Background flush thread:**
- Dedicated thread, runs `flush()` every N seconds (default 10)
- Merges all in-memory changes since last flush into single disk write
- Reduces I/O from "once per task completion" to "once per 10 seconds"

**Explicit flush points:**
- Stage transitions (between pipeline stages)
- Graceful shutdown (Ctrl+C signal handler)
- Program can call `cache.flush()` at any time

**Thread safety:**
- `threading.Lock` protects `_entries` dict and `_dirty` flag
- `mark_done` / `invalidate` / `is_valid` all acquire lock
- `flush()` acquires lock, serializes, writes to disk

**Crash recovery (on next startup):**
- Entry says "valid" but output file missing → mark "missing"
- File exists but no entry → ignore (orphan file)
- Entry was mid-write (corrupted JSON) → reset to empty, regenerate all

### Scheduler Integration

**Leaf module dispatch** (replaces `documentation_scheduler.py` lines 168-175):
```python
for key, (path, _, info, is_leaf) in all_tasks.items():
    if is_leaf:
        artifact_id = f"module:{doc_id_for_path(tree, path)}"
        input_hash = compute_module_input_hash(info, components)
        if cache.is_valid(artifact_id, input_hash):
            await done_queue.put((key, True, False, None))
            continue
        await work_queue.put(key)
```

**Parent module dispatch** (replaces coordinator lines 267-332):
```python
if pending_count[parent_key] == 0:
    artifact_id = f"overview:{parent_doc_id}"
    # Check per-segment staleness
    stale_segments = count_stale_child_segments(cache, parent_doc_id, child_ids)
    total_segments = len(child_ids) + 1  # +1 for arch_intro
    if stale_segments == 0:
        # All segments valid — skip entirely
        await done_queue.put((parent_key, True, False, None))
        continue
    if stale_segments / total_segments >= cache.OVERVIEW_REGENERATE_THRESHOLD:
        # Too many changes — full regenerate
        enqueue as full_regenerate task
    else:
        # Partial update — only regenerate stale segments then reassemble
        enqueue as partial_update task
```

**Guide dispatch** (replaces GuideGenerator._should_regenerate):
```python
artifact_id = f"guide:{guide_type}"
input_hash = compute_guide_input_hash(input_files, version, language)
if cache.is_valid(artifact_id, input_hash):
    continue  # skip entirely at stage level
```

**Key principle:** `work_queue` only contains tasks that genuinely need LLM calls.

### input_hash Computation

| Artifact | input_hash = |
|----------|--------------|
| `cluster` | not cached — always re-runs (pure computation, seconds) |
| `cluster_llm:{group}` | `hash(sorted component IDs in this Leiden group)` |
| `module:{id}` | `hash(module_name + module_path + sorted component IDs + component source hashes + assigned_filename + language + custom_instructions_hash + PROMPT_VERSION)` |
| `overview:{id}:arch_intro` | `hash(child module ID list + all child module input_hashes + language + PROMPT_VERSION)` |
| `overview:{id}:child:{cid}` | `module:{cid}` input_hash + `PROMPT_VERSION` — child inputs didn't change AND prompt unchanged means summary still valid |
| `overview:{id}` | `hash(all segment input_hashes)` |
| `guide:{type}` | `hash(input file contents) + guide_prompt_version + language` |
| `postprocess_repair:{id}:{idx}` | `hash(md content of that specific mermaid/math block)` |

**`PROMPT_VERSION`**: A constant string (e.g. `"prompt-v9"`) bumped whenever `prompt_template.py` or system prompt content changes. This covers system prompt, writing discipline, mermaid rules, evidence rules — all prompt-level inputs that aren't captured by component source hashes. Lives in `prompt_template.py` alongside the prompts themselves.

**`custom_instructions_hash`**: `hash(config.custom_instructions or "")`. User-supplied instructions that become part of the system prompt.

**Deliberate approximation: `context_pack` not in module input_hash.**
The module prompt also includes a `context_pack` (glossary, link_map from index_products). This is intentionally excluded from input_hash because:
1. context_pack content is derived from the same components — if component source hashes haven't changed, glossary/link_map won't change either.
2. Including it would require serializing and hashing the entire glossary+link_map on every cache check, which is expensive for large repos.
3. Index is rebuilt every run anyway, so the derivation is always fresh.

This means: if someone manually edits glossary logic without changing component source, the module cache won't invalidate. This is an acceptable trade-off — prompt template changes are covered by `PROMPT_VERSION`.

### Incremental Update Flow (new commit)

```
1. graph re-run (seconds, pure computation)
2. index re-run (seconds, pure computation)
3. cluster: Leiden always re-runs (seconds, pure computation)
           → for each group: component membership unchanged → skip LLM refinement/naming
                            : component membership changed → re-run LLM refinement/naming
4. For each module:
     compute input_hash = hash(module_name + module_path + component IDs + source hashes + filename + language + custom_instructions_hash + PROMPT_VERSION)
     cache.is_valid? → yes: skip (no LLM call)
                     → no:  enqueue, generate doc, cache.mark_done
5. For each parent overview:
     count stale child segments
     0 stale → skip entirely
     <50% stale → partial update (regenerate only stale segments, reassemble)
     >=50% stale → full regenerate
6. For each guide:
     compute input_hash from input files
     cache.is_valid? → skip or regenerate
7. For each postprocess repair:
     compute input_hash from md block content
     cache.is_valid? → skip or re-run LLM repair
```

### Migration from Current System

**Phase 1:** Create `CacheManager` class + `cache_registry.json`

**Phase 2:** Migrate scheduler to use `cache.is_valid()` for dispatch filtering

**Phase 3:** Migrate each stage:
- `dependency_graphs_builder.py` — register graph entry on completion
- `index_builder.py` — persist index products, register entry
- `cluster_modules.py` — split Leiden vs LLM caching
- `agent_orchestrator.py` — replace `generation_state` task lookup with `cache.is_valid()`
- `documentation_overview.py` — implement per-segment caching + merge strategy
- `guide_generator.py` — replace `_guide_cache.json` with cache entries
- `mermaid_validator.py` / `math_validator.py` — cache LLM repair results

**Phase 4:** Delete old cache files:
- Remove `GenerationState` / `GenerationStateManager` classes
- Remove `_guide_cache.json` logic from `GuideGenerator`
- Remove `_graph_cache.json` logic from `DependencyGraphBuilder`
- Remove `_mark_stale_tasks` / `mark_stale` from scheduler

### Concurrency Safety

- `CacheManager._lock` (threading.Lock) protects all in-memory operations
- Background flush thread acquires lock only during serialization
- 12 concurrent workers calling `mark_done` → lock contention is minimal (dict update is microseconds)
- `is_valid` is read-only under lock — no write contention on hot path
- Actual file I/O (writing md files) happens outside the lock in workers

### Overview Segment Storage

Per-child segments stored as separate files in `.codewiki/_overview_parts/`:
```
.codewiki/_overview_parts/
  root_arch_intro.md
  root_child_auth.md
  root_child_db.md
  root_child_api.md
  auth_arch_intro.md
  auth_child_jwt.md
  auth_child_session.md
```

Assembly: read all valid segments for an overview, concatenate in tree order, write to final `overview.md`. If regenerating the whole overview, write directly to `overview.md` and split into segments afterward for future incremental use.
