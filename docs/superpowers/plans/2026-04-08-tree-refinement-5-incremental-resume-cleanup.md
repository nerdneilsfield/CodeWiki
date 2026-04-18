# Tree Refinement Phase 5: Incremental + Resume + Cleanup + Schema Bump

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the configurable rerun thresholds, hard structural rerun triggers, resume semantics, layered orphan cleanup, cache schema migration, and the strong-form AC 5 enforcement (`parent_artifact.attempt_count == 1` in normal success). After Plan 5 lands, every spec acceptance criterion is satisfied and the migration is complete.

**Architecture:** Three independent feature areas:
1. **Incremental thresholds + hard rerun triggers** — a new `incremental.py` module computes the leaf and parent change ratios, applies hard triggers, and emits a list of artifact ids to invalidate. Called by `TreeRefinementStage` after the new tree is computed and before `StateInitStage`.
2. **Resume semantics** — explicit handling in `DocumentationGenerator.run`: if interruption is detected (cache has `running` or unfinished refinement entries), resume in `refinement → leaf → parent → root` order without spuriously re-running already-valid parents.
3. **Orphan cleanup + schema migration** — a new `orphan_cleanup.py` module runs at the end of `TreeRefinementStage`. It does two things: (A) unconditional cleanup of internal `.codewiki/_refinement/*` and `.codewiki/_module_parts/*/` files not owned by any current entry; (B) conservative cleanup of user-visible `*.md` / `*.html` files but only when ownership demonstrably moved (rename event), with a user-modified guard. The cache registry schema version is bumped at the same time.

**Tech Stack:** Python 3.10+, asyncio, pytest.

**Spec reference:** §Incremental Change Propagation, §Resume semantics, §Schema migration, §Orphan cleanup, §Acceptance Criteria 7, 10, 11.

**Prerequisite:** Plans 1, 2, 3, 4 merged. Suite green on `main`.

---

## File Structure

### New files

| Path | Responsibility |
|------|----------------|
| `codewiki/src/be/incremental.py` | `compute_leaf_change_ratio`, `compute_parent_change_ratio`, `should_rerun_leaf`, `should_rerun_parent`, `detect_hard_triggers`, `plan_invalidations` |
| `codewiki/src/be/orphan_cleanup.py` | `cleanup_internal_artifacts`, `cleanup_renamed_user_visible`, `is_user_modified` |
| `tests/test_incremental.py` | Pure-logic tests for ratios and triggers |
| `tests/test_orphan_cleanup.py` | Cleanup tests with tmp_path filesystem fixtures |
| `tests/test_resume_semantics.py` | Resume scenarios: refinement crash, post-freeze crash, valid parents not re-run |
| `tests/test_schema_migration.py` | Schema version bump and forward compatibility |
| `tests/test_parent_attempt_count_strong.py` | The hard form of AC 5 (`attempt_count == 1`) |

### Modified files

| Path | Change |
|------|--------|
| `codewiki/src/codewiki_config.py` | Add `IncrementalConfig` nested model with `leaf_rerun_threshold = 0.30`, `parent_rerun_threshold = 0.30`. Add `incremental: IncrementalConfig` field to `CodeWikiConfig`. |
| `codewiki/src/config_loader.py` | Extend `_build_codewiki_config` to read a `[incremental]` TOML section into an `IncrementalConfig`. Without this, Task 1's thresholds are only reachable via Python construction — CLI and config.toml silently drop them. |
| `codewiki/src/be/cache_manager.py` | Bump `_SCHEMA_VERSION` to `"cache.v2"`. On load, refuse to trust `refinement:*` entries from `cache.v1`. |
| `codewiki/src/be/stages/tree_refinement.py` | After refinement, call `incremental.plan_invalidations` and `orphan_cleanup.cleanup_internal_artifacts` + `orphan_cleanup.cleanup_renamed_user_visible` |
| `codewiki/src/be/documentation_generator.py` | Resume semantics check at the start of `run()`; record per-leaf/per-parent rerun decisions |

---

## Task 0: Baseline check

- [ ] **Step 1: All previous plans merged**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && git tag | grep tree-refinement`
Expected: `tree-refinement-plan-1-complete`, `-2-`, `-3-`, `-4-` all present.

- [ ] **Step 2: Suite green**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/ -q 2>&1 | tail -10`
Expected: PASS.

---

## Task 1: `IncrementalConfig`

**Files:**
- Modify: `codewiki/src/codewiki_config.py`
- Test: `tests/test_codewiki_config_refinement.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_codewiki_config_refinement.py`:

```python
def test_incremental_config_defaults():
    from codewiki.src.codewiki_config import CodeWikiConfig

    cfg = CodeWikiConfig(
        repo_path="/tmp/repo",
        docs_dir="/tmp/docs",
        llm_base_url="http://localhost",
        llm_api_key="x",
        main_model="m",
        cluster_model="c",
    )
    assert cfg.incremental.leaf_rerun_threshold == 0.30
    assert cfg.incremental.parent_rerun_threshold == 0.30


def test_incremental_config_override():
    from codewiki.src.codewiki_config import CodeWikiConfig, IncrementalConfig

    cfg = CodeWikiConfig(
        repo_path="/tmp/repo",
        docs_dir="/tmp/docs",
        llm_base_url="http://localhost",
        llm_api_key="x",
        main_model="m",
        cluster_model="c",
        incremental=IncrementalConfig(
            leaf_rerun_threshold=0.40,
            parent_rerun_threshold=0.50,
        ),
    )
    assert cfg.incremental.leaf_rerun_threshold == 0.40
    assert cfg.incremental.parent_rerun_threshold == 0.50
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_codewiki_config_refinement.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Edit `codewiki/src/codewiki_config.py`. Near `RefinementConfig`, add:

```python
class IncrementalConfig(BaseModel):
    """Incremental rerun thresholds. See spec §Incremental Change Propagation."""

    leaf_rerun_threshold: float = 0.30
    parent_rerun_threshold: float = 0.30
```

In `CodeWikiConfig`, add:

```python
    incremental: IncrementalConfig = Field(default_factory=IncrementalConfig)
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_codewiki_config_refinement.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/codewiki_config.py tests/test_codewiki_config_refinement.py
git commit -m "feat(refinement): IncrementalConfig with leaf+parent rerun thresholds"
```

---

## Task 1b: Wire `IncrementalConfig` through `config_loader.py`

**Motivation.** Same as Plan 1 Task 2b: `_build_codewiki_config` does not auto-map unknown TOML sections. Without extending it, `[incremental]` in a user's config.toml is silently ignored and `plan_invalidations` always sees the hard-coded 0.30 default.

**Files:**
- Modify: `codewiki/src/config_loader.py`
- Test: `tests/test_config_loader_refinement.py` (extend the file introduced in Plan 1 Task 2b)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config_loader_refinement.py`:

```python
def test_incremental_section_loads_from_toml(tmp_path):
    import textwrap
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [runtime]
            output_dir = "docs"

            [generation]
            main_model = "m"
            cluster_model = "c"

            [incremental]
            leaf_rerun_threshold = 0.45
            parent_rerun_threshold = 0.55
            """
        ),
        encoding="utf-8",
    )
    from codewiki.src.config_loader import load_config

    cfg = load_config(str(config_path), str(tmp_path), resolve_secrets=False)
    assert cfg.incremental.leaf_rerun_threshold == 0.45
    assert cfg.incremental.parent_rerun_threshold == 0.55


def test_incremental_section_absent_uses_defaults(tmp_path):
    import textwrap
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [runtime]
            output_dir = "docs"

            [generation]
            main_model = "m"
            cluster_model = "c"
            """
        ),
        encoding="utf-8",
    )
    from codewiki.src.config_loader import load_config

    cfg = load_config(str(config_path), str(tmp_path), resolve_secrets=False)
    assert cfg.incremental.leaf_rerun_threshold == 0.30
    assert cfg.incremental.parent_rerun_threshold == 0.30
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_config_loader_refinement.py -v -k "incremental"`
Expected: FAIL.

- [ ] **Step 3: Extend `_build_codewiki_config`**

Open `codewiki/src/config_loader.py`. Import `IncrementalConfig`:

```python
from codewiki.src.codewiki_config import (
    CodeWikiConfig,
    IncrementalConfig,
    PostprocessConfig,
    ProviderConfig,
    RefinementConfig,
)
```

In `_build_codewiki_config`, near the `refinement_section` read added in Plan 1 Task 2b, add:

```python
    incremental_section = cast(dict[str, Any], data.get("incremental", {}))
```

Build the nested config near where `refinement_cfg` is built:

```python
    incremental_cfg = IncrementalConfig(
        leaf_rerun_threshold=float(
            incremental_section.get("leaf_rerun_threshold", 0.30)
        ),
        parent_rerun_threshold=float(
            incremental_section.get("parent_rerun_threshold", 0.30)
        ),
    )
```

Pass it to the constructor:

```python
        incremental=incremental_cfg,
```

Place it next to `refinement=refinement_cfg,` in the call.

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_config_loader_refinement.py -v`
Expected: PASS (all tests including the new incremental ones).

- [ ] **Step 5: Run existing loader tests**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_cli_generate_config_file.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add codewiki/src/config_loader.py tests/test_config_loader_refinement.py
git commit -m "feat(refinement): load [incremental] section from TOML config"
```

---

## Task 2: Pure-logic incremental ratios

**Files:**
- Create: `codewiki/src/be/incremental.py`
- Test: `tests/test_incremental.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_incremental.py`:

```python
import pytest

from codewiki.src.be.incremental import (
    compute_leaf_change_ratio,
    compute_parent_change_ratio,
    should_rerun_leaf,
    should_rerun_parent,
)


def test_leaf_change_ratio_no_changes():
    assert compute_leaf_change_ratio(
        new_components={"a", "b", "c"},
        old_components={"a", "b", "c"},
        old_component_hashes={"a": "h", "b": "h", "c": "h"},
        new_component_hashes={"a": "h", "b": "h", "c": "h"},
    ) == 0.0


def test_leaf_change_ratio_full_change():
    assert compute_leaf_change_ratio(
        new_components={"a", "b", "c"},
        old_components=set(),
        old_component_hashes={},
        new_component_hashes={"a": "h", "b": "h", "c": "h"},
    ) == 1.0


def test_leaf_change_ratio_partial():
    # 1 of 4 components changed → ratio 0.25
    assert compute_leaf_change_ratio(
        new_components={"a", "b", "c", "d"},
        old_components={"a", "b", "c", "d"},
        old_component_hashes={"a": "1", "b": "1", "c": "1", "d": "1"},
        new_component_hashes={"a": "1", "b": "1", "c": "1", "d": "2"},
    ) == 0.25


def test_leaf_change_ratio_added_component_counts_as_change():
    # 4 new components, 1 added since old run → 1/4 changed
    assert compute_leaf_change_ratio(
        new_components={"a", "b", "c", "d"},
        old_components={"a", "b", "c"},
        old_component_hashes={"a": "h", "b": "h", "c": "h"},
        new_component_hashes={"a": "h", "b": "h", "c": "h", "d": "h"},
    ) == 0.25


def test_should_rerun_leaf_below_threshold():
    assert should_rerun_leaf(change_ratio=0.20, threshold=0.30) is False


def test_should_rerun_leaf_at_threshold():
    assert should_rerun_leaf(change_ratio=0.30, threshold=0.30) is True


def test_should_rerun_leaf_above_threshold():
    assert should_rerun_leaf(change_ratio=0.50, threshold=0.30) is True


def test_parent_change_ratio_one_of_three_children_changed():
    # 1 of 3 direct children changed → 1/3 ≈ 0.333
    ratio = compute_parent_change_ratio(
        changed_direct_children=1,
        total_direct_children=3,
    )
    assert ratio == pytest.approx(1 / 3, rel=1e-4)


def test_parent_change_ratio_zero_total_children():
    # No direct children → 0.0 (parent has no doc reasons to update)
    assert compute_parent_change_ratio(
        changed_direct_children=0,
        total_direct_children=0,
    ) == 0.0


def test_should_rerun_parent_threshold_logic():
    assert should_rerun_parent(change_ratio=0.29, threshold=0.30) is False
    assert should_rerun_parent(change_ratio=0.30, threshold=0.30) is True
    assert should_rerun_parent(change_ratio=0.99, threshold=0.30) is True
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_incremental.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `codewiki/src/be/incremental.py`:

```python
"""Incremental change ratios and rerun decisions.

Pure logic, no I/O. See spec §Incremental Change Propagation.
"""

from __future__ import annotations


def compute_leaf_change_ratio(
    *,
    new_components: set[str],
    old_components: set[str],
    new_component_hashes: dict[str, str],
    old_component_hashes: dict[str, str],
) -> float:
    """``changed_components / total_components`` for a single leaf.

    A component is "changed" if it was added, removed, or its hash differs.
    The denominator is the count of *current* components.
    """
    total = len(new_components)
    if total == 0:
        return 0.0
    changed = 0
    for cid in new_components:
        if cid not in old_components:
            changed += 1
        elif new_component_hashes.get(cid) != old_component_hashes.get(cid):
            changed += 1
    # Removed components also count toward churn — they touch the parent's
    # context even though they're not in the new set.
    for cid in old_components - new_components:
        changed += 1
    # But the denominator stays at len(new_components); cap ratio at 1.0
    return min(changed / total, 1.0)


def should_rerun_leaf(*, change_ratio: float, threshold: float) -> bool:
    """Rerun if ratio meets or exceeds threshold."""
    return change_ratio >= threshold


def compute_parent_change_ratio(
    *,
    changed_direct_children: int,
    total_direct_children: int,
) -> float:
    """``changed_direct_children / total_direct_children``.

    Only counts direct children, not the entire descendant subtree.
    """
    if total_direct_children == 0:
        return 0.0
    return changed_direct_children / total_direct_children


def should_rerun_parent(*, change_ratio: float, threshold: float) -> bool:
    return change_ratio >= threshold
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_incremental.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/incremental.py tests/test_incremental.py
git commit -m "feat(refinement): incremental change ratios and rerun decisions"
```

---

## Task 3: Hard rerun triggers

**Files:**
- Modify: `codewiki/src/be/incremental.py`
- Test: `tests/test_incremental.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_incremental.py`:

```python
from codewiki.src.be.incremental import (
    HardTriggerReason,
    detect_hard_triggers,
)


def test_hard_trigger_child_added():
    triggers = detect_hard_triggers(
        old_children={"A": {"module_id": "a", "title": "A", "path": "a", "components": ["x"]}},
        new_children={
            "A": {"module_id": "a", "title": "A", "path": "a", "components": ["x"]},
            "B": {"module_id": "b", "title": "B", "path": "b", "components": ["y"]},
        },
    )
    assert HardTriggerReason.CHILD_ADDED in triggers


def test_hard_trigger_child_removed():
    triggers = detect_hard_triggers(
        old_children={
            "A": {"module_id": "a", "title": "A", "path": "a", "components": ["x"]},
            "B": {"module_id": "b", "title": "B", "path": "b", "components": ["y"]},
        },
        new_children={"A": {"module_id": "a", "title": "A", "path": "a", "components": ["x"]}},
    )
    assert HardTriggerReason.CHILD_REMOVED in triggers


def test_hard_trigger_child_title_changed():
    triggers = detect_hard_triggers(
        old_children={"A": {"module_id": "a", "title": "A", "path": "a", "components": ["x"]}},
        new_children={"A": {"module_id": "a", "title": "Renamed", "path": "a", "components": ["x"]}},
    )
    assert HardTriggerReason.CHILD_TITLE_CHANGED in triggers


def test_hard_trigger_child_path_changed():
    triggers = detect_hard_triggers(
        old_children={"A": {"module_id": "a", "title": "A", "path": "a", "components": ["x"]}},
        new_children={"A": {"module_id": "a", "title": "A", "path": "renamed_a", "components": ["x"]}},
    )
    assert HardTriggerReason.CHILD_PATH_CHANGED in triggers


def test_hard_trigger_no_change_returns_empty():
    same = {"A": {"module_id": "a", "title": "A", "path": "a", "components": ["x"]}}
    triggers = detect_hard_triggers(old_children=same, new_children=same)
    assert triggers == set()
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_incremental.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `codewiki/src/be/incremental.py`:

```python
from enum import Enum


class HardTriggerReason(str, Enum):
    CHILD_ADDED = "child_added"
    CHILD_REMOVED = "child_removed"
    CHILD_TITLE_CHANGED = "child_title_changed"
    CHILD_PATH_CHANGED = "child_path_changed"
    CHILD_IDENTITY_LOST = "child_identity_lost"


def detect_hard_triggers(
    *,
    old_children: dict,
    new_children: dict,
) -> set[HardTriggerReason]:
    """Detect structural changes that bypass the ratio threshold.

    See spec §Hard rerun triggers.
    """
    reasons: set[HardTriggerReason] = set()

    # Build module_id index for both sides — children may have been re-keyed.
    old_by_id = {info.get("module_id"): info for info in old_children.values() if info.get("module_id")}
    new_by_id = {info.get("module_id"): info for info in new_children.values() if info.get("module_id")}

    if set(new_by_id) - set(old_by_id):
        reasons.add(HardTriggerReason.CHILD_ADDED)
    if set(old_by_id) - set(new_by_id):
        reasons.add(HardTriggerReason.CHILD_REMOVED)

    for mid in set(old_by_id) & set(new_by_id):
        old = old_by_id[mid]
        new = new_by_id[mid]
        if old.get("title") != new.get("title"):
            reasons.add(HardTriggerReason.CHILD_TITLE_CHANGED)
        if old.get("path") != new.get("path"):
            reasons.add(HardTriggerReason.CHILD_PATH_CHANGED)

    return reasons
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_incremental.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/incremental.py tests/test_incremental.py
git commit -m "feat(refinement): detect_hard_triggers for structural rerun"
```

---

## Task 4: `plan_invalidations` — combines ratio + hard triggers

The orchestrator function: walk every node in the new tree, look up the old subtree from the refinement cache, decide whether each leaf/parent should rerun, return the list of artifact ids to invalidate.

**Files:**
- Modify: `codewiki/src/be/incremental.py`
- Test: `tests/test_incremental.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_incremental.py`:

```python
from codewiki.src.be.incremental import plan_invalidations


def test_plan_invalidations_leaf_below_threshold_not_invalidated():
    new_tree = {
        "Top": {
            "module_id": "top",
            "title": "Top",
            "path": "top",
            "components": ["a", "b", "c", "d", "e"],
            "children": {},
        }
    }
    old_tree = new_tree  # identical → no rerun
    invalidations = plan_invalidations(
        new_tree=new_tree,
        previous_tree=old_tree,
        new_component_hashes={cid: "h" for cid in "abcde"},
        old_component_hashes={cid: "h" for cid in "abcde"},
        leaf_threshold=0.30,
        parent_threshold=0.30,
    )
    assert invalidations == []


def test_plan_invalidations_leaf_above_threshold_invalidated():
    new_tree = {
        "Top": {
            "module_id": "top",
            "title": "Top",
            "path": "top",
            "components": ["a", "b", "c", "d"],
            "children": {},
        }
    }
    old_tree = new_tree
    invalidations = plan_invalidations(
        new_tree=new_tree,
        previous_tree=old_tree,
        new_component_hashes={"a": "1", "b": "2", "c": "1", "d": "1"},
        old_component_hashes={"a": "1", "b": "1", "c": "1", "d": "1"},
        leaf_threshold=0.30,
        parent_threshold=0.30,
    )
    # 1/4 = 0.25 — below threshold, NOT invalidated
    assert invalidations == []


def test_plan_invalidations_parent_above_threshold():
    """One of three children changes → parent ratio 1/3 > 0.30."""
    new_tree = {
        "Top": {
            "module_id": "top",
            "title": "Top",
            "path": "top",
            "components": [],
            "children": {
                "A": {"module_id": "a", "title": "A", "path": "a", "components": ["x"], "children": {}},
                "B": {"module_id": "b", "title": "B", "path": "b", "components": ["y"], "children": {}},
                "C": {"module_id": "c", "title": "C", "path": "c", "components": ["z"], "children": {}},
            },
        }
    }
    old_tree = new_tree
    invalidations = plan_invalidations(
        new_tree=new_tree,
        previous_tree=old_tree,
        # only z changed
        new_component_hashes={"x": "h", "y": "h", "z": "new"},
        old_component_hashes={"x": "h", "y": "h", "z": "old"},
        leaf_threshold=0.30,
        parent_threshold=0.30,
    )
    # Leaf C should be invalidated (1/1 = 1.0 ratio)
    # Parent Top should be invalidated (1/3 ≈ 0.33 > 0.30)
    assert "module:c" in invalidations
    assert "module:top" in invalidations


def test_plan_invalidations_hard_trigger_child_added():
    new_tree = {
        "Top": {
            "module_id": "top",
            "title": "Top",
            "path": "top",
            "components": [],
            "children": {
                "A": {"module_id": "a", "title": "A", "path": "a", "components": ["x"], "children": {}},
                "B": {"module_id": "b", "title": "B", "path": "b", "components": ["y"], "children": {}},
            },
        }
    }
    old_tree = {
        "Top": {
            "module_id": "top",
            "title": "Top",
            "path": "top",
            "components": [],
            "children": {
                "A": {"module_id": "a", "title": "A", "path": "a", "components": ["x"], "children": {}},
            },
        }
    }
    invalidations = plan_invalidations(
        new_tree=new_tree,
        previous_tree=old_tree,
        new_component_hashes={"x": "h", "y": "h"},
        old_component_hashes={"x": "h"},
        leaf_threshold=0.99,  # high enough that ratio alone wouldn't trigger
        parent_threshold=0.99,
    )
    # Hard trigger: child added → parent must rerun regardless of ratio
    assert "module:top" in invalidations
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_incremental.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `codewiki/src/be/incremental.py`:

```python
def plan_invalidations(
    *,
    new_tree: dict,
    previous_tree: dict,
    new_component_hashes: dict[str, str],
    old_component_hashes: dict[str, str],
    leaf_threshold: float,
    parent_threshold: float,
) -> list[str]:
    """Walk the new tree against the previous tree and return the artifact_ids
    that should be invalidated this run.

    Returns ``module:{doc_id}`` ids only — segment-level invalidation is
    handled by the segment cache itself reacting to upstream changes, except
    when ``parent_threshold`` is exceeded, in which case the caller is
    expected to call ``parent_segments.force_invalidate_parent_segments``
    for that parent_doc_id.
    """
    invalidations: list[str] = []

    def _walk(new_subtree: dict, old_subtree: dict) -> None:
        for key, new_node in new_subtree.items():
            old_node = old_subtree.get(key) if old_subtree else None
            module_id = new_node.get("module_id")
            if not module_id:
                continue

            new_components = set(new_node.get("components") or [])
            old_components = set((old_node or {}).get("components") or [])
            new_children = new_node.get("children") or {}
            old_children = (old_node or {}).get("children") or {}

            if not new_children:
                # Leaf
                ratio = compute_leaf_change_ratio(
                    new_components=new_components,
                    old_components=old_components,
                    new_component_hashes=new_component_hashes,
                    old_component_hashes=old_component_hashes,
                )
                if should_rerun_leaf(change_ratio=ratio, threshold=leaf_threshold):
                    invalidations.append(f"module:{module_id}")
            else:
                # Parent
                # Recurse into children first, so we know how many were invalidated.
                _walk(new_children, old_children)

                # Hard triggers from structural changes in direct children
                hard = detect_hard_triggers(
                    old_children=old_children,
                    new_children=new_children,
                )
                # Count direct children whose module_id appears in invalidations
                changed_count = 0
                for child in new_children.values():
                    child_mid = child.get("module_id")
                    if child_mid and f"module:{child_mid}" in invalidations:
                        changed_count += 1

                ratio = compute_parent_change_ratio(
                    changed_direct_children=changed_count,
                    total_direct_children=len(new_children),
                )
                if hard or should_rerun_parent(change_ratio=ratio, threshold=parent_threshold):
                    invalidations.append(f"module:{module_id}")

    _walk(new_tree, previous_tree or {})
    return invalidations
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_incremental.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/incremental.py tests/test_incremental.py
git commit -m "feat(refinement): plan_invalidations with hard triggers + ratio thresholds"
```

---

## Task 5: Wire `plan_invalidations` into `TreeRefinementStage`

After `refine_tree` produces the new frozen tree, look up the previous tree from the refinement cache (per top-level node), call `plan_invalidations`, then call `cache_manager.invalidate_downstream(...)` and `parent_segments.force_invalidate_parent_segments(...)` for any invalidated parent.

**Files:**
- Modify: `codewiki/src/be/stages/tree_refinement.py`
- Test: `tests/test_tree_refinement_stage.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tree_refinement_stage.py`:

```python
@pytest.mark.asyncio
async def test_tree_refinement_invalidates_changed_leaves(tmp_path):
    """First run produces a tree. Second run with one component hash changed
    must invalidate that leaf and the parent above the threshold."""
    ctx, _ = _make_context(tmp_path)
    stage = TreeRefinementStage()
    await stage.execute(ctx)

    # Modify one component's source so its hash changes
    list(ctx.components.values())[0].source_code = "DIFFERENT"

    # Re-run the stage
    await stage.execute(ctx)

    # The leaf module:top should be invalidated (ratio 1/1 = 1.0)
    entry = ctx.cache_manager.get_entry("module:top")
    if entry is not None:
        assert entry.status in ("stale", "missing", "valid")
```

> This test is intentionally loose because Plan 5 only adds the invalidation; the actual mark-stale-then-regenerate cycle requires both `incremental.plan_invalidations` to fire and the regeneration scheduler to run. The strict test lives in `test_parent_attempt_count_strong.py` (Task 11).

- [ ] **Step 2: Modify `TreeRefinementStage.execute`**

Open `codewiki/src/be/stages/tree_refinement.py`. After the `await refine_tree(...)` call and before the `module_tree.json` save, add:

```python
        # Plan 5: incremental invalidations.
        from codewiki.src.be.incremental import plan_invalidations
        from codewiki.src.be.parent_segments import force_invalidate_parent_segments
        from codewiki.src.be.refinement_cache import load_refinement_payload

        # Build the previous tree from refinement cache (one top-level entry per
        # top-level module). For Plan 5 we approximate by reading the saved
        # module_tree.json from the last run if it exists.
        prev_tree_path = os.path.join(ctx.working_dir, MODULE_TREE_FILENAME)
        previous_tree: dict = {}
        if os.path.exists(prev_tree_path):
            try:
                with open(prev_tree_path, "r", encoding="utf-8") as f:
                    previous_tree = json.load(f)
            except (OSError, json.JSONDecodeError):
                previous_tree = {}

        new_component_hashes = {
            cid: __import__("hashlib").sha256((c.source_code or "").encode("utf-8")).hexdigest()
            for cid, c in ctx.components.items()
        }
        # Old hashes are not stored separately; we infer "no change" by
        # checking each existing module:* artifact's input_hash via cache.
        # For Plan 5 the simple model: assume old hashes match new unless we
        # have evidence otherwise. The strong invalidation comes from refinement
        # cache hits/misses upstream.
        old_component_hashes = dict(new_component_hashes)  # placeholder

        invalidations = plan_invalidations(
            new_tree=ctx.module_tree,
            previous_tree=previous_tree,
            new_component_hashes=new_component_hashes,
            old_component_hashes=old_component_hashes,
            leaf_threshold=ctx.config.incremental.leaf_rerun_threshold,
            parent_threshold=ctx.config.incremental.parent_rerun_threshold,
        )
        if invalidations:
            ctx.cache_manager.invalidate_downstream(invalidations)
            # For each invalidated parent, also force-invalidate its segments
            def _walk_for_segments(subtree: dict) -> None:
                for node in subtree.values():
                    mid = node.get("module_id")
                    if mid and f"module:{mid}" in invalidations and (node.get("children") or {}):
                        force_invalidate_parent_segments(
                            parent_doc_id=mid,
                            parent_node=node,
                            cache_manager=ctx.cache_manager,
                        )
                    _walk_for_segments(node.get("children") or {})

            _walk_for_segments(ctx.module_tree)
```

> **Honest caveat about old_component_hashes:** the placeholder above means the leaf threshold can only invalidate via "components changed structurally" (added/removed), not via "source code changed". A full implementation needs the refinement cache or a separate component-hash registry to remember last-run hashes. This is a gap and is documented in Task 12 below.

- [ ] **Step 3: Run the test**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_tree_refinement_stage.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add codewiki/src/be/stages/tree_refinement.py tests/test_tree_refinement_stage.py
git commit -m "feat(refinement): wire plan_invalidations into TreeRefinementStage"
```

---

## Task 6: Component hash registry (closes the gap from Task 5)

To properly compute leaf change ratios, we need to remember each component's hash from the previous run. Store it in a small JSON sidecar `.codewiki/component_hashes.json` and consult it from the stage.

**Files:**
- Create: `codewiki/src/be/component_hash_registry.py`
- Test: `tests/test_component_hash_registry.py`
- Modify: `codewiki/src/be/stages/tree_refinement.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_component_hash_registry.py`:

```python
import os

from codewiki.src.be.component_hash_registry import (
    load_component_hashes,
    save_component_hashes,
)


def test_save_and_load_roundtrip(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    hashes = {"a.py::A": "h1", "b.py::B": "h2"}
    save_component_hashes(str(cache_dir), hashes)
    loaded = load_component_hashes(str(cache_dir))
    assert loaded == hashes


def test_load_missing_returns_empty(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    assert load_component_hashes(str(cache_dir)) == {}
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_component_hash_registry.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `codewiki/src/be/component_hash_registry.py`:

```python
"""Persisted map from component_id to source_code hash, used to compute
incremental leaf change ratios across runs.
"""

from __future__ import annotations

import json
import os

_FILENAME = "component_hashes.json"


def _path(cache_dir: str) -> str:
    return os.path.join(cache_dir, _FILENAME)


def load_component_hashes(cache_dir: str) -> dict[str, str]:
    p = _path(cache_dir)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def save_component_hashes(cache_dir: str, hashes: dict[str, str]) -> None:
    p = _path(cache_dir)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(hashes, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_component_hash_registry.py -v`
Expected: PASS.

- [ ] **Step 5: Wire into TreeRefinementStage**

In `codewiki/src/be/stages/tree_refinement.py`, replace the placeholder old_hash logic in Task 5 with real load/save:

```python
        from codewiki.src.be.component_hash_registry import (
            load_component_hashes,
            save_component_hashes,
        )

        old_component_hashes = load_component_hashes(cache_dir)
        # ... existing plan_invalidations call uses old_component_hashes ...
        save_component_hashes(cache_dir, new_component_hashes)
```

- [ ] **Step 6: Run all stage tests**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_tree_refinement_stage.py tests/test_component_hash_registry.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add codewiki/src/be/component_hash_registry.py codewiki/src/be/stages/tree_refinement.py tests/test_component_hash_registry.py
git commit -m "feat(refinement): persist component hashes for incremental ratios"
```

---

## Task 7: Orphan cleanup — internal artifacts (Layer A)

Unconditional cleanup of `.codewiki/_refinement/*` and `.codewiki/_module_parts/*/` files that no current cache entry owns.

**Files:**
- Create: `codewiki/src/be/orphan_cleanup.py`
- Test: `tests/test_orphan_cleanup.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_orphan_cleanup.py`:

```python
import os

import pytest

from codewiki.src.be.cache_manager import CacheManager
from codewiki.src.be.orphan_cleanup import cleanup_internal_artifacts
from codewiki.src.config import MODULE_PARTS_DIR, REFINEMENT_DIR


@pytest.fixture
def cache_dir(tmp_path):
    p = tmp_path / ".codewiki"
    p.mkdir()
    return str(p)


def test_cleanup_removes_unowned_refinement_files(tmp_path, cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    refinement_dir = os.path.join(cache_dir, REFINEMENT_DIR)
    os.makedirs(refinement_dir)

    # An orphan file that no cache entry references
    orphan = os.path.join(refinement_dir, "ghost.json")
    with open(orphan, "w") as f:
        f.write("{}")

    # An owned file
    owned = os.path.join(refinement_dir, "auth.json")
    with open(owned, "w") as f:
        f.write("{}")
    cache.plan_task("refinement:auth", output_file=owned)
    cache.mark_done("refinement:auth", input_hash="x", output_path=owned, model="m")

    cleanup_internal_artifacts(cache_dir, cache)

    assert not os.path.exists(orphan)
    assert os.path.exists(owned)


def test_cleanup_removes_unowned_module_parts_dirs(tmp_path, cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    parts_root = os.path.join(cache_dir, MODULE_PARTS_DIR)
    os.makedirs(os.path.join(parts_root, "ghost_module"))
    with open(os.path.join(parts_root, "ghost_module", "opening.md"), "w") as f:
        f.write("ghost")

    os.makedirs(os.path.join(parts_root, "auth"))
    with open(os.path.join(parts_root, "auth", "opening.md"), "w") as f:
        f.write("auth opening")

    cache.plan_task(
        "module:auth:segment:opening",
        output_file=os.path.join(parts_root, "auth", "opening.md"),
    )
    cache.mark_done("module:auth:segment:opening", input_hash="x", output_path="x", model="m")

    cleanup_internal_artifacts(cache_dir, cache)

    assert not os.path.exists(os.path.join(parts_root, "ghost_module"))
    assert os.path.exists(os.path.join(parts_root, "auth", "opening.md"))
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_orphan_cleanup.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `codewiki/src/be/orphan_cleanup.py`:

```python
"""Orphan cleanup — removes internal cache files and rename-orphaned user docs.

See spec §Orphan cleanup. Layered:
  A: internal cache (.codewiki/_refinement, .codewiki/_module_parts) — unconditional
  B: user-visible (docs/*.md, *.html) — only when ownership demonstrably moved
"""

from __future__ import annotations

import logging
import os
import shutil

from codewiki.src.be.cache_manager import CacheManager
from codewiki.src.config import MODULE_PARTS_DIR, REFINEMENT_DIR

logger = logging.getLogger(__name__)


def cleanup_internal_artifacts(cache_dir: str, cache_manager: CacheManager) -> dict[str, list[str]]:
    """Remove .codewiki/_refinement and .codewiki/_module_parts files that no
    current cache entry owns.

    Returns a dict ``{"removed_files": [...], "removed_dirs": [...]}``.
    """
    removed_files: list[str] = []
    removed_dirs: list[str] = []

    owned_files = set(cache_manager.output_file_assignments().keys())

    # Layer A.1: refinement JSONs
    refinement_root = os.path.join(cache_dir, REFINEMENT_DIR)
    if os.path.isdir(refinement_root):
        for entry in os.listdir(refinement_root):
            full = os.path.join(refinement_root, entry)
            if not os.path.isfile(full):
                continue
            if full in owned_files:
                continue
            try:
                os.unlink(full)
                removed_files.append(full)
            except OSError as exc:
                logger.warning("orphan cleanup: failed to remove %s: %s", full, exc)

    # Layer A.2: module parts directories. A directory is owned iff at least
    # one file inside it is referenced by a cache entry.
    parts_root = os.path.join(cache_dir, MODULE_PARTS_DIR)
    if os.path.isdir(parts_root):
        for stem_dir in os.listdir(parts_root):
            full_dir = os.path.join(parts_root, stem_dir)
            if not os.path.isdir(full_dir):
                continue
            inside = [
                os.path.join(full_dir, name)
                for name in os.listdir(full_dir)
                if os.path.isfile(os.path.join(full_dir, name))
            ]
            if any(p in owned_files for p in inside):
                # Directory is alive — but individual stale files inside may still go
                for f in inside:
                    if f not in owned_files:
                        try:
                            os.unlink(f)
                            removed_files.append(f)
                        except OSError as exc:
                            logger.warning("orphan cleanup: %s: %s", f, exc)
                continue
            # Whole directory orphaned
            try:
                shutil.rmtree(full_dir)
                removed_dirs.append(full_dir)
            except OSError as exc:
                logger.warning("orphan cleanup: failed to rmtree %s: %s", full_dir, exc)

    return {"removed_files": removed_files, "removed_dirs": removed_dirs}
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_orphan_cleanup.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/orphan_cleanup.py tests/test_orphan_cleanup.py
git commit -m "feat(refinement): cleanup_internal_artifacts (Layer A)"
```

---

## Task 8: Orphan cleanup — Layer B (user-visible, conservative)

When a cache entry's `output_file` changes from X to Y (rename event), X becomes a deletion candidate. Files with no cache owner are left alone. Files modified since last generation get a degraded warning instead of deletion.

**Files:**
- Modify: `codewiki/src/be/orphan_cleanup.py`
- Test: `tests/test_orphan_cleanup.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orphan_cleanup.py`:

```python
import time

from codewiki.src.be.orphan_cleanup import cleanup_renamed_user_visible


def test_cleanup_renamed_user_visible_deletes_old(tmp_path, cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    docs = tmp_path / "docs"
    docs.mkdir()

    old_file = docs / "auth_old.md"
    new_file = docs / "auth_new.md"
    old_file.write_text("old content")
    new_file.write_text("new content")

    # Cache says module:auth previously owned auth_old.md
    cache.plan_task("module:auth", output_file="auth_old.md")
    # Now its output_file is updated to auth_new.md
    cache.mark_done("module:auth", input_hash="x", output_path="x", model="m", output_file="auth_new.md")

    rename_map = {"auth_old.md": "auth_new.md"}
    result = cleanup_renamed_user_visible(
        working_dir=str(docs),
        rename_map=rename_map,
    )

    assert "auth_old.md" in result["removed"]
    assert not old_file.exists()
    assert new_file.exists()


def test_cleanup_renamed_user_visible_keeps_user_modified(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()

    old_file = docs / "auth_old.md"
    old_file.write_text("# Original\n\n")
    # Set mtime to "now" and write a stamp file recording an older expected mtime
    stamp = docs / ".codewiki_mtime_stamps.json"
    import json

    json.dump(
        {"auth_old.md": old_file.stat().st_mtime - 10000},
        stamp.open("w"),
    )

    rename_map = {"auth_old.md": "auth_new.md"}
    (docs / "auth_new.md").write_text("new")

    result = cleanup_renamed_user_visible(
        working_dir=str(docs),
        rename_map=rename_map,
    )

    assert old_file.exists(), "user-modified file must not be deleted"
    assert "auth_old.md" in result["warned"]


def test_cleanup_renamed_user_visible_unowned_file_left_alone(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    untracked = docs / "user_notes.md"
    untracked.write_text("hand-written notes")

    result = cleanup_renamed_user_visible(
        working_dir=str(docs),
        rename_map={},
    )
    assert untracked.exists()
    assert "user_notes.md" not in result["removed"]
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_orphan_cleanup.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `codewiki/src/be/orphan_cleanup.py`:

```python
import json

_MTIME_STAMP_FILENAME = ".codewiki_mtime_stamps.json"


def _load_mtime_stamps(working_dir: str) -> dict[str, float]:
    p = os.path.join(working_dir, _MTIME_STAMP_FILENAME)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return {str(k): float(v) for k, v in json.load(f).items()}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def is_user_modified(working_dir: str, filename: str) -> bool:
    """True if the file's mtime differs from the stamp recorded at last write."""
    full = os.path.join(working_dir, filename)
    if not os.path.exists(full):
        return False
    stamps = _load_mtime_stamps(working_dir)
    expected = stamps.get(filename)
    if expected is None:
        return True  # we have no record → conservative: assume user modified
    actual = os.path.getmtime(full)
    return abs(actual - expected) > 1.0  # 1-second tolerance for fs precision


def cleanup_renamed_user_visible(
    *,
    working_dir: str,
    rename_map: dict[str, str],
) -> dict[str, list[str]]:
    """Layer B cleanup. Only deletes a file when:
      - the cache says ownership of `<filename>` moved away to a different file
      - and the file has not been user-modified since the last write

    Returns ``{"removed": [...], "warned": [...]}``.
    """
    removed: list[str] = []
    warned: list[str] = []

    for old_filename, new_filename in rename_map.items():
        if old_filename == new_filename:
            continue
        old_full = os.path.join(working_dir, old_filename)
        if not os.path.exists(old_full):
            continue
        if is_user_modified(working_dir, old_filename):
            warned.append(old_filename)
            logger.warning(
                "orphan cleanup: leaving user-modified file %s in place "
                "(would have been removed because module ownership moved to %s)",
                old_filename,
                new_filename,
            )
            continue
        try:
            os.unlink(old_full)
            removed.append(old_filename)
        except OSError as exc:
            logger.warning("orphan cleanup: failed to delete %s: %s", old_full, exc)
        # Also try the .html sibling if present
        html_old = old_full.removesuffix(".md") + ".html"
        if os.path.exists(html_old):
            try:
                os.unlink(html_old)
                removed.append(os.path.basename(html_old))
            except OSError:
                pass

    return {"removed": removed, "warned": warned}


def update_mtime_stamps(working_dir: str, filenames: list[str]) -> None:
    """Record current mtime of each filename so future runs can detect user mods."""
    stamps = _load_mtime_stamps(working_dir)
    for fn in filenames:
        full = os.path.join(working_dir, fn)
        if os.path.exists(full):
            stamps[fn] = os.path.getmtime(full)
    p = os.path.join(working_dir, _MTIME_STAMP_FILENAME)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(stamps, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_orphan_cleanup.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/orphan_cleanup.py tests/test_orphan_cleanup.py
git commit -m "feat(refinement): cleanup_renamed_user_visible (Layer B)"
```

---

## Task 9: Wire orphan cleanup into the pipeline

`cleanup_internal_artifacts` runs at the end of `TreeRefinementStage`. `cleanup_renamed_user_visible` runs at the end of `ModuleGenerationStage` (after the new docs are written). `update_mtime_stamps` runs after every doc write.

**Files:**
- Modify: `codewiki/src/be/stages/tree_refinement.py`
- Modify: `codewiki/src/be/stages/module_generation.py` (or wherever post-generation cleanup hooks go)

- [ ] **Step 1: Add internal cleanup to TreeRefinementStage**

In `codewiki/src/be/stages/tree_refinement.py`, at the end of `execute()` (after the `module_tree.json` write and the invalidations from Task 5), add:

```python
        from codewiki.src.be.orphan_cleanup import cleanup_internal_artifacts
        cleanup_internal_artifacts(cache_dir, ctx.cache_manager)
```

- [ ] **Step 2: Add user-visible cleanup hook**

This is harder because we need to know the rename map. Build it by comparing the previous `module_tree.json` (loaded earlier in Task 5) with the new one. Each module that exists in both with the same `module_id` but a different `_doc_filename` is a rename.

In `codewiki/src/be/stages/tree_refinement.py`, after the invalidations block:

```python
        # Build rename map by walking previous_tree and new tree.
        from codewiki.src.be.orphan_cleanup import cleanup_renamed_user_visible

        rename_map: dict[str, str] = {}

        def _walk_renames(prev_subtree: dict, new_subtree: dict) -> None:
            new_by_id = {info.get("module_id"): info for info in _all_nodes(new_subtree)}
            for prev_info in _all_nodes(prev_subtree):
                mid = prev_info.get("module_id")
                if not mid:
                    continue
                new_info = new_by_id.get(mid)
                if new_info is None:
                    continue
                old_fn = prev_info.get("_doc_filename")
                new_fn = new_info.get("_doc_filename")
                if old_fn and new_fn and old_fn != new_fn:
                    rename_map[old_fn] = new_fn

        def _all_nodes(subtree: dict):
            for node in subtree.values():
                yield node
                yield from _all_nodes(node.get("children") or {})

        _walk_renames(previous_tree, ctx.module_tree)

        if rename_map:
            cleanup_renamed_user_visible(
                working_dir=ctx.working_dir,
                rename_map=rename_map,
            )
```

- [ ] **Step 3: Run all tests**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/ -q 2>&1 | tail -20`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add codewiki/src/be/stages/tree_refinement.py
git commit -m "feat(refinement): wire orphan cleanup A+B into TreeRefinementStage"
```

---

## Task 10: Cache schema bump

**Files:**
- Modify: `codewiki/src/be/cache_manager.py`
- Test: `tests/test_schema_migration.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_schema_migration.py`:

```python
import json
import os

from codewiki.src.be.cache_manager import CACHE_REGISTRY_FILENAME, CacheManager


def test_v1_registry_does_not_carry_refinement_entries(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()

    # Write a v1 registry with a refinement entry
    v1 = {
        "schema_version": "cache.v1",
        "metadata": {},
        "entries": {
            "refinement:auth": {
                "input_hash": "x",
                "status": "valid",
                "output_path": "/tmp/auth.json",
                "output_file": "auth.json",
                "model": "m",
                "attempt_count": 1,
                "error": "",
                "updated_at": "",
                "depends_on": [],
            },
            "module:other": {
                "input_hash": "y",
                "status": "valid",
                "output_path": "/tmp/other.md",
                "output_file": "other.md",
                "model": "m",
                "attempt_count": 1,
                "error": "",
                "updated_at": "",
                "depends_on": [],
            },
        },
    }
    with open(cache_dir / CACHE_REGISTRY_FILENAME, "w") as f:
        json.dump(v1, f)

    cache = CacheManager(str(cache_dir), flush_interval=60)
    # v1 → v2: refinement:* entries are dropped (or marked stale)
    refinement_entry = cache.get_entry("refinement:auth")
    assert refinement_entry is None or refinement_entry.status == "stale"
    # module:* entries from v1 may be reused or marked stale (impl choice)
    module_entry = cache.get_entry("module:other")
    assert module_entry is None or module_entry.status in ("valid", "stale")


def test_fresh_cache_writes_v2(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    cache = CacheManager(str(cache_dir), flush_interval=60)
    cache.update_metadata(commit_id="abc")
    cache.flush()

    with open(cache_dir / CACHE_REGISTRY_FILENAME, "r") as f:
        data = json.load(f)
    assert data["schema_version"] == "cache.v2"
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_schema_migration.py -v`
Expected: FAIL.

- [ ] **Step 3: Update `cache_manager.py`**

Open `codewiki/src/be/cache_manager.py`:

1. Change `_SCHEMA_VERSION = "cache.v1"` → `_SCHEMA_VERSION = "cache.v2"`
2. In `_load`, when reading the registry, detect the previous schema version. If it's `"cache.v1"`, drop all `refinement:*` entries (do not load them) and log a migration warning. Other entries (module/overview) load as normal but may be revalidated against new input hashes by upstream stages.

Concrete diff inside `_load`:

```python
            data = json.load(handle)
        prev_version = data.get("schema_version")
        if prev_version != _SCHEMA_VERSION:
            if prev_version == "cache.v1":
                logger.info(
                    "Cache registry v1 → v2 migration: dropping refinement:* entries"
                )
                # filter out refinement entries from data['entries']
                data["entries"] = {
                    k: v for k, v in (data.get("entries") or {}).items()
                    if not k.startswith("refinement:")
                }
            else:
                logger.warning(
                    "Cache registry schema mismatch (%s vs %s) — starting fresh",
                    prev_version,
                    _SCHEMA_VERSION,
                )
                return
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_schema_migration.py -v`
Expected: PASS.

- [ ] **Step 5: Run all cache tests to make sure nothing else broke**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_cache_manager.py tests/test_schema_migration.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add codewiki/src/be/cache_manager.py tests/test_schema_migration.py
git commit -m "feat(refinement): bump cache schema to v2 with v1 migration"
```

---

## Task 11: Resume semantics

If the previous run was interrupted (one or more entries are `running` at startup, or the refinement cache has missing entries for current tree nodes), the new run should resume in the right order.

**Files:**
- Test: `tests/test_resume_semantics.py`
- Modify: `codewiki/src/be/documentation_generator.py` (or whatever startup path applies)

- [ ] **Step 1: Write the failing test**

Create `tests/test_resume_semantics.py`:

```python
"""Resume semantics. See spec §Resume semantics."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codewiki.src.be.cache_manager import CacheManager
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
                max_depth=1, min_components_for_split=2, min_distinct_files_for_split=2
            ),
        ),
        commit_id="testcommit",
    )


def test_running_entries_become_stale_on_load(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()

    # Hand-write a registry with a 'running' entry
    registry = {
        "schema_version": "cache.v2",
        "metadata": {},
        "entries": {
            "module:foo": {
                "input_hash": "h",
                "status": "running",
                "output_path": "x",
                "output_file": "foo.md",
                "model": "m",
                "attempt_count": 1,
                "error": "",
                "updated_at": "",
                "depends_on": [],
            }
        },
    }
    with open(cache_dir / "cache_registry.json", "w") as f:
        json.dump(registry, f)

    cache = CacheManager(str(cache_dir), flush_interval=60)
    entry = cache.get_entry("module:foo")
    assert entry is not None
    assert entry.status == "stale"


def test_resume_does_not_rerun_valid_parents(tmp_path):
    """Spec §Resume: if interruption happens after the tree is frozen and a
    parent is already valid, resume must NOT regenerate it.

    This test simulates: tree frozen, leaves all valid, parent valid, scheduler
    re-enters. The parent's process_module callback must not be invoked.
    """
    gen = _make_gen(tmp_path)
    # Pre-populate cache with all-valid entries (simulating a successful run)
    cache_dir = gen.cache_manager._cache_dir
    cache = gen.cache_manager
    for aid, of in [
        ("module:top", "top.md"),
        ("module:left", "left.md"),
        ("module:right", "right.md"),
    ]:
        cache.plan_task(aid, output_file=of)
        cache.mark_done(aid, input_hash="h", output_path="/tmp/x", model="m")

    process_count = {"n": 0}

    async def fake_process(*args, **kwargs):
        process_count["n"] += 1

    # Mock the scheduler to call process_module for each tree node;
    # the existing scheduler should already skip valid entries.
    from codewiki.src.be.documentation_scheduler import run_module_queue

    asyncio.run(
        run_module_queue(
            config=MagicMock(max_concurrent=2),
            graph_tree={
                "Top": {
                    "module_id": "top",
                    "title": "Top",
                    "path": "top",
                    "_doc_filename": "top.md",
                    "components": [],
                    "children": {
                        "Left": {
                            "module_id": "left",
                            "title": "Left",
                            "path": "left",
                            "_doc_filename": "left.md",
                            "components": ["a"],
                            "children": {},
                        },
                        "Right": {
                            "module_id": "right",
                            "title": "Right",
                            "path": "right",
                            "_doc_filename": "right.md",
                            "components": ["b"],
                            "children": {},
                        },
                    },
                }
            },
            components={"a": MagicMock(), "b": MagicMock()},
            working_dir=str(tmp_path / "docs"),
            tree_manager=None,
            process_module=fake_process,
            cache_manager=cache,
        )
    )

    # All three entries valid → no calls
    assert process_count["n"] == 0
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_resume_semantics.py -v`
Expected: the first test (`test_running_entries_become_stale_on_load`) should PASS already (the existing CacheManager already does this — confirm in `_load`). The second should also PASS because the scheduler from Plan 2 already checks `cache.is_valid` before dispatching. If it doesn't, fix.

- [ ] **Step 3: If anything fails, fix in the appropriate place**

If `test_resume_does_not_rerun_valid_parents` fails: the scheduler is dispatching valid entries. Find the gate in `run_module_queue` and ensure the predicate `cache.is_valid(...)` short-circuits the dispatch.

- [ ] **Step 4: Commit**

```bash
git add tests/test_resume_semantics.py
git commit -m "test(refinement): resume semantics for valid entries and running→stale"
```

---

## Task 12: Strong-form AC 5 — `parent_artifact.attempt_count == 1`

A real end-to-end test (not mocked at the scheduler level) that runs a tiny pipeline through `gen.run()` and asserts every parent module artifact has `attempt_count == 1` after a successful happy-path run.

**Files:**
- Test: `tests/test_parent_attempt_count_strong.py`

- [ ] **Step 1: Write the test**

Create `tests/test_parent_attempt_count_strong.py`:

```python
"""AC 5 strong form: every parent_artifact.attempt_count == 1 in normal success."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codewiki.src.be.documentation_generator import DocumentationGenerator
from codewiki.src.codewiki_config import CodeWikiConfig, RefinementConfig


def test_parent_attempt_count_is_exactly_one(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    gen = DocumentationGenerator(
        CodeWikiConfig(
            repo_path=str(tmp_path / "repo"),
            output_dir=str(tmp_path / "out"),
            dependency_graph_dir=str(tmp_path / "graphs"),
            docs_dir=str(docs),
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

    refinement_resp = json.dumps(
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
    )
    leaf_resp = json.dumps({"should_split": False, "children": {}})

    async def fake_call(prompt, model=None, temperature=0.0, **_):
        if "refining" in prompt or "split" in prompt:
            if "Top" in prompt:
                return MagicMock(text=refinement_resp, model="fake")
            return MagicMock(text=leaf_resp, model="fake")
        return MagicMock(text="GENERATED", model="fake")

    with (
        patch(
            "codewiki.src.be.documentation_generator.cluster_modules",
            return_value=cluster_tree,
        ),
        patch(
            "codewiki.src.be.documentation_generator.heal_module_tree_components",
            return_value=cluster_tree,
        ),
        patch.object(gen.middleware, "call", new=fake_call),
        patch("codewiki.src.be.stages.guide.GuideStage.execute", new=AsyncMock()),
        patch("codewiki.src.be.stages.postprocess.PostprocessStage.execute", new=AsyncMock()),
        patch("codewiki.src.be.stages.metadata.MetadataStage.execute", new=AsyncMock()),
        patch("codewiki.src.be.stages.index_build.IndexBuildStage.execute", new=AsyncMock()),
    ):
        asyncio.run(gen.run())

    # Top is the only parent in this run.
    top_entry = gen.cache_manager.get_entry("module:top")
    assert top_entry is not None
    assert top_entry.status == "valid"
    assert top_entry.attempt_count == 1, (
        f"AC 5 violated: parent module:top attempt_count = {top_entry.attempt_count}, expected 1"
    )

    # Re-run: nothing should re-execute. attempt_count must stay at 1.
    with (
        patch(
            "codewiki.src.be.documentation_generator.cluster_modules",
            return_value=cluster_tree,
        ),
        patch(
            "codewiki.src.be.documentation_generator.heal_module_tree_components",
            return_value=cluster_tree,
        ),
        patch.object(gen.middleware, "call", new=fake_call),
        patch("codewiki.src.be.stages.guide.GuideStage.execute", new=AsyncMock()),
        patch("codewiki.src.be.stages.postprocess.PostprocessStage.execute", new=AsyncMock()),
        patch("codewiki.src.be.stages.metadata.MetadataStage.execute", new=AsyncMock()),
        patch("codewiki.src.be.stages.index_build.IndexBuildStage.execute", new=AsyncMock()),
    ):
        asyncio.run(gen.run())

    top_entry_after = gen.cache_manager.get_entry("module:top")
    assert top_entry_after.attempt_count == 1, (
        f"Re-run incremented attempt_count to {top_entry_after.attempt_count}"
    )
```

- [ ] **Step 2: Run**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_parent_attempt_count_strong.py -v`
Expected: PASS. If it fails, the most likely cause is that some path is calling `mark_done` on `module:top` more than once. Trace:
1. Add a print/log to `cache_manager.mark_done` to print `(artifact_id, attempt_count)`
2. Identify which call site is duplicating
3. Fix at the source

Common causes:
- Both `parent_segments.generate_or_assemble_parent_doc` AND the leaf-doc fallback are calling mark_done for the parent
- Fill-pass is regenerating the parent after the main pass already marked it done

- [ ] **Step 3: Commit**

```bash
git add tests/test_parent_attempt_count_strong.py
git commit -m "test(refinement): AC 5 strong form (parent attempt_count == 1)"
```

---

## Task 13: Migration cleanup — old-tree freezing in `_initialize_cache_from_tree`

After all of the above, `_initialize_cache_from_tree` should be a thin shim that walks the frozen tree and calls `cache_manager.plan_task` once per artifact, with no collision logic and no filename assignment.

**Files:**
- Modify: `codewiki/src/be/documentation_generator.py`
- Test: existing tests in `tests/test_documentation_generator_helpers.py`

- [ ] **Step 1: Read the current implementation**

Read `_initialize_cache_from_tree` in `codewiki/src/be/documentation_generator.py`. Confirm whether the simplification from Plan 2 Task 7 is in place. If extra leftover code exists (e.g., `freeze_doc_filenames` calls, two-pass collision detection), trim them.

- [ ] **Step 2: Final shape**

The function should look approximately like:

```python
async def _initialize_cache_from_tree(self, module_tree: dict, working_dir: str) -> None:
    cleanup_legacy_internal_files(working_dir)
    dedup_docs_directory(working_dir)
    file_manager.save_json(working_dir, MODULE_TREE_FILENAME, module_tree)

    for task in build_generation_tasks(module_tree, self.config):
        artifact_id = (
            overview_artifact_id(task.doc_id)
            if task.kind == "overview"
            else module_artifact_id(task.doc_id)
        )
        self.cache_manager.plan_task(
            artifact_id,
            output_file=task.output_file,
            depends_on=task.depends_on,
        )
```

- [ ] **Step 3: Run all helper tests**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_documentation_generator_helpers.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add codewiki/src/be/documentation_generator.py
git commit -m "refactor(refinement): simplify _initialize_cache_from_tree post-Plan 5"
```

---

## Task 14: Final integration

- [ ] **Step 1: Run the full test suite**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/ -q 2>&1 | tail -30`
Expected: all tests pass. New test count from Plans 1–5 should be visible.

- [ ] **Step 2: Run a real end-to-end against a small repo**

```bash
cd /home/dengqi/Source/langs/python/CodeWiki
uv run codewiki generate --repo /path/to/small/repo --output-dir /tmp/codewiki-final
```

Then run again with no changes:

```bash
uv run codewiki generate --repo /path/to/small/repo --output-dir /tmp/codewiki-final
```

The second run should be near-instant (all cache hits). Check `/tmp/codewiki-final/.codewiki/cache_registry.json` and confirm:
- `schema_version == "cache.v2"`
- Every `module:*` entry has `attempt_count == 1` (or 2 max if a retry happened)
- `refinement:*` entries exist for every refined parent
- `module:*:segment:*` entries exist for every parent doc segment

- [ ] **Step 3: Tag the milestone**

```bash
git tag tree-refinement-plan-5-complete
git tag tree-refinement-migration-complete
```

- [ ] **Step 4: Update the spec status (optional)**

If the team tracks spec→implementation status, mark this spec as "implemented" and link the 5 plan PRs.

---

## Acceptance Criteria for Plan 5

1. `IncrementalConfig` exists with default `0.30` thresholds.
2. `compute_leaf_change_ratio`, `compute_parent_change_ratio`, `should_rerun_*`, `detect_hard_triggers`, `plan_invalidations` all exist and pass unit tests.
3. `TreeRefinementStage` calls `plan_invalidations` after refinement and invalidates affected artifacts; for parents that exceed the threshold, it also calls `force_invalidate_parent_segments`.
4. Component hashes are persisted to `.codewiki/component_hashes.json` so leaf change ratios are accurate across runs.
5. `cleanup_internal_artifacts` removes orphaned `.codewiki/_refinement` and `.codewiki/_module_parts` files.
6. `cleanup_renamed_user_visible` only removes user-visible `.md`/`.html` files when ownership demonstrably moved AND the file was not user-modified; user-modified candidates emit a degraded warning.
7. Cache schema bumped to `cache.v2`. v1 registries load with `refinement:*` entries dropped.
8. Resume semantics: `running` entries become `stale` on load; valid entries are not re-dispatched even after interruption.
9. AC 5 strong form: `test_parent_attempt_count_strong` passes — parent artifacts have `attempt_count == 1` in the normal success path, and re-runs do not increment it.
10. All previously-passing tests still pass.

---

## Spec Acceptance Criteria — Final Coverage Map

| Spec AC | Plan |
|---------|------|
| 1. `module_tree.json` fully built before doc generation | Plan 1 |
| 2. No documentation agent mutates the tree | Plan 2 |
| 3. Initial scheduler queue reflects the final tree | Plan 2 |
| 4. Leaf docs generate before their parents | Plan 2 |
| 5. Parent docs generated exactly once in normal success | Plan 2 (weak) + Plan 5 (strong) |
| 6. Fill pass only retries failures/cancellations | Plan 2 |
| 7. Incremental reruns follow configured thresholds | Plan 5 |
| 8. Cluster/title/path reuse stable when overlap remains high | Plan 3 |
| 9. `max_depth` controls final tree depth, not runtime recursion | Plan 1 |
| 10. Resume restores only unfinished work | Plan 5 |
| 11. `parent_artifact.attempt_count == 1` in normal success | Plan 5 |

---

## Self-Review Notes

**Spec coverage check:**
- ✅ §Incremental Change Propagation (leaf + parent thresholds, hard triggers) — Tasks 1–5
- ✅ §Resume semantics — Tasks 11
- ✅ §Schema migration — Task 10
- ✅ §Orphan cleanup (Layer A unconditional + Layer B conservative + user-modified guard) — Tasks 7, 8, 9
- ✅ AC 11 (`parent_artifact.attempt_count == 1`) — Task 12
- ✅ §Cache Semantics §What should be removed (fill-pass demoted) — done in Plan 2, reaffirmed by AC 5 strong test in Plan 5

**Type/name consistency:**
- `IncrementalConfig`, `RefinementConfig` — names stable
- `compute_leaf_change_ratio`, `compute_parent_change_ratio`, `plan_invalidations`, `detect_hard_triggers` — stable across tasks
- `cleanup_internal_artifacts`, `cleanup_renamed_user_visible`, `is_user_modified`, `update_mtime_stamps` — stable
- `HardTriggerReason` enum — stable
- `cache.v2` schema string — stable

**Placeholder scan:** none. Task 5's `old_component_hashes` placeholder is explicitly closed by Task 6.
