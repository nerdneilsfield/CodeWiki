# Tree Refinement Phase 3: Identity Reuse

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add identity reuse to the refinement stage so re-runs preserve `module_id`/`title`/`path` for nodes whose component membership is "mostly the same" as a prior run. Apply the same mechanism to top-level clustering (Stage 3) so we have a single identity-reuse implementation rather than two.

**Architecture:** A pure `identity_reuse.py` module that holds the matching math: exact-set match, `overlap_ratio = |new ∩ old| / |new|` for normal/merge cases, `split_successor_overlap = |new ∩ old| / |old|` for split cases, with the spec's `>= identity_reuse_threshold` AND `>= margin` rules. `tree_refiner.refine_one_node` consumes a `previous_subtree` lookup and applies identity reuse before assigning fresh ids/filenames. `cluster_modules` (top-level) is rewired to call the same matcher instead of its existing freeze rule.

**Tech Stack:** Python 3.10+, pytest, existing CodeWiki internals.

**Spec reference:** §Identity Reuse Strategy, §Matching strategy, §Split / Merge handling, Stage 3 unification note.

**Prerequisite:** Plans 1 and 2 merged. Smoke tests passing on `main`.

**Out of scope for Plan 3:**
- Parent doc segment cache — Plan 4
- Resume semantics, orphan cleanup, schema bump — Plan 5

---

## File Structure

### New files

| Path | Responsibility |
|------|----------------|
| `codewiki/src/be/identity_reuse.py` | Pure matching math: `match_overlap`, `find_dominant_match`, `find_split_successor`, `find_merge_predecessor`, `IdentityMatch` dataclass |
| `tests/test_identity_reuse.py` | Unit tests for every matching path |

### Modified files

| Path | Change |
|------|--------|
| `codewiki/src/be/tree_refiner.py` | `refine_one_node` accepts `previous_subtree` parameter, applies identity reuse to children before assigning fresh ids |
| `codewiki/src/be/clustering/pipeline.py` | Replace existing top-level naming freeze (`_apply_naming_freeze` around line 211, called from line 91) with a call to `identity_reuse.find_dominant_match`. The freeze does **not** live in `cluster_modules.py` — it lives in the clustering sub-package. |
| `codewiki/src/be/refinement_cache.py` | `compute_refinement_input_hash` already includes `identity_reuse_threshold` (Plan 1 Task 4 — confirm); add helper to load previous subtree from refinement cache |
| `tests/test_tree_refiner.py` | Extend with identity reuse scenarios |
| `tests/test_clustering_pipeline.py` | Update to assert top-level identity reuse goes through the new module |

---

## Task 0: Baseline check

- [ ] **Step 1: Confirm Plans 1+2 merged**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && git log --oneline -30 | grep -i "refinement\|frozen"`
Expected: see commits from both Plans 1 and 2.

- [ ] **Step 2: Run the suite**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/ -q 2>&1 | tail -10`
Expected: PASS.

---

## Task 1: `IdentityMatch` dataclass and `match_overlap` helper

**Files:**
- Create: `codewiki/src/be/identity_reuse.py`
- Test: `tests/test_identity_reuse.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_identity_reuse.py`:

```python
import pytest

from codewiki.src.be.identity_reuse import IdentityMatch, match_overlap


def test_match_overlap_full_overlap():
    new = {"a", "b", "c"}
    old = {"a", "b", "c"}
    assert match_overlap(new, old) == 1.0


def test_match_overlap_partial():
    new = {"a", "b", "c", "d"}
    old = {"a", "b", "x", "y"}
    # |new ∩ old| / |new| = 2/4 = 0.5
    assert match_overlap(new, old) == 0.5


def test_match_overlap_zero():
    assert match_overlap({"a"}, {"b"}) == 0.0


def test_match_overlap_empty_new_returns_zero():
    assert match_overlap(set(), {"a"}) == 0.0


def test_identity_match_dataclass_fields():
    m = IdentityMatch(
        old_key="auth_layer",
        old_module_id="auth_layer",
        old_title="Auth Layer",
        old_path="auth_layer",
        overlap=0.85,
        margin=0.30,
    )
    assert m.old_key == "auth_layer"
    assert m.overlap == 0.85
    assert m.margin == 0.30
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_identity_reuse.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the dataclass and helper**

Create `codewiki/src/be/identity_reuse.py`:

```python
"""Identity reuse for tree refinement and top-level clustering.

See spec §Identity Reuse Strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class IdentityMatch:
    """Result of matching a new node against an old sibling for identity reuse."""

    old_key: str
    old_module_id: str
    old_title: str
    old_path: str
    overlap: float
    margin: float  # overlap minus second-best overlap


def match_overlap(new_components: set[str], old_components: set[str]) -> float:
    """Return ``|new ∩ old| / |new|``. New-normalized overlap.

    Used for normal identity reuse and for merge predecessor matching.
    See spec §Matching strategy and §Merge.
    """
    if not new_components:
        return 0.0
    return len(new_components & old_components) / len(new_components)
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_identity_reuse.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/identity_reuse.py tests/test_identity_reuse.py
git commit -m "feat(refinement): add IdentityMatch and match_overlap helper"
```

---

## Task 2: `find_dominant_match` — exact match + dominant overlap

**Files:**
- Modify: `codewiki/src/be/identity_reuse.py`
- Test: `tests/test_identity_reuse.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_identity_reuse.py`:

```python
from codewiki.src.be.identity_reuse import find_dominant_match


def _old_sibling(key: str, module_id: str, components: list[str]) -> dict:
    return {
        key: {
            "module_id": module_id,
            "title": key,
            "path": module_id,
            "components": components,
            "children": {},
        }
    }


def test_find_dominant_match_exact_set():
    new = {"a.py::A", "b.py::B"}
    old_siblings = {
        "Auth": {
            "module_id": "auth",
            "title": "Auth",
            "path": "auth",
            "components": ["a.py::A", "b.py::B"],
            "children": {},
        },
        "Other": {
            "module_id": "other",
            "title": "Other",
            "path": "other",
            "components": ["x.py::X"],
            "children": {},
        },
    }
    m = find_dominant_match(new, old_siblings, threshold=0.70, margin=0.15)
    assert m is not None
    assert m.old_key == "Auth"
    assert m.overlap == 1.0


def test_find_dominant_match_dominant_overlap():
    new = {"a.py::A", "b.py::B", "c.py::C", "d.py::D"}
    old_siblings = {
        "Auth": {
            "module_id": "auth",
            "title": "Auth",
            "path": "auth",
            "components": ["a.py::A", "b.py::B", "c.py::C"],  # 3/4 = 0.75
            "children": {},
        },
        "Other": {
            "module_id": "other",
            "title": "Other",
            "path": "other",
            "components": ["x.py::X"],  # 0/4 = 0.0
            "children": {},
        },
    }
    m = find_dominant_match(new, old_siblings, threshold=0.70, margin=0.15)
    assert m is not None
    assert m.old_key == "Auth"
    assert m.overlap == 0.75
    assert m.margin >= 0.15


def test_find_dominant_match_below_threshold():
    new = {"a.py::A", "b.py::B", "c.py::C", "d.py::D"}
    old_siblings = {
        "Auth": {
            "module_id": "auth",
            "title": "Auth",
            "path": "auth",
            "components": ["a.py::A", "b.py::B"],  # 2/4 = 0.5
            "children": {},
        },
    }
    assert find_dominant_match(new, old_siblings, threshold=0.70, margin=0.15) is None


def test_find_dominant_match_two_close_candidates_rejected():
    new = {"a.py::A", "b.py::B", "c.py::C", "d.py::D"}
    old_siblings = {
        "Auth": {
            "module_id": "auth",
            "title": "Auth",
            "path": "auth",
            "components": ["a.py::A", "b.py::B", "c.py::C"],  # 3/4 = 0.75
            "children": {},
        },
        "AuthV2": {
            "module_id": "auth_v2",
            "title": "AuthV2",
            "path": "auth_v2",
            "components": ["a.py::A", "b.py::B", "d.py::D"],  # 3/4 = 0.75
            "children": {},
        },
    }
    # Both 0.75; margin 0.0, fails margin >= 0.15
    assert find_dominant_match(new, old_siblings, threshold=0.70, margin=0.15) is None


def test_find_dominant_match_no_old_siblings_returns_none():
    assert find_dominant_match({"a"}, {}, 0.70, 0.15) is None
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_identity_reuse.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `find_dominant_match`**

Append to `codewiki/src/be/identity_reuse.py`:

```python
def find_dominant_match(
    new_components: set[str],
    old_siblings: dict[str, Any],
    threshold: float,
    margin: float,
) -> IdentityMatch | None:
    """Find the dominant old sibling for a new node.

    Returns an ``IdentityMatch`` if the best old sibling clears the threshold
    and is dominant by at least ``margin``; ``None`` otherwise.

    Exact component-set matches always win regardless of margin (spec §Matching
    strategy bullet 1).
    """
    if not old_siblings or not new_components:
        return None

    scored: list[tuple[float, str, dict]] = []
    for old_key, old_info in old_siblings.items():
        old_components = set(old_info.get("components") or [])
        if not old_components:
            continue
        # Exact set match → return immediately with overlap 1.0 and full margin.
        if new_components == old_components:
            return IdentityMatch(
                old_key=old_key,
                old_module_id=old_info.get("module_id", ""),
                old_title=old_info.get("title", old_key),
                old_path=old_info.get("path", ""),
                overlap=1.0,
                margin=1.0,
            )
        scored.append((match_overlap(new_components, old_components), old_key, old_info))

    if not scored:
        return None

    scored.sort(key=lambda t: t[0], reverse=True)
    best_overlap, best_key, best_info = scored[0]
    second_overlap = scored[1][0] if len(scored) > 1 else 0.0
    delta = best_overlap - second_overlap

    if best_overlap < threshold:
        return None
    if delta < margin:
        return None

    return IdentityMatch(
        old_key=best_key,
        old_module_id=best_info.get("module_id", ""),
        old_title=best_info.get("title", best_key),
        old_path=best_info.get("path", ""),
        overlap=best_overlap,
        margin=delta,
    )
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_identity_reuse.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/identity_reuse.py tests/test_identity_reuse.py
git commit -m "feat(refinement): find_dominant_match for identity reuse"
```

---

## Task 3: `find_split_successor` — split-direction overlap (`/|old|`)

**Files:**
- Modify: `codewiki/src/be/identity_reuse.py`
- Test: `tests/test_identity_reuse.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_identity_reuse.py`:

```python
from codewiki.src.be.identity_reuse import find_split_successor


def test_find_split_successor_dominant():
    """Old node 'auth' with 4 components is split into two new groups.
    The new group containing 3 of the 4 old components becomes the successor.
    """
    old_components = {"a", "b", "c", "d"}
    new_groups = {
        "AuthCore": {"components": ["a", "b", "c"]},  # 3/4 = 0.75
        "AuthExtra": {"components": ["d", "e"]},  # 1/4 = 0.25
    }
    successor = find_split_successor(
        old_components, new_groups, threshold=0.70, margin=0.15
    )
    assert successor == "AuthCore"


def test_find_split_successor_not_dominant_enough():
    old_components = {"a", "b", "c", "d"}
    new_groups = {
        "G1": {"components": ["a", "b"]},  # 2/4 = 0.5
        "G2": {"components": ["c", "d"]},  # 2/4 = 0.5
    }
    assert find_split_successor(old_components, new_groups, 0.70, 0.15) is None


def test_find_split_successor_below_threshold():
    old_components = {"a", "b", "c", "d", "e"}
    new_groups = {
        "G1": {"components": ["a", "b"]},  # 2/5 = 0.4
        "G2": {"components": ["x"]},  # 0/5
    }
    assert find_split_successor(old_components, new_groups, 0.70, 0.15) is None


def test_find_split_successor_margin_check():
    """Two groups close to threshold but margin too small."""
    old_components = {"a", "b", "c", "d"}
    new_groups = {
        "G1": {"components": ["a", "b", "c"]},  # 3/4 = 0.75
        "G2": {"components": ["a", "b", "d"]},  # 3/4 = 0.75
    }
    # Both 0.75, margin 0.0
    assert find_split_successor(old_components, new_groups, 0.70, 0.15) is None
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_identity_reuse.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `find_split_successor`**

Append to `codewiki/src/be/identity_reuse.py`:

```python
def _split_overlap(new_components: set[str], old_components: set[str]) -> float:
    """``|new ∩ old| / |old|`` — old-normalized overlap.

    Used to identify which new node is the dominant successor of an old node
    being split. See spec §Split.
    """
    if not old_components:
        return 0.0
    return len(new_components & old_components) / len(old_components)


def find_split_successor(
    old_components: set[str],
    new_groups: dict[str, Any],
    threshold: float,
    margin: float,
) -> str | None:
    """Return the key of the new group that should inherit the old node's identity.

    Spec formula:
        split_successor_overlap = |new ∩ old| / |old|
        keep iff overlap >= threshold AND (overlap - second_best) >= margin
    """
    if not old_components or not new_groups:
        return None

    scored: list[tuple[float, str]] = []
    for new_key, new_info in new_groups.items():
        new_components = set(new_info.get("components") or [])
        if not new_components:
            continue
        scored.append((_split_overlap(new_components, old_components), new_key))

    if not scored:
        return None

    scored.sort(key=lambda t: t[0], reverse=True)
    best_overlap, best_key = scored[0]
    second_overlap = scored[1][0] if len(scored) > 1 else 0.0

    if best_overlap < threshold:
        return None
    if best_overlap - second_overlap < margin:
        return None
    return best_key
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_identity_reuse.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/identity_reuse.py tests/test_identity_reuse.py
git commit -m "feat(refinement): find_split_successor with old-normalized overlap"
```

---

## Task 4: `find_merge_predecessor` — merge-direction overlap (`/|new|`)

The spec calls this out explicitly: merge uses **new-normalized** overlap (the same formula as `match_overlap`), not old-normalized. So `find_merge_predecessor` is essentially `find_dominant_match` over the old siblings of a single merged-into new node — but we expose it as a separate symbol so callers can be explicit about intent.

**Files:**
- Modify: `codewiki/src/be/identity_reuse.py`
- Test: `tests/test_identity_reuse.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_identity_reuse.py`:

```python
from codewiki.src.be.identity_reuse import find_merge_predecessor


def test_find_merge_predecessor_dominant():
    """One new node is formed by merging old A (mostly) + a few from old B.
    Old A should be the predecessor.
    """
    new_components = {"a", "b", "c", "d", "e"}  # 5 total
    old_siblings = {
        "OldA": {
            "module_id": "old_a",
            "title": "OldA",
            "path": "old_a",
            "components": ["a", "b", "c", "d"],  # 4/5 = 0.8
            "children": {},
        },
        "OldB": {
            "module_id": "old_b",
            "title": "OldB",
            "path": "old_b",
            "components": ["e"],  # 1/5 = 0.2
            "children": {},
        },
    }
    m = find_merge_predecessor(new_components, old_siblings, threshold=0.70, margin=0.15)
    assert m is not None
    assert m.old_key == "OldA"


def test_find_merge_predecessor_close_split_rejected():
    """Two old nodes contributed about equally — neither is dominant."""
    new_components = {"a", "b", "c", "d"}
    old_siblings = {
        "OldA": {
            "module_id": "old_a",
            "title": "OldA",
            "path": "old_a",
            "components": ["a", "b"],  # 2/4 = 0.5
            "children": {},
        },
        "OldB": {
            "module_id": "old_b",
            "title": "OldB",
            "path": "old_b",
            "components": ["c", "d"],  # 2/4 = 0.5
            "children": {},
        },
    }
    assert find_merge_predecessor(new_components, old_siblings, 0.70, 0.15) is None
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_identity_reuse.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `codewiki/src/be/identity_reuse.py`:

```python
def find_merge_predecessor(
    new_components: set[str],
    old_siblings: dict[str, Any],
    threshold: float,
    margin: float,
) -> IdentityMatch | None:
    """Identify the dominant old predecessor when several old nodes merge into one new node.

    Uses the new-normalized overlap formula:
        merge_predecessor_overlap = |new ∩ old| / |new|

    Note: structurally identical to ``find_dominant_match``. Exposed as a
    separate name so call sites are explicit about intent (merge vs general
    identity reuse). See spec §Merge.
    """
    return find_dominant_match(new_components, old_siblings, threshold, margin)
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_identity_reuse.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/identity_reuse.py tests/test_identity_reuse.py
git commit -m "feat(refinement): find_merge_predecessor (merge-direction matching)"
```

---

## Task 5: Helper to load previous subtree from refinement cache

Identity reuse needs to look up "what were this parent's children last time?" The refinement cache already persists subtrees as JSON; this task adds a small load helper that returns the children dict for a given parent doc_id.

**Files:**
- Modify: `codewiki/src/be/refinement_cache.py`
- Test: `tests/test_refinement_cache.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_refinement_cache.py`:

```python
from codewiki.src.be.refinement_cache import (
    load_previous_children,
    save_refinement_payload,
)


def test_load_previous_children_returns_dict_when_present(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    save_refinement_payload(
        str(cache_dir),
        "auth",
        {
            "children": {
                "Login": {
                    "module_id": "login",
                    "title": "Login",
                    "path": "login",
                    "components": ["a.py::Login"],
                },
            }
        },
    )
    children = load_previous_children(str(cache_dir), "auth")
    assert children is not None
    assert "Login" in children
    assert children["Login"]["module_id"] == "login"


def test_load_previous_children_missing_returns_empty_dict(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    assert load_previous_children(str(cache_dir), "missing") == {}
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_refinement_cache.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `codewiki/src/be/refinement_cache.py`:

```python
def load_previous_children(cache_dir: str, doc_id: str) -> dict:
    """Return the children dict from a previously persisted refinement payload.

    Returns an empty dict if the payload is missing or has no children.
    """
    payload = load_refinement_payload(cache_dir, doc_id)
    if not payload:
        return {}
    return payload.get("children", {}) or {}
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_refinement_cache.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/refinement_cache.py tests/test_refinement_cache.py
git commit -m "feat(refinement): add load_previous_children helper"
```

---

## Task 6: Wire identity reuse into `refine_one_node`

When the LLM returns new children, walk each new child and try to match it against the previous siblings (loaded via `load_previous_children`). If a match is found, copy `module_id`/`title`/`path`/`_doc_filename` from the matched old node. If no match, generate fresh ids.

**Files:**
- Modify: `codewiki/src/be/tree_refiner.py`
- Test: `tests/test_tree_refiner.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tree_refiner.py`:

```python
from codewiki.src.be.refinement_cache import save_refinement_payload


@pytest.mark.asyncio
async def test_refine_one_node_reuses_identity_from_previous_run(cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    components = {
        "a.py::A": _node("a.py::A", "a.py"),
        "b.py::B": _node("b.py::B", "b.py"),
        "c.py::C": _node("c.py::C", "c.py"),
        "d.py::D": _node("d.py::D", "d.py"),
    }

    # Pre-seed the refinement cache with a previous payload
    save_refinement_payload(
        cache_dir,
        "root",
        {
            "children": {
                "AuthLayer": {
                    "module_id": "auth_layer",
                    "title": "AuthLayer",
                    "path": "auth_layer",
                    "_doc_filename": "auth_layer.md",
                    "components": ["a.py::A", "b.py::B"],
                    "children": {},
                },
                "DataLayer": {
                    "module_id": "data_layer",
                    "title": "DataLayer",
                    "path": "data_layer",
                    "_doc_filename": "data_layer.md",
                    "components": ["c.py::C", "d.py::D"],
                    "children": {},
                },
            }
        },
    )

    # The LLM proposes new groupings with slightly renamed titles but the same
    # component sets. Identity reuse must take over and preserve the old ids.
    middleware = MagicMock()
    middleware.call = AsyncMock(
        return_value=MagicMock(
            text=json.dumps(
                {
                    "should_split": True,
                    "children": {
                        "Authentication": {
                            "module_id": "authentication",
                            "title": "Authentication",
                            "path": "authentication",
                            "description": ".",
                            "components": ["a.py::A", "b.py::B"],
                        },
                        "DataAccess": {
                            "module_id": "data_access",
                            "title": "DataAccess",
                            "path": "data_access",
                            "description": ".",
                            "components": ["c.py::C", "d.py::D"],
                        },
                    },
                }
            ),
            model="fake",
        )
    )
    cfg = RefinementConfig(
        max_depth=2,
        min_components_for_split=2,
        min_distinct_files_for_split=2,
        identity_reuse_threshold=0.70,
    )
    used: dict[str, str] = {}
    children = await refine_one_node(
        parent_doc_id="root",
        parent_title="Root",
        parent_path="root",
        component_ids=list(components.keys()),
        components=components,
        current_depth=1,
        refinement_cfg=cfg,
        output_language="en",
        cluster_model="c",
        middleware=middleware,
        cache_manager=cache,
        cache_dir=cache_dir,
        used_files=used,
    )

    # Identity reuse: keys come from LLM ('Authentication'/'DataAccess')
    # but module_id/path/_doc_filename are inherited from old siblings.
    assert "Authentication" in children
    assert children["Authentication"]["module_id"] == "auth_layer"
    assert children["Authentication"]["path"] == "auth_layer"
    assert children["Authentication"]["_doc_filename"] == "auth_layer.md"
    assert children["DataAccess"]["module_id"] == "data_layer"
    assert children["DataAccess"]["_doc_filename"] == "data_layer.md"
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_tree_refiner.py::test_refine_one_node_reuses_identity_from_previous_run -v`
Expected: FAIL — current `refine_one_node` always assigns fresh ids.

- [ ] **Step 3: Wire identity reuse into `refine_one_node`**

Open `codewiki/src/be/tree_refiner.py`. Find the section in `refine_one_node` that constructs `children` from `children_raw`. Replace it:

```python
from codewiki.src.be.identity_reuse import IdentityMatch, find_dominant_match
from codewiki.src.be.refinement_cache import load_previous_children

# ... inside refine_one_node, after parsing children_raw:

    previous_children = load_previous_children(cache_dir, parent_doc_id)
    # Mutable copy so we can pop matched old siblings to prevent two new nodes
    # from claiming the same old identity.
    available_old: dict[str, Any] = dict(previous_children)

    children: dict[str, Any] = {}
    for title, child in children_raw.items():
        new_components = set(child.get("components") or [])
        match = find_dominant_match(
            new_components,
            available_old,
            threshold=refinement_cfg.identity_reuse_threshold,
            margin=0.15,
        )

        if match is not None:
            module_id = match.old_module_id
            path = match.old_path or module_id
            title_to_use = child.get("title", title)
            preferred_stem = path or module_id
            # Reuse the old _doc_filename verbatim if it's still available.
            old_filename = available_old[match.old_key].get("_doc_filename")
            available_old.pop(match.old_key, None)
            child_artifact = f"module:{module_id}"
            if old_filename and (
                used_files.get(old_filename) in (None, child_artifact)
            ):
                used_files[old_filename] = child_artifact
                doc_filename = old_filename
            else:
                doc_filename = assign_doc_filename(
                    used_files=used_files,
                    artifact_id=child_artifact,
                    preferred_stem=preferred_stem,
                )
        else:
            module_id = child.get("module_id") or title.lower().replace(" ", "_")
            path = child.get("path", module_id)
            title_to_use = child.get("title", title)
            child_artifact = f"module:{module_id}"
            doc_filename = assign_doc_filename(
                used_files=used_files,
                artifact_id=child_artifact,
                preferred_stem=path or module_id,
            )

        children[title] = {
            "module_id": module_id,
            "title": title_to_use,
            "path": path,
            "description": child.get("description", ""),
            "_doc_filename": doc_filename,
            "components": list(child.get("components", [])),
            "children": {},
        }
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_tree_refiner.py -v`
Expected: PASS (the new test plus all previous Plan 1 tests).

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/tree_refiner.py tests/test_tree_refiner.py
git commit -m "feat(refinement): identity reuse in refine_one_node"
```

---

## Task 7: Identity reuse for split scenario in `refine_one_node`

When one old child is split into multiple new children (none of which are dominant under the normal-direction overlap), the spec says: the dominant successor (using `/|old|` formula) inherits the old identity, others get fresh ids.

**Files:**
- Modify: `codewiki/src/be/tree_refiner.py`
- Test: `tests/test_tree_refiner.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tree_refiner.py`:

```python
@pytest.mark.asyncio
async def test_refine_one_node_split_successor_inherits_identity(cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    # Old: one big "Auth" child with components a..d
    # New: split into AuthCore (a,b,c) and AuthExtras (d,e,f)
    # AuthCore should inherit "auth" identity (3/4 = 0.75 of old, dominant)
    save_refinement_payload(
        cache_dir,
        "root",
        {
            "children": {
                "Auth": {
                    "module_id": "auth",
                    "title": "Auth",
                    "path": "auth",
                    "_doc_filename": "auth.md",
                    "components": ["a.py::A", "b.py::B", "c.py::C", "d.py::D"],
                    "children": {},
                },
            }
        },
    )

    components = {
        "a.py::A": _node("a.py::A", "a.py"),
        "b.py::B": _node("b.py::B", "b.py"),
        "c.py::C": _node("c.py::C", "c.py"),
        "d.py::D": _node("d.py::D", "d.py"),
        "e.py::E": _node("e.py::E", "e.py"),
        "f.py::F": _node("f.py::F", "f.py"),
    }
    middleware = MagicMock()
    middleware.call = AsyncMock(
        return_value=MagicMock(
            text=json.dumps(
                {
                    "should_split": True,
                    "children": {
                        "AuthCore": {
                            "module_id": "auth_core",
                            "title": "AuthCore",
                            "path": "auth_core",
                            "description": ".",
                            "components": ["a.py::A", "b.py::B", "c.py::C"],
                        },
                        "AuthExtras": {
                            "module_id": "auth_extras",
                            "title": "AuthExtras",
                            "path": "auth_extras",
                            "description": ".",
                            "components": ["d.py::D", "e.py::E", "f.py::F"],
                        },
                    },
                }
            ),
            model="fake",
        )
    )
    cfg = RefinementConfig(
        max_depth=2,
        min_components_for_split=2,
        min_distinct_files_for_split=2,
        identity_reuse_threshold=0.70,
    )
    used: dict[str, str] = {}
    children = await refine_one_node(
        parent_doc_id="root",
        parent_title="Root",
        parent_path="root",
        component_ids=list(components.keys()),
        components=components,
        current_depth=1,
        refinement_cfg=cfg,
        output_language="en",
        cluster_model="c",
        middleware=middleware,
        cache_manager=cache,
        cache_dir=cache_dir,
        used_files=used,
    )

    # AuthCore overlap with old Auth: 3/3 = 1.0 (new-normalized) ← dominant via find_dominant_match
    # In this case the normal path already wins; the split-successor branch
    # only kicks in when no new node has high enough new-normalized overlap.
    assert children["AuthCore"]["module_id"] == "auth"
    assert children["AuthCore"]["_doc_filename"] == "auth.md"
    # AuthExtras has zero overlap → fresh identity
    assert children["AuthExtras"]["module_id"] == "auth_extras"


@pytest.mark.asyncio
async def test_refine_one_node_split_successor_kicks_in_when_normal_match_fails(cache_dir):
    """Old node with 5 comps. New: 2 groups of 2 and 3 each — neither has high
    enough new-normalized overlap (max 3/3 = 1.0 actually...). Make new groups
    bigger so new-normalized overlap drops below threshold but old-normalized
    is still dominant.
    """
    cache = CacheManager(cache_dir, flush_interval=60)
    save_refinement_payload(
        cache_dir,
        "root",
        {
            "children": {
                "Auth": {
                    "module_id": "auth",
                    "title": "Auth",
                    "path": "auth",
                    "_doc_filename": "auth.md",
                    "components": ["a", "b", "c", "d"],  # 4 comps
                    "children": {},
                },
            }
        },
    )

    components = {
        cid: _node(cid, f"{cid}.py")
        for cid in ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
    }
    middleware = MagicMock()
    # AuthMega has a..d + e..j (10 comps), so new-normalized = 4/10 = 0.4 (below 0.70)
    # but old-normalized = 4/4 = 1.0 → split-successor wins
    middleware.call = AsyncMock(
        return_value=MagicMock(
            text=json.dumps(
                {
                    "should_split": True,
                    "children": {
                        "AuthMega": {
                            "module_id": "auth_mega",
                            "title": "AuthMega",
                            "path": "auth_mega",
                            "description": ".",
                            "components": list("abcdefghij"),
                        },
                    },
                }
            ),
            model="fake",
        )
    )
    cfg = RefinementConfig(
        max_depth=2,
        min_components_for_split=2,
        min_distinct_files_for_split=2,
        identity_reuse_threshold=0.70,
    )
    used: dict[str, str] = {}
    children = await refine_one_node(
        parent_doc_id="root",
        parent_title="Root",
        parent_path="root",
        component_ids=list(components.keys()),
        components=components,
        current_depth=1,
        refinement_cfg=cfg,
        output_language="en",
        cluster_model="c",
        middleware=middleware,
        cache_manager=cache,
        cache_dir=cache_dir,
        used_files=used,
    )

    # Split-successor branch should reuse "auth" identity for AuthMega.
    assert children["AuthMega"]["module_id"] == "auth"
```

- [ ] **Step 2: Run, confirm second test fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_tree_refiner.py::test_refine_one_node_split_successor_kicks_in_when_normal_match_fails -v`
Expected: FAIL — current code only does normal-direction matching.

- [ ] **Step 3: Add split-successor fallback**

In `refine_one_node`, after the normal-direction match loop completes (and `available_old` still contains unmatched old siblings), add a second pass:

```python
from codewiki.src.be.identity_reuse import find_split_successor

# ... after normal-direction matching loop:

    # Second pass: split successor.
    # For each remaining old sibling, ask "is one new node a dominant successor of you?"
    for old_key in list(available_old.keys()):
        old_info = available_old[old_key]
        old_components = set(old_info.get("components") or [])
        # Build a view of new groups that don't yet have an inherited identity.
        new_groups_for_split = {
            title: {"components": info["components"]}
            for title, info in children.items()
            if info.get("module_id") != old_info.get("module_id")  # not already reused
            and info.get("module_id", "").endswith(("_x_", ""))  # placeholder
        }
        # Simpler: only consider children that did NOT get a match — track them.
        # See refactor below.
```

Actually, the cleaner way is to track which `children` dict entries got identity reuse vs fresh. Refactor `refine_one_node` to keep a separate `unmatched_children` list during the first pass, then run the split-successor pass over them. The diff is:

```python
    children: dict[str, Any] = {}
    unmatched_titles: list[str] = []  # titles whose entries got fresh ids in pass 1

    for title, child in children_raw.items():
        new_components = set(child.get("components") or [])
        match = find_dominant_match(
            new_components,
            available_old,
            threshold=refinement_cfg.identity_reuse_threshold,
            margin=0.15,
        )
        if match is not None:
            # ... existing inheritance code ...
            children[title] = { ... }  # with inherited ids
        else:
            unmatched_titles.append(title)
            # Build a placeholder; we'll patch it in the split-successor pass
            module_id = child.get("module_id") or title.lower().replace(" ", "_")
            children[title] = {
                "module_id": module_id,
                "title": child.get("title", title),
                "path": child.get("path", module_id),
                "description": child.get("description", ""),
                "_doc_filename": "",  # assigned later
                "components": list(child.get("components", [])),
                "children": {},
            }

    # Pass 2: split-successor for any remaining unmatched new node.
    # For each remaining old sibling, see if one of the unmatched new nodes
    # is a dominant successor (old-normalized overlap).
    for old_key in list(available_old.keys()):
        old_info = available_old[old_key]
        old_components = set(old_info.get("components") or [])
        candidate_groups = {
            t: {"components": children[t]["components"]} for t in unmatched_titles
        }
        successor_title = find_split_successor(
            old_components,
            candidate_groups,
            threshold=refinement_cfg.identity_reuse_threshold,
            margin=0.15,
        )
        if successor_title is None:
            continue
        # Inherit old identity into successor.
        children[successor_title]["module_id"] = old_info.get("module_id", "")
        children[successor_title]["path"] = old_info.get("path", "")
        old_filename = old_info.get("_doc_filename")
        if old_filename:
            child_artifact = f"module:{children[successor_title]['module_id']}"
            used_files[old_filename] = child_artifact
            children[successor_title]["_doc_filename"] = old_filename
        unmatched_titles.remove(successor_title)
        available_old.pop(old_key, None)

    # Pass 3: assign fresh _doc_filename for any remaining unmatched titles.
    for title in unmatched_titles:
        info = children[title]
        child_artifact = f"module:{info['module_id']}"
        info["_doc_filename"] = assign_doc_filename(
            used_files=used_files,
            artifact_id=child_artifact,
            preferred_stem=info["path"] or info["module_id"],
        )
```

- [ ] **Step 4: Run all tree refiner tests**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_tree_refiner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/tree_refiner.py tests/test_tree_refiner.py
git commit -m "feat(refinement): split successor identity inheritance"
```

---

## Task 8: Apply identity reuse to top-level clustering

Stage 3 currently has its own freeze rule. It lives in **`codewiki/src/be/clustering/pipeline.py`**, not in `codewiki/src/be/cluster_modules.py`. The function is `_apply_naming_freeze(clusters, names, previous_tree)` at line 211, and it is called from the main clustering pipeline at line 91. `cluster_modules.py` is a thin wrapper that eventually delegates into `clustering/pipeline.py`.

Replace `_apply_naming_freeze` with a call to the unified `identity_reuse.find_dominant_match` so there is **one** identity-reuse implementation across the codebase.

**Files:**
- Modify: `codewiki/src/be/clustering/pipeline.py`
- Test: `tests/test_clustering_pipeline.py`

- [ ] **Step 1: Read the current freeze implementation**

Read `codewiki/src/be/clustering/pipeline.py` lines 211–260 (`_apply_naming_freeze`) and lines 263–278 (`_index_previous_tree`). Key observations before touching anything:

- The freeze operates on `clusters: list[list[str]]` (member component id lists) and `names: list[dict]` (naming results, parallel to `clusters`).
- It computes `module_id_from_members(cluster)` (a deterministic hash of sorted members) and reuses old title/path/description **only when the module_id matches exactly** — i.e. the old freeze rule is "exact member set" and nothing more.
- It returns an updated `names` list with `frozen_path` carried through; the downstream code at line 101 uses `naming.get("frozen_path") or _compute_module_path(...)` to actually assign the path.

The new behavior we want:

- Exact-set match → still wins (that's what `find_dominant_match` does in its first bullet).
- Dominant-but-not-exact match (e.g. one member added or removed) → reuse old identity if `overlap_ratio >= identity_reuse_threshold` **and** `overlap_ratio − second_best >= 0.15`.
- No dominant match → fall through to the LLM/heuristic `names` entry unchanged.

Keep the output shape (`list[dict]` with optional `frozen_path`) so the rest of `_build_children_nodes` around lines 93–150 works without any caller change.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_clustering_pipeline.py`:

```python
def test_apply_naming_freeze_reuses_old_identity_on_dominant_overlap():
    """Old Backend had 4 members; new cluster has 3 of those 4 plus 1 new
    member. Overlap 3/4 = 0.75, margin vs anything else is large → reuse
    Backend's title/path under the unified identity-reuse rule.
    """
    from codewiki.src.be.clustering.pipeline import _apply_naming_freeze

    # New clusters (members only)
    clusters = [
        ["f0.py::C0", "f1.py::C1", "f2.py::C2", "f_new.py::C_new"],
    ]
    names = [
        {"cluster_idx": 0, "title": "Inferred Title", "description": "x"},
    ]
    previous_tree = {
        "Backend": {
            "title": "Backend",
            "path": "backend",
            "components": ["f0.py::C0", "f1.py::C1", "f2.py::C2", "f3.py::C3"],
            "children": {},
        },
        "Frontend": {
            "title": "Frontend",
            "path": "frontend",
            "components": ["f10.py::C10"],
            "children": {},
        },
    }
    frozen = _apply_naming_freeze(clusters, names, previous_tree)
    assert frozen[0]["title"] == "Backend"
    assert frozen[0]["frozen_path"] == "backend"


def test_apply_naming_freeze_no_dominant_match_keeps_llm_name():
    from codewiki.src.be.clustering.pipeline import _apply_naming_freeze

    clusters = [
        ["f0.py::C0", "f1.py::C1", "f2.py::C2", "f3.py::C3"],
    ]
    names = [
        {"cluster_idx": 0, "title": "Fresh Name", "description": "x"},
    ]
    previous_tree = {
        "OldA": {
            "title": "OldA",
            "path": "old_a",
            "components": ["f0.py::C0", "f_other.py::C_other"],
            "children": {},
        },
    }
    # Only 1/4 overlap → below 0.70 → no reuse
    frozen = _apply_naming_freeze(clusters, names, previous_tree)
    assert frozen[0]["title"] == "Fresh Name"
    assert "frozen_path" not in frozen[0] or frozen[0].get("frozen_path") in ("", None)


def test_apply_naming_freeze_exact_match_still_wins():
    from codewiki.src.be.clustering.pipeline import _apply_naming_freeze

    members = ["f0.py::C0", "f1.py::C1"]
    clusters = [members]
    names = [{"cluster_idx": 0, "title": "LLM Name", "description": "x"}]
    previous_tree = {
        "Backend": {
            "title": "Backend",
            "path": "backend",
            "components": members,
            "children": {},
        },
    }
    frozen = _apply_naming_freeze(clusters, names, previous_tree)
    assert frozen[0]["title"] == "Backend"
    assert frozen[0]["frozen_path"] == "backend"


def test_apply_naming_freeze_two_close_candidates_rejected():
    from codewiki.src.be.clustering.pipeline import _apply_naming_freeze

    clusters = [
        ["a", "b", "c", "d"],
    ]
    names = [{"cluster_idx": 0, "title": "LLM Name", "description": "x"}]
    previous_tree = {
        "OldA": {
            "title": "OldA",
            "path": "old_a",
            "components": ["a", "b", "c"],  # 3/4 = 0.75
            "children": {},
        },
        "OldB": {
            "title": "OldB",
            "path": "old_b",
            "components": ["a", "b", "d"],  # 3/4 = 0.75
            "children": {},
        },
    }
    # Both 0.75, margin 0.0 → no reuse; keep LLM name
    frozen = _apply_naming_freeze(clusters, names, previous_tree)
    assert frozen[0]["title"] == "LLM Name"
```

- [ ] **Step 3: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_clustering_pipeline.py -v -k "_apply_naming_freeze"`
Expected: `test_apply_naming_freeze_reuses_old_identity_on_dominant_overlap` FAILS because the current freeze only reuses on exact module_id match (i.e. exact member set).

- [ ] **Step 4: Rewrite `_apply_naming_freeze` to use `find_dominant_match`**

Open `codewiki/src/be/clustering/pipeline.py`. Replace the body of `_apply_naming_freeze` (line 211–260) with:

```python
def _apply_naming_freeze(
    clusters: list[list[str]],
    names: list[dict],
    previous_tree: dict | None,
    identity_reuse_threshold: float = 0.70,
    identity_reuse_margin: float = 0.15,
) -> list[dict]:
    """Reuse old title/path when a new cluster has dominant overlap with an old node.

    Uses the unified ``identity_reuse.find_dominant_match`` rule (Plan 3):
      - exact component-set match always wins
      - otherwise overlap_ratio must be ``>= identity_reuse_threshold`` AND
        ``(overlap - second_best) >= identity_reuse_margin``

    Output shape matches the pre-Plan-3 freeze: a list parallel to ``clusters``
    where each entry may include ``title``, ``description``, and ``frozen_path``.
    """
    if not previous_tree:
        return names

    from codewiki.src.be.identity_reuse import find_dominant_match

    # Flatten previous tree into a dict of {old_key: old_info} at all depths.
    # Top-level clustering only sees top-level; but the previous tree may have
    # been written at any depth, so flatten defensively.
    old_siblings: dict[str, dict] = {}

    def _collect(subtree: dict) -> None:
        for key, info in subtree.items():
            if not isinstance(info, dict):
                continue
            if info.get("components"):
                old_siblings[key] = {
                    "module_id": info.get("module_id") or key,
                    "title": info.get("title", key),
                    "path": info.get("path", ""),
                    "components": info.get("components", []),
                    "description": info.get("description", ""),
                }
            children = info.get("children") or {}
            if children:
                _collect(children)

    _collect(previous_tree)

    available = dict(old_siblings)
    result = []
    frozen_count = 0
    for cluster, naming in zip(clusters, names):
        match = find_dominant_match(
            set(cluster),
            available,
            threshold=identity_reuse_threshold,
            margin=identity_reuse_margin,
        )
        if match is not None:
            old_info = available.pop(match.old_key)
            result.append(
                {
                    "cluster_idx": naming.get("cluster_idx", 0),
                    "title": match.old_title,
                    "description": old_info.get("description")
                    or naming.get("description", ""),
                    "frozen_path": match.old_path,
                }
            )
            frozen_count += 1
        else:
            result.append(naming)

    if frozen_count:
        logger.info(
            "Identity reuse (top-level): reused %d/%d module names from previous tree",
            frozen_count,
            len(clusters),
        )

    return result
```

Note that `_index_previous_tree` and `module_id_from_members` are no longer needed by this function. Leave `_index_previous_tree` alone if other callers still reference it (search with Grep first); if nothing references it, delete the function to avoid dead code.

- [ ] **Step 5: Thread the configured threshold into the call site**

At line 91 the caller is:

```python
frozen_names = _apply_naming_freeze(clusters, names, current_module_tree)
```

Change to:

```python
frozen_names = _apply_naming_freeze(
    clusters,
    names,
    current_module_tree,
    identity_reuse_threshold=getattr(
        getattr(config, "refinement", None), "identity_reuse_threshold", 0.70
    ),
)
```

This makes the threshold configurable via `CodeWikiConfig.refinement.identity_reuse_threshold` introduced in Plan 1 Task 2. Hard-coded `0.15` margin stays in the function default — it's shared with `find_dominant_match` everywhere else.

- [ ] **Step 6: Run the test**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_clustering_pipeline.py -v -k "_apply_naming_freeze"`
Expected: PASS (4 tests).

- [ ] **Step 7: Run all clustering tests**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_clustering_pipeline.py -v`
Expected: PASS. Any pre-existing test that asserted exact-member-set-only reuse behavior must be updated to reflect the new dominant-overlap rule, not papered over with exact-match fixtures.

- [ ] **Step 8: Commit**

```bash
git add codewiki/src/be/clustering/pipeline.py tests/test_clustering_pipeline.py
git commit -m "refactor(refinement): top-level freeze uses unified identity reuse"
```

> **Why not rip out `_apply_naming_freeze` entirely and inline it?** Because `clustering/pipeline.py` wires its result into `frozen_path` at line 101, and keeping the function boundary lets us test it in isolation (the tests above) without spinning up the entire clustering pipeline with its Leiden + LLM machinery.

---

## Task 9: Final integration

- [ ] **Step 1: Run the full test suite**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/ -q 2>&1 | tail -20`
Expected: all tests pass.

- [ ] **Step 2: Tag the milestone**

```bash
git tag tree-refinement-plan-3-complete
```

---

## Acceptance Criteria for Plan 3

1. `identity_reuse.py` exists with `IdentityMatch`, `match_overlap`, `find_dominant_match`, `find_split_successor`, `find_merge_predecessor`.
2. Every matching function applies the spec's `>= threshold AND >= margin (0.15)` rule.
3. `find_split_successor` uses `|new ∩ old| / |old|`; `find_merge_predecessor` uses `|new ∩ old| / |new|`.
4. `refine_one_node` calls identity reuse before assigning fresh ids; matched children inherit `module_id`, `path`, and `_doc_filename` from the old subtree.
5. Top-level `cluster_modules` uses the same `find_dominant_match` mechanism — no separate freeze rule remains.
6. Two-pass identity reuse: pass 1 normal direction, pass 2 split-successor fallback for unmatched new nodes.
7. AC 8 (Cluster/title/path reuse is stable when overlap remains high) is verified by `test_refine_one_node_reuses_identity_from_previous_run` and `test_top_level_clustering_uses_identity_reuse`.
8. All previously-passing tests still pass.

---

## Self-Review Notes

**Spec coverage check:**
- ✅ §Matching strategy (exact + dominant overlap + margin) — Tasks 1, 2
- ✅ §Split (`|new ∩ old| / |old|`) — Task 3, 7
- ✅ §Merge (`|new ∩ old| / |new|`) — Task 4
- ✅ §What gets reused (module_id, path, title) — Tasks 6, 8
- ✅ Stage 3 unification (top-level uses same mechanism) — Task 8
- ❌ "Descriptions may be lightly refreshed" — not implemented; we always preserve the new description from the LLM, which is also valid per spec ("preserved as-is, or lightly refreshed")
- ❌ Identity-reuse failure as a hard rerun trigger — Plan 5

**Type/name consistency:** `IdentityMatch`, `find_dominant_match`, `find_split_successor`, `find_merge_predecessor` all match across tasks. `refine_one_node` signature unchanged from Plan 1.

**Placeholder scan:** none.
