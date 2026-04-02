# Documentation Generator Split Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split `documentation_generator.py` into focused modules while preserving current v7 behavior and keeping the public generation entry points stable.

**Architecture:** Extract tree/ledger helpers, overview generation, and scheduler logic into dedicated backend modules. Keep `DocumentationGenerator` as the orchestration layer that wires graph build, clustering, generation state, agents, overview generation, guide generation, and postprocess together.

**Tech Stack:** Python 3.13, pytest, asyncio, existing CodeWiki backend modules

---

### Task 1: Extract tree utility helpers

**Files:**
- Create: `codewiki/src/be/documentation_tree_utils.py`
- Modify: `codewiki/src/be/documentation_generator.py`
- Test: `tests/test_documentation_tree_utils.py`

**Step 1: Write the failing tests**

Cover:

- `freeze_doc_filenames()` preserves existing frozen names
- colliding paths are disambiguated
- `build_generation_tasks()` builds child dependencies and root overview
- `module_doc_exists()` prefers ledger-completed output file
- `cleanup_legacy_internal_files()` removes root cache files

**Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_documentation_tree_utils.py -q
```

Expected: import/module errors or missing symbol failures.

**Step 3: Create `documentation_tree_utils.py`**

Move these helpers out of `documentation_generator.py`:

- `_iter_tree_nodes`
- `_collect_path_counts`
- `_stable_hash`
- `_hash_mapping`
- `_content_similarity`
- `dedup_docs_directory`
- `cleanup_legacy_internal_files`
- `_config_fingerprint` as `config_fingerprint`
- `_freeze_doc_filenames` as `freeze_doc_filenames`
- `_build_generation_tasks` as `build_generation_tasks`
- `_module_doc_exists` as `module_doc_exists`

Keep signatures explicit. Do not depend on `self`.

**Step 4: Rewire `documentation_generator.py`**

Import and use the extracted helpers. Remove their in-file definitions.

**Step 5: Run tests**

Run:

```bash
pytest tests/test_documentation_tree_utils.py tests/test_documentation_generator_state_bridge.py -q
```

**Step 6: Commit**

```bash
git add codewiki/src/be/documentation_tree_utils.py codewiki/src/be/documentation_generator.py tests/test_documentation_tree_utils.py tests/test_documentation_generator_state_bridge.py
git commit -m "refactor(generation): extract tree utility helpers"
```

### Task 2: Extract overview generation logic

**Files:**
- Create: `codewiki/src/be/documentation_overview.py`
- Modify: `codewiki/src/be/documentation_generator.py`
- Test: `tests/test_documentation_overview.py`

**Step 1: Write the failing tests**

Cover:

- `build_overview_structure()` repo-level fallback behavior
- sub-module child doc loading
- `collect_child_doc_hashes()` prefers ledger content hashes
- `generate_parent_module_docs()` skips when input hash unchanged

**Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_documentation_overview.py -q
```

**Step 3: Create `documentation_overview.py`**

Move and adapt:

- `_strip_tree_for_overview`
- `build_overview_structure`
- `_collect_child_doc_hashes`
- `generate_parent_module_docs`

Use explicit parameters for config, module tree, working dir, `gen_state`, `state_mgr`, and `tree_manager`.

**Step 4: Rewire the generator**

Keep the public behavior the same, but replace in-class helper logic with calls into the new module.

**Step 5: Run tests**

Run:

```bash
pytest tests/test_documentation_overview.py tests/test_build_overview_structure.py tests/test_overview_language.py -q
```

**Step 6: Commit**

```bash
git add codewiki/src/be/documentation_overview.py codewiki/src/be/documentation_generator.py tests/test_documentation_overview.py tests/test_build_overview_structure.py tests/test_overview_language.py
git commit -m "refactor(generation): extract overview helpers"
```

### Task 3: Extract queue scheduler

**Files:**
- Create: `codewiki/src/be/documentation_scheduler.py`
- Modify: `codewiki/src/be/documentation_generator.py`
- Test: `tests/test_documentation_scheduler.py`

**Step 1: Write the failing tests**

Cover:

- leaf tasks are enqueued first
- parent tasks unblock when children finish
- root overview enqueue behavior
- failed tasks mark ledger state
- fill pass respects `module_doc_exists`

**Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_documentation_scheduler.py -q
```

**Step 3: Create `documentation_scheduler.py`**

Move:

- `_run_module_queue`
- `_fill_missing_module_docs`

Inject all collaborators explicitly rather than importing generator internals.

**Step 4: Rewire the generator**

Update `DocumentationGenerator.generate_module_documentation()` to call into the new scheduler module.

**Step 5: Run tests**

Run:

```bash
pytest tests/test_documentation_scheduler.py tests/test_documentation_generator_worker_cleanup.py -q
```

**Step 6: Commit**

```bash
git add codewiki/src/be/documentation_scheduler.py codewiki/src/be/documentation_generator.py tests/test_documentation_scheduler.py tests/test_documentation_generator_worker_cleanup.py
git commit -m "refactor(generation): extract scheduler logic"
```

### Task 4: Thin down `DocumentationGenerator`

**Files:**
- Modify: `codewiki/src/be/documentation_generator.py`
- Test: `tests/test_documentation_generator_state_bridge.py`

**Step 1: Write a failing assertion or extend an existing bridge test**

Add coverage for:

- `generate_module_documentation()` still initializes ledger + frozen filenames
- `run()` still orchestrates clustering, module docs, metadata, guides, and postprocess in order

**Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_documentation_generator_state_bridge.py -q
```

**Step 3: Refactor the main file**

Reduce `documentation_generator.py` to orchestration-focused methods:

- constructor
- metadata creation
- `generate_module_documentation`
- `run`

Remove stale comments and leftover local helper code.

**Step 4: Run tests**

Run:

```bash
pytest tests/test_documentation_generator_state_bridge.py tests/test_generation_state.py tests/test_generation_glossary.py -q
```

**Step 5: Commit**

```bash
git add codewiki/src/be/documentation_generator.py tests/test_documentation_generator_state_bridge.py tests/test_generation_state.py tests/test_generation_glossary.py
git commit -m "refactor(generation): slim documentation generator"
```

### Task 5: Full regression verification

**Files:**
- Modify if needed: any failing files from prior tasks
- Test: existing v7 regression suite

**Step 1: Run full targeted regression suite**

Run:

```bash
python3.13 -m py_compile codewiki/src/be/documentation_tree_utils.py codewiki/src/be/documentation_overview.py codewiki/src/be/documentation_scheduler.py codewiki/src/be/documentation_generator.py
pytest -q tests/test_documentation_tree_utils.py tests/test_documentation_overview.py tests/test_documentation_scheduler.py tests/test_generation_state.py tests/test_str_replace_editor_assigned_filename.py tests/test_module_doc_filename.py tests/test_link_rewriter.py tests/test_static_generator_corner_cases.py tests/test_overview_language.py tests/test_generation_glossary.py tests/test_documentation_generator_state_bridge.py tests/test_agent_assigned_filename.py tests/test_postprocess_link_validator.py tests/test_perf_docs_fixer.py
```

**Step 2: Fix any regressions**

Apply minimal fixes only where tests prove the split changed behavior.

**Step 3: Commit**

```bash
git add codewiki/src/be/documentation_tree_utils.py codewiki/src/be/documentation_overview.py codewiki/src/be/documentation_scheduler.py codewiki/src/be/documentation_generator.py tests/test_documentation_tree_utils.py tests/test_documentation_overview.py tests/test_documentation_scheduler.py
git commit -m "test(generation): verify generator split regressions"
```

Plan complete and saved to `docs/plans/2026-04-03-documentation-generator-split.md`. Two execution options:

**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

**Which approach?**
