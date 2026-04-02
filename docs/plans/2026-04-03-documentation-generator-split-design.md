# Documentation Generator Split Design

**Date:** 2026-04-03
**Target:** `codewiki/src/be/documentation_generator.py`
**Goal:** Split the 1100+ line generator into smaller modules while preserving current behavior and continuing the v7 naming/state cleanup.

---

## Context

`documentation_generator.py` currently mixes five responsibilities:

1. Tree and filename helper logic
2. Generation ledger/task construction
3. Queue scheduling and retry behavior
4. Overview/parent-document assembly
5. Top-level orchestration

That makes the file hard to review and increases the risk of v7 regressions when we touch one concern and accidentally affect another.

The split should preserve the existing public entry point, `DocumentationGenerator.run()`, and keep the current v7 behavior:

- frozen `_doc_filename`
- `generation_state.json` ledger
- assigned filenames for agents
- pre-validation link rewriting
- `.codewiki/` internal state files

## Design

### 1. Keep one orchestrator, move helpers out

`DocumentationGenerator` stays as the top-level coordinator. It will continue to own:

- `config`
- `commit_id`
- `graph_builder`
- `agent_orchestrator`
- loaded `GenerationState`
- loaded `GenerationStateManager`

But it will delegate most helper behavior to three new modules:

- `documentation_tree_utils.py`
- `documentation_overview.py`
- `documentation_scheduler.py`

### 2. `documentation_tree_utils.py`

This module owns logic that is pure or mostly pure with respect to the module tree and docs directory:

- `_iter_tree_nodes`
- `_collect_path_counts`
- `_stable_hash`
- `_hash_mapping`
- `_content_similarity`
- `dedup_docs_directory`
- `cleanup_legacy_internal_files`
- `config_fingerprint`
- `freeze_doc_filenames`
- `build_generation_tasks`
- `module_doc_exists`

This module should not import `DocumentationGenerator`.

It may accept small explicit inputs such as:

- `tree`
- `working_dir`
- `config`
- `gen_state`

### 3. `documentation_overview.py`

This module owns parent/overview generation behavior:

- `strip_tree_for_overview`
- `build_overview_structure`
- `collect_child_doc_hashes`
- `generate_parent_module_docs`

It should depend on explicit call arguments, not generator instance state wherever possible.

It may take:

- `config`
- `call_llm`
- `module_tree`
- `module_path`
- `working_dir`
- `gen_state`
- `state_mgr`
- `tree_manager`

### 4. `documentation_scheduler.py`

This module owns queue execution and retry/unblock behavior:

- `run_module_queue`
- `fill_missing_module_docs`

This is the most stateful extraction. It should be passed all needed collaborators explicitly:

- `config`
- `graph_tree`
- `components`
- `working_dir`
- `tree_manager`
- `agent_orchestrator`
- `gen_state`
- `state_mgr`
- callback or helper for `generate_parent_module_docs`
- callback or helper for `module_doc_exists`

That keeps scheduler behavior testable without needing the whole generator object.

### 5. `documentation_generator.py` after split

After extraction, the main file should mostly contain:

- constructor
- repo metadata helpers
- `create_documentation_metadata`
- `generate_module_documentation`
- `run`

The goal is to turn it into an orchestrator rather than a grab-bag of helpers.

## Behavioral Constraints

The split must not change these behaviors:

- existing `run()` call sites still work
- small repo path still works
- module-tree caching and ledger reuse still work
- overview prompt language injection still works
- doc filename freeze remains stable
- worker retries and fill-pass behavior remain unchanged

## v7 Cleanup To Fold In

While splitting, keep tightening the v7 transition:

- prefer direct attribute access over repeated `getattr(self, "_gen_state", None)` where safe
- reduce duplicate helper logic
- keep read paths aligned with frozen `_doc_filename`
- do not reintroduce old `_completed` or `_parent_doc_hashes` semantics

## Non-Goals

This split does **not** attempt to:

- redesign generation scheduling
- migrate guide generation to the ledger
- change the clustering/generation architecture
- clean historical duplicate output directories automatically

## Success Criteria

The split is complete when:

- `documentation_generator.py` is substantially smaller and orchestration-focused
- the three new modules each have clear ownership
- existing v7 tests still pass
- new module-level tests cover extracted behavior directly
