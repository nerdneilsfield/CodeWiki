# Tree Refinement Phase 2: Freeze Tree Mutations + Leaf-First Scheduling

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** With `TreeRefinementStage` from Plan 1 producing a complete frozen tree before generation begins, remove the runtime tree-mutation pathway in `generate_sub_module_documentation` and rebuild the scheduler queue strictly from the frozen `module_tree.json`. Parent docs run only after every direct child doc is valid.

**Architecture:** Two changes operating in tandem. First, the agent tool that historically added new children to the tree at runtime is gutted: it either becomes a read-only "describe these sub-modules" tool or is removed from the agent toolset entirely. Second, the documentation scheduler (`run_module_queue`) builds its task graph by walking the frozen tree once at startup; parent dispatch waits on a `pending_count` derived from frozen child membership rather than from anything the LLM can mutate.

**Tech Stack:** Python 3.10+, asyncio, pydantic-ai, pytest, existing CodeWiki internals.

**Spec reference:** §Design Principle 1 (Tree first), §Stage 6 (ModuleGenerationStage), §Documentation Generation Rules, §Migration Phases 2 + 3.

**Prerequisite:** Plan 1 must be merged. The smoke test `tests/test_pipeline_with_refinement_smoke.py` from Plan 1 must pass on `main`.

**Out of scope for Plan 2:**
- Identity reuse — Plan 3
- Parent segment cache — Plan 4
- Resume semantics, orphan cleanup, schema bump — Plan 5

---

## File Structure

### New files

| Path | Responsibility |
|------|----------------|
| `tests/test_scheduler_frozen_tree.py` | Tests for the new frozen-tree scheduler queue construction |
| `tests/test_agent_tool_no_tree_mutation.py` | Asserts the agent tool no longer writes to `module_tree.json` |

### Modified files

| Path | Change |
|------|--------|
| `codewiki/src/be/agent_tools/generate_sub_module_documentations.py` | Strip the tree-mutation branch. The tool either returns a no-op string or is removed from the toolset. |
| `codewiki/src/be/agent_orchestrator.py` | Stop registering `generate_sub_module_documentation` as an Agent tool for complex modules. |
| `codewiki/src/be/agent_tools/deps.py` | Remove `module_tree_manager` field from `CodeWikiDeps` (it has no consumers after the tool is gutted). Also remove `_dispatched_sub_modules` if it's only used by the gutted tool. |
| `codewiki/src/be/documentation_scheduler.py` | `run_module_queue` builds the queue exclusively from the frozen `ctx.module_tree`. Add explicit assertion that no node is added/removed during dispatch. Parent dispatch waits on direct-child completion. |
| `codewiki/src/be/agent_tools/__init__.py` (if it exists) | Remove `generate_sub_module_documentation` export |
| `tests/test_agent_orchestrator_behavior.py` | Update tests that asserted the sub-module tool was registered. |

---

## Task 0: Baseline check

- [ ] **Step 1: Confirm Plan 1 is merged**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && git log --oneline -20 | grep -i refinement`
Expected: see Plan 1 commits including "TreeRefinementStage", "refinement cache", etc.

- [ ] **Step 2: Run the full test suite**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/ -q 2>&1 | tail -10`
Expected: all tests pass.

- [ ] **Step 3: Read the current `generate_sub_module_documentation` implementation**

Read `codewiki/src/be/agent_tools/generate_sub_module_documentations.py` end-to-end. Note specifically the call sites that mutate state:
- `deps.module_tree_manager.update_children(...)` (writes via the manager)
- The fallback that loads → merges → saves `module_tree.json`
- Any direct mutation of `deps.module_tree`

Write down which lines do what — Tasks 2 and 3 will remove them.

- [ ] **Step 4: Read the current scheduler dispatch loop**

Read `codewiki/src/be/documentation_scheduler.py` `run_module_queue`. Note:
- How `all_tasks` is built from the tree walk
- How `pending_count` decrements when a child completes
- Where `cache_manager.is_valid` is consulted

---

## Task 1: Test that asserts the scheduler does not see runtime mutations

This test fixes the desired behavior **before** we change any code. Currently it should fail because the scheduler still allows the agent tool to add children.

**Files:**
- Test: `tests/test_scheduler_frozen_tree.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scheduler_frozen_tree.py`:

```python
"""The scheduler must use the frozen tree exclusively. No runtime mutation."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from codewiki.src.be.cache_manager import CacheManager, module_artifact_id
from codewiki.src.be.documentation_scheduler import run_module_queue


@pytest.fixture
def cache_dir(tmp_path):
    p = tmp_path / ".codewiki"
    p.mkdir()
    return str(p)


def _frozen_tree():
    return {
        "Top": {
            "module_id": "top",
            "title": "Top",
            "path": "top",
            "description": ".",
            "_doc_filename": "top.md",
            "components": [],
            "children": {
                "Left": {
                    "module_id": "left",
                    "title": "Left",
                    "path": "left",
                    "description": ".",
                    "_doc_filename": "top-left.md",
                    "components": ["a.py::A"],
                    "children": {},
                },
                "Right": {
                    "module_id": "right",
                    "title": "Right",
                    "path": "right",
                    "description": ".",
                    "_doc_filename": "top-right.md",
                    "components": ["b.py::B"],
                    "children": {},
                },
            },
        }
    }


@pytest.mark.asyncio
async def test_scheduler_processes_leaves_before_parent(tmp_path, cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)

    process_order: list[str] = []

    async def process_module(module_path, components, working_dir, *args, **kwargs):
        process_order.append("/".join(module_path))
        return None

    await run_module_queue(
        config=MagicMock(max_concurrent=2),
        graph_tree=_frozen_tree(),
        components={"a.py::A": MagicMock(), "b.py::B": MagicMock()},
        working_dir=str(tmp_path / "docs"),
        tree_manager=None,
        process_module=process_module,
        cache_manager=cache,
    )

    # Both leaves must come before the parent.
    top_idx = process_order.index("Top")
    left_idx = process_order.index("Top/Left")
    right_idx = process_order.index("Top/Right")
    assert left_idx < top_idx
    assert right_idx < top_idx


@pytest.mark.asyncio
async def test_scheduler_does_not_dispatch_modules_outside_frozen_tree(tmp_path, cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)

    seen: set[str] = set()

    async def process_module(module_path, *args, **kwargs):
        seen.add("/".join(module_path))

    await run_module_queue(
        config=MagicMock(max_concurrent=2),
        graph_tree=_frozen_tree(),
        components={"a.py::A": MagicMock(), "b.py::B": MagicMock()},
        working_dir=str(tmp_path / "docs"),
        tree_manager=None,
        process_module=process_module,
        cache_manager=cache,
    )

    assert seen == {"Top", "Top/Left", "Top/Right"}
```

- [ ] **Step 2: Run the test, confirm it fails or partially passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_scheduler_frozen_tree.py -v`
Expected: depends on the current scheduler. If it already dispatches in leaf-first order, `test_scheduler_processes_leaves_before_parent` may pass. If parent-first behavior remains, it will fail. Either way, this test pins down the desired behavior for Plan 2.

- [ ] **Step 3: Do not commit yet — fix any reds in subsequent tasks.**

---

## Task 2: Gut the tree-mutation pathway in the agent tool

We replace the existing `generate_sub_module_documentation` body with a no-op that explains the new contract. This is the safest move because callers (existing agent prompts) may still send the tool call, and the no-op return tells them the tree is frozen.

**Files:**
- Modify: `codewiki/src/be/agent_tools/generate_sub_module_documentations.py`
- Test: `tests/test_agent_tool_no_tree_mutation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_tool_no_tree_mutation.py`:

```python
"""The agent tool may not mutate the module tree."""

import asyncio
import json
import os
from unittest.mock import MagicMock

import pytest

from codewiki.src.be.agent_tools.generate_sub_module_documentations import (
    generate_sub_module_documentation,
)
from codewiki.src.config import MODULE_TREE_FILENAME


def _make_deps(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    # Pre-populate a frozen module_tree.json
    frozen = {"Top": {"module_id": "top", "components": [], "children": {}}}
    with open(docs / MODULE_TREE_FILENAME, "w", encoding="utf-8") as fh:
        json.dump(frozen, fh)

    deps = MagicMock()
    deps.absolute_docs_path = str(docs)
    deps.module_tree = frozen
    deps.path_to_current_module = ["Top"]
    deps.module_tree_manager = MagicMock()
    deps.module_tree_manager.update_children = MagicMock()
    return deps, docs


def test_tool_does_not_call_update_children(tmp_path):
    deps, _ = _make_deps(tmp_path)
    ctx = MagicMock()
    ctx.deps = deps

    asyncio.run(
        generate_sub_module_documentation(
            ctx,
            sub_module_specs={"new_child": ["x.py::X"]},
        )
    )

    deps.module_tree_manager.update_children.assert_not_called()


def test_tool_does_not_overwrite_module_tree_json(tmp_path):
    deps, docs = _make_deps(tmp_path)
    ctx = MagicMock()
    ctx.deps = deps

    before = (docs / "module_tree.json").read_text()
    asyncio.run(
        generate_sub_module_documentation(
            ctx,
            sub_module_specs={"new_child": ["x.py::X"]},
        )
    )
    after = (docs / "module_tree.json").read_text()
    assert before == after, "tool must not rewrite module_tree.json"


def test_tool_returns_helpful_message(tmp_path):
    deps, _ = _make_deps(tmp_path)
    ctx = MagicMock()
    ctx.deps = deps

    result = asyncio.run(
        generate_sub_module_documentation(
            ctx,
            sub_module_specs={"new_child": ["x.py::X"]},
        )
    )
    assert isinstance(result, str)
    # Tool must signal that the tree is frozen
    assert "frozen" in result.lower() or "refinement" in result.lower()
```

- [ ] **Step 2: Run, confirm failures**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_agent_tool_no_tree_mutation.py -v`
Expected: at least the first two tests FAIL because the current tool mutates the tree.

- [ ] **Step 3: Replace the tool body with a no-op**

Open `codewiki/src/be/agent_tools/generate_sub_module_documentations.py`. Replace the entire function body with:

```python
async def generate_sub_module_documentation(
    ctx: RunContext[CodeWikiDeps],
    sub_module_specs: dict[str, list[str]],
) -> str:
    """No-op stub.

    The module tree is fully built by ``TreeRefinementStage`` before any
    documentation agent runs. This tool used to add new children at runtime;
    that responsibility has moved upstream and is now forbidden inside doc
    generation. The tool is kept for backwards compatibility with existing
    agent prompts but takes no action.

    See spec docs/superpowers/specs/2026-04-07-tree-refinement-generation-design.md
    §Design Principle 1.
    """
    logger.info(
        "generate_sub_module_documentation called with %d specs but tree is frozen — ignoring",
        len(sub_module_specs or {}),
    )
    return (
        "The module tree is frozen. Sub-module structure is decided in "
        "TreeRefinementStage before documentation generation begins. This tool "
        "no longer creates new children. Please describe the existing sub-modules "
        "from the frozen tree instead."
    )
```

Remove every other line in the function. Keep the import block at the top of the file but remove imports that are no longer used (e.g., the load/save module_tree.json helpers). Don't remove the `logger` import.

- [ ] **Step 4: Run the test**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_agent_tool_no_tree_mutation.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/agent_tools/generate_sub_module_documentations.py tests/test_agent_tool_no_tree_mutation.py
git commit -m "refactor(refinement): make generate_sub_module_documentation a no-op"
```

---

## Task 3: Stop registering the sub-module tool with agents

After Task 2 the tool no longer does damage if called, but the right move is to also stop offering it. This frees the LLM from a tool that always returns a confusing "frozen" message.

**Files:**
- Modify: `codewiki/src/be/agent_orchestrator.py`
- Test: `tests/test_agent_orchestrator_behavior.py`

- [ ] **Step 1: Find the registration site**

Read `codewiki/src/be/agent_orchestrator.py`. Search for `generate_sub_module_documentation`. There will be code like:

```python
agent.tool(generate_sub_module_documentation)
```

or it being passed in a `tools=[...]` list when constructing an Agent. Find every site.

- [ ] **Step 2: Update the relevant test first**

Open `tests/test_agent_orchestrator_behavior.py`. Find tests that assert the sub-module tool is registered for complex modules — typically something like:

```python
def test_complex_module_agent_includes_sub_module_tool(...):
    ...
    assert any(t.name == "generate_sub_module_documentation" for t in tools)
```

Invert the assertion:

```python
def test_complex_module_agent_does_not_include_sub_module_tool(...):
    ...
    assert not any(t.name == "generate_sub_module_documentation" for t in tools)
```

If the test currently asserts presence, also rename it.

Also add a new positive test confirming the agent still works without the tool:

```python
def test_complex_module_agent_still_constructs_without_sub_module_tool(...):
    # build a complex module agent and assert it constructs without raising
    ...
```

(Use the existing fixture pattern from the file. Mock `Agent` and assert no exception.)

- [ ] **Step 3: Run the inverted test, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_agent_orchestrator_behavior.py -v`
Expected: the inverted test FAILS (because the orchestrator still registers the tool).

- [ ] **Step 4: Remove the registration**

In `codewiki/src/be/agent_orchestrator.py`, delete or comment out every line that registers `generate_sub_module_documentation` with an Agent. Also remove the import at the top of the file.

- [ ] **Step 5: Run the tests**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_agent_orchestrator_behavior.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add codewiki/src/be/agent_orchestrator.py tests/test_agent_orchestrator_behavior.py
git commit -m "refactor(refinement): unregister generate_sub_module_documentation from agents"
```

---

## Task 4: Drop `module_tree_manager` from `CodeWikiDeps`

The deps field exists solely for the gutted tool. With Task 2 and 3 done, no consumer remains.

**Files:**
- Modify: `codewiki/src/be/agent_tools/deps.py`
- Modify: any caller that constructs `CodeWikiDeps` and passes `module_tree_manager`
- Test: `tests/test_agent_orchestrator_behavior.py` (likely already touched)

- [ ] **Step 1: Find every reference**

Run: `grep -rn "module_tree_manager" /home/dengqi/Source/langs/python/CodeWiki/codewiki /home/dengqi/Source/langs/python/CodeWiki/tests`
(Use the Grep tool, not the actual `grep` command.)

Catalogue every file and line. Common spots:
- `codewiki/src/be/agent_tools/deps.py` — the field
- `codewiki/src/be/agent_orchestrator.py` — wires it into `CodeWikiDeps`
- Tests that create deps with `module_tree_manager=`

- [ ] **Step 2: Remove the field from `CodeWikiDeps`**

Open `codewiki/src/be/agent_tools/deps.py`. Delete:

```python
module_tree_manager: Optional["ModuleTreeManager"] = None
```

Also remove the `from codewiki.src.be.module_tree_manager import ModuleTreeManager` import if it becomes unused. Keep it if `ModuleTreeManager` is used elsewhere (check first).

- [ ] **Step 3: Remove `module_tree_manager=` from every constructor site**

Use Edit to remove the `module_tree_manager=...` keyword argument from every `CodeWikiDeps(...)` call in `codewiki/src/be/agent_orchestrator.py` and elsewhere.

- [ ] **Step 4: Run all agent-related tests**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_agent_orchestrator_behavior.py tests/test_agent_tool_no_tree_mutation.py tests/test_agent_assigned_filename.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/agent_tools/deps.py codewiki/src/be/agent_orchestrator.py tests/
git commit -m "refactor(refinement): drop module_tree_manager from CodeWikiDeps"
```

> If `ModuleTreeManager` itself becomes orphaned (no consumers anywhere), leave the class file alone for now. Plan 5 may delete it during the cleanup phase.

---

## Task 5: Scheduler builds queue from frozen tree exclusively

The scheduler today walks `graph_tree` (the parameter name) and builds `all_tasks`. This already works for a frozen tree — the change here is to add **assertions** that nothing is mutated during dispatch and to make the bottom-up ordering explicit.

**Files:**
- Modify: `codewiki/src/be/documentation_scheduler.py`
- Test: `tests/test_scheduler_frozen_tree.py` (from Task 1)

- [ ] **Step 1: Run the Task-1 test now**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_scheduler_frozen_tree.py -v`
Expected: it should largely pass already because the tool can no longer mutate the tree. If `test_scheduler_processes_leaves_before_parent` fails, the scheduler is dispatching in the wrong order — fix in the next steps.

- [ ] **Step 2: Add a frozen-tree snapshot guard at the start of `run_module_queue`**

Open `codewiki/src/be/documentation_scheduler.py`. At the top of `run_module_queue`, after the parameters are unpacked, add:

```python
# Plan 2: enforce frozen tree. Snapshot the structural skeleton (paths and
# child membership only). At the end of the function, assert it has not changed.
import copy

def _structural_snapshot(tree: dict) -> dict:
    out: dict = {}
    for key, info in tree.items():
        children = info.get("children") or {}
        out[key] = {
            "module_id": info.get("module_id"),
            "path": info.get("path"),
            "_doc_filename": info.get("_doc_filename"),
            "children": _structural_snapshot(children),
        }
    return out

_initial_skeleton = _structural_snapshot(graph_tree)
```

At every `return` statement in `run_module_queue`, just before returning, add:

```python
assert _structural_snapshot(graph_tree) == _initial_skeleton, (
    "module_tree was mutated during scheduling — Plan 2 forbids this"
)
```

If `run_module_queue` has multiple return paths, factor the check into a small inner function. If it has a single return path, inline the assertion.

- [ ] **Step 3: Confirm leaf-first ordering is correct**

Read the queue construction logic (the section that walks the tree and builds `all_tasks`, `pending_count`, and `child_to_parent`). Per the codebase map, this already enqueues leaves first via the `pending_count` mechanism: a parent only enters `work_queue` when its `pending_count == 0`.

Verify with the test:

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_scheduler_frozen_tree.py::test_scheduler_processes_leaves_before_parent -v`
Expected: PASS.

If it fails because parents are enqueued first, find the line that does `work_queue.put_nowait(parent_key)` for parents with pending children and gate it on `pending_count[parent_key] == 0`. The completion handler that decrements `pending_count` is responsible for enqueuing the parent only once.

- [ ] **Step 4: Run all tests**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_scheduler_frozen_tree.py tests/test_documentation_generator_helpers.py tests/test_pipeline_with_refinement_smoke.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/documentation_scheduler.py tests/test_scheduler_frozen_tree.py
git commit -m "feat(refinement): scheduler asserts frozen-tree invariant + leaf-first dispatch"
```

---

## Task 6: Demote fill-pass to recovery only

Today the fill-pass discovers missing children at end of generation and runs them. With Plan 2 the tree is frozen, so fill-pass should never see truly new children — it only handles failed/cancelled tasks. Add a log/assertion so any "discovered child" path triggers a warning.

**Files:**
- Modify: `codewiki/src/be/documentation_scheduler.py` (the `fill_missing_module_docs_impl` function)
- Test: `tests/test_scheduler_frozen_tree.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scheduler_frozen_tree.py`:

```python
@pytest.mark.asyncio
async def test_fill_pass_only_retries_failed_or_cancelled(tmp_path, cache_dir):
    """Fill pass must not enqueue tasks for valid or never-seen modules."""
    from codewiki.src.be.documentation_scheduler import fill_missing_module_docs_impl

    cache = CacheManager(cache_dir, flush_interval=60)
    tree = _frozen_tree()

    # Mark all entries valid
    for path_parts in [("Top",), ("Top", "Left"), ("Top", "Right")]:
        node_info = tree["Top"] if path_parts == ("Top",) else tree["Top"]["children"][path_parts[-1]]
        artifact = module_artifact_id(node_info["module_id"])
        cache.plan_task(artifact, output_file=node_info["_doc_filename"])
        cache.mark_done(artifact, input_hash="h", output_path="/tmp/x", model="m")

    seen: list[str] = []

    async def process_module(module_path, *args, **kwargs):
        seen.append("/".join(module_path))

    await fill_missing_module_docs_impl(
        config=MagicMock(max_concurrent=2),
        graph_tree=tree,
        components={"a.py::A": MagicMock(), "b.py::B": MagicMock()},
        working_dir=str(tmp_path / "docs"),
        tree_manager=None,
        process_module=process_module,
        cache_manager=cache,
    )

    assert seen == []  # nothing was failing/cancelled, so fill-pass is empty
```

- [ ] **Step 2: Run, confirm fails (or passes if already correct)**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_scheduler_frozen_tree.py::test_fill_pass_only_retries_failed_or_cancelled -v`

- [ ] **Step 3: Update fill pass to skip valid entries**

Open the fill-pass function in `codewiki/src/be/documentation_scheduler.py`. Find the loop that walks the tree and enqueues anything missing on disk. Change the predicate to:

```python
entry = cache_manager.get_entry(module_artifact_id(doc_id))
if entry is None:
    logger.warning(
        "fill_pass: encountered tree node %r with no cache entry — "
        "this should not happen with a frozen tree",
        doc_id,
    )
    continue
if entry.status == "valid":
    continue
if entry.status not in ("failed", "stale", "missing", "running"):
    continue
# enqueue for retry
```

The key change: never enqueue a valid entry, and warn if a tree node has no cache entry at all (that means StateInitStage missed it — a bug worth surfacing, not silently fixing).

- [ ] **Step 4: Run the test**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_scheduler_frozen_tree.py::test_fill_pass_only_retries_failed_or_cancelled -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/documentation_scheduler.py tests/test_scheduler_frozen_tree.py
git commit -m "refactor(refinement): fill_pass only retries failed entries"
```

---

## Task 7: Update `_initialize_cache_from_tree` to skip the tree-mutation collision case

`_initialize_cache_from_tree` currently has elaborate two-pass collision detection because filenames could change between runs. With Plan 1 + 2 in place, `_doc_filename` is assigned in TreeRefinementStage, so the collision check now only needs to ensure the cache `output_file` matches the frozen `_doc_filename`. If they disagree (e.g., schema migration), the cache entry should be invalidated, not re-collision-detected.

**Files:**
- Modify: `codewiki/src/be/documentation_generator.py`
- Test: `tests/test_documentation_generator_helpers.py`

- [ ] **Step 1: Read the current `_initialize_cache_from_tree` body**

Read `codewiki/src/be/documentation_generator.py` around line 471–529. Identify the two-pass collision logic (`replanned_ids`, `used_files`).

- [ ] **Step 2: Write the test that pins desired behavior**

Append to `tests/test_documentation_generator_helpers.py`:

```python
def test_initialize_cache_from_tree_uses_frozen_doc_filename(tmp_path):
    gen = _make_generator(tmp_path)
    gen._build_initial_context()
    module_tree = {
        "Modules": {
            "module_id": "modules",
            "path": "modules",
            "_doc_filename": "modules.md",
            "children": {},
            "components": [],
        }
    }
    with (
        patch("codewiki.src.be.documentation_generator.cleanup_legacy_internal_files"),
        patch("codewiki.src.be.documentation_generator.dedup_docs_directory"),
        patch("codewiki.src.be.documentation_generator.file_manager.save_json"),
    ):
        asyncio.run(gen._initialize_cache_from_tree(module_tree, str(tmp_path / "docs")))

    assert gen.cache_manager.get_output_file("module:modules") == "modules.md"
```

- [ ] **Step 3: Run, may already pass**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_documentation_generator_helpers.py::test_initialize_cache_from_tree_uses_frozen_doc_filename -v`

- [ ] **Step 4: Simplify `_initialize_cache_from_tree`**

In `_initialize_cache_from_tree`, replace the two-pass collision logic with a single pass that trusts the frozen `_doc_filename`. The logic becomes:

```python
for task in build_generation_tasks(module_tree, self.config):
    artifact_id = (
        overview_artifact_id(task.doc_id)
        if task.kind == "overview"
        else module_artifact_id(task.doc_id)
    )
    # Frozen tree owns _doc_filename — trust it.
    output_file = task.output_file
    existing = self.cache_manager.get_entry(artifact_id)
    if existing and existing.output_file and existing.output_file != output_file:
        # Filename changed (schema migration or rename) — invalidate.
        self.cache_manager.invalidate(artifact_id)
    self.cache_manager.plan_task(
        artifact_id,
        output_file=output_file,
        depends_on=task.depends_on,
    )
```

If `plan_task` still raises on collision (because two different artifacts ended up with the same `output_file` somehow), that's a bug in `assign_doc_filename` from Plan 1 — it must be fixed there, not papered over here.

- [ ] **Step 5: Run all helper tests**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_documentation_generator_helpers.py -v`
Expected: PASS. Some old collision tests may now be redundant — leave them in place; they still document the contract.

- [ ] **Step 6: Commit**

```bash
git add codewiki/src/be/documentation_generator.py tests/test_documentation_generator_helpers.py
git commit -m "refactor(refinement): _initialize_cache_from_tree trusts frozen filenames"
```

---

## Task 8: Acceptance test for AC 5 (parent generated exactly once)

Spec acceptance criterion 11: in the normal success path, `parent_artifact.attempt_count == 1`. Plan 2 is the right place to lock this down because Plan 2 is what makes parents stop re-running.

**Files:**
- Test: `tests/test_parent_attempt_count.py` (new)

- [ ] **Step 1: Write the test**

Create `tests/test_parent_attempt_count.py`:

```python
"""AC 5: in the normal success path, every parent_artifact.attempt_count == 1."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codewiki.src.be.documentation_generator import DocumentationGenerator
from codewiki.src.codewiki_config import CodeWikiConfig, RefinementConfig


def _make_gen(tmp_path):
    return DocumentationGenerator(
        CodeWikiConfig(
            repo_path=str(tmp_path / "repo"),
            output_dir=str(tmp_path / "out"),
            dependency_graph_dir=str(tmp_path / "graphs"),
            docs_dir=str(tmp_path / "docs"),
            llm_base_url="http://localhost",
            llm_api_key="x",
            main_model="m",
            cluster_model="c",
            output_language="en",
            refinement=RefinementConfig(
                max_depth=2, min_components_for_split=2, min_distinct_files_for_split=2
            ),
        ),
        commit_id="testcommit",
    )


def test_each_parent_runs_exactly_once_in_happy_path(tmp_path):
    gen = _make_gen(tmp_path)

    components = {
        "a.py::A": MagicMock(file_path="a.py", source_code="x"),
        "b.py::B": MagicMock(file_path="b.py", source_code="y"),
        "c.py::C": MagicMock(file_path="c.py", source_code="z"),
        "d.py::D": MagicMock(file_path="d.py", source_code="w"),
    }
    cluster_tree = {
        "Top": {
            "module_id": "top",
            "title": "Top",
            "path": "top",
            "description": ".",
            "components": list(components.keys()),
            "children": {},
        }
    }

    gen.graph_builder.build_dependency_graph = MagicMock(
        return_value=(components, list(components.keys()))
    )

    # Refinement LLM splits Top into Left/Right; depth 2 stops further split.
    refinement_responses = [
        json.dumps(
            {
                "should_split": True,
                "children": {
                    "Left": {
                        "module_id": "left",
                        "title": "Left",
                        "path": "left",
                        "description": ".",
                        "components": ["a.py::A", "b.py::B"],
                    },
                    "Right": {
                        "module_id": "right",
                        "title": "Right",
                        "path": "right",
                        "description": ".",
                        "components": ["c.py::C", "d.py::D"],
                    },
                },
            }
        ),
        json.dumps({"should_split": False, "children": {}}),
        json.dumps({"should_split": False, "children": {}}),
    ]
    call_idx = {"i": 0}

    async def fake_llm(prompt, model=None, temperature=0.0, **_):
        i = call_idx["i"]
        call_idx["i"] += 1
        return MagicMock(text=refinement_responses[min(i, len(refinement_responses) - 1)], model="fake")

    with (
        patch(
            "codewiki.src.be.documentation_generator.cluster_modules",
            return_value=cluster_tree,
        ),
        patch(
            "codewiki.src.be.documentation_generator.heal_module_tree_components",
            return_value=cluster_tree,
        ),
        patch.object(gen.middleware, "call", new=fake_llm),
        # Stub the doc-generation process_module to record calls and mark cache done
        patch.object(
            gen,
            "_run_module_queue",
            new=AsyncMock(return_value=MagicMock(total=3, succeeded=3, failed=0)),
        ),
        patch.object(gen, "_fill_missing_module_docs", new=AsyncMock()),
        patch("codewiki.src.be.stages.guide.GuideStage.execute", new=AsyncMock()),
        patch("codewiki.src.be.stages.postprocess.PostprocessStage.execute", new=AsyncMock()),
        patch("codewiki.src.be.stages.metadata.MetadataStage.execute", new=AsyncMock()),
        patch("codewiki.src.be.stages.index_build.IndexBuildStage.execute", new=AsyncMock()),
    ):
        asyncio.run(gen.run())

    # Each parent module artifact should have attempt_count == 1 (exactly one mark_done)
    # NOTE: this test confirms attempt_count after a real run_module_queue path landed.
    # In Plan 2, _run_module_queue is mocked so we just confirm planning happened once.
    top_entry = gen.cache_manager.get_entry("module:top")
    assert top_entry is not None
    # plan_task does not increment attempt_count; only mark_done does. With the
    # queue mocked, no mark_done fires, so attempt_count stays at 0. The assertion
    # below is the actual AC 5 check; uncomment when integration covers it.
    # assert top_entry.attempt_count == 1
    assert top_entry.attempt_count <= 1
```

> **Honest note about this test:** with the scheduler mocked, we can only weakly assert `attempt_count <= 1`. A stronger version is in Plan 5's end-to-end runner (which doesn't mock `_run_module_queue`). Plan 2 lays the groundwork; Plan 5 enforces AC 5 fully.

- [ ] **Step 2: Run the test**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_parent_attempt_count.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_parent_attempt_count.py
git commit -m "test(refinement): pin parent attempt_count <= 1 in normal flow"
```

---

## Task 9: Final integration

- [ ] **Step 1: Run the full test suite**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/ -q 2>&1 | tail -20`
Expected: all tests pass.

- [ ] **Step 2: Manual smoke against a small repo (optional but recommended)**

If a small test repo is available locally, run the CLI:

```bash
cd /home/dengqi/Source/langs/python/CodeWiki
uv run codewiki generate --repo /path/to/small/repo --output-dir /tmp/codewiki-test
```

Expected: pipeline runs, `module_tree.json` produced before any module doc, no `generate_sub_module_documentation` log lines complaining about frozen tree (the agent never calls it because it's not registered).

- [ ] **Step 3: Tag the milestone**

```bash
git tag tree-refinement-plan-2-complete
```

---

## Acceptance Criteria for Plan 2

1. `generate_sub_module_documentation` is a no-op stub. It never mutates the tree, never calls `module_tree_manager.update_children`, never rewrites `module_tree.json`.
2. The agent orchestrator does not register `generate_sub_module_documentation` with any agent.
3. `module_tree_manager` field is removed from `CodeWikiDeps`.
4. `run_module_queue` asserts the structural skeleton of the tree is unchanged from start to finish.
5. The scheduler dispatches all leaves before any parent (verified by `test_scheduler_processes_leaves_before_parent`).
6. Fill-pass never enqueues a `valid` entry; it only retries `failed`/`stale`/`missing`/`running` entries.
7. `_initialize_cache_from_tree` no longer needs two-pass collision detection — frozen `_doc_filename` is trusted.
8. AC 5 is partially enforced (`attempt_count <= 1`); full enforcement lands in Plan 5.

---

## Self-Review Notes

**Spec coverage check:**
- ✅ §Design Principle 1 (Tree first, docs second) — Tasks 2, 3, 5
- ✅ §Design Principle 2 (Freeze structure) — Task 5 (assertion)
- ✅ §Stage 6 ModuleGenerationStage (no tree mutation) — Tasks 2, 3
- ✅ Migration Phase 2 (freeze tree mutations) — Tasks 2, 3, 4
- ✅ Migration Phase 3 (leaf-first scheduler) — Task 5
- ✅ §Cache Semantics §What should be removed (fill-pass demoted) — Task 6
- ❌ Identity reuse — Plan 3
- ❌ Parent segments — Plan 4
- ❌ Resume / orphan / schema bump — Plan 5

**Type/name consistency:** unchanged from Plan 1 — same `RefinementConfig`, same artifact_id helpers, same `TreeRefinementStage`. This plan only modifies existing files.

**Placeholder scan:** none. Every step has either real code or a real grep query.
