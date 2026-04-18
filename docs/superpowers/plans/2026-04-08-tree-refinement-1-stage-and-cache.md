# Tree Refinement Phase 1: TreeRefinementStage + Refinement Cache

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a dedicated `TreeRefinementStage` that runs between `ClusteringStage` and `StateInitStage`, recursively refines the top-level tree to `max_depth`, assigns frozen `_doc_filename` values, and caches subtree refinement results as `refinement:{doc_id}` artifacts. Existing runtime sub-module generation in agent tools is left intact as a fallback for this phase.

**Architecture:** A new pipeline stage that consumes `ctx.module_tree` (top level only, from `ClusteringStage`) and produces a fully-refined tree which it writes back to `ctx.module_tree`. Refinement results are persisted as JSON files under `.codewiki/_refinement/<normalized_doc_id>.json` and tracked in `cache_manager` as `refinement:{doc_id}` entries with explicit input hashes. Filename collision resolution happens during this stage so subsequent stages see frozen names.

**Tech Stack:** Python 3.10+, pydantic-ai, asyncio, pytest, existing CodeWiki internals (`pipeline.PipelineStage`, `cache_manager.CacheManager`, `llm_middleware.LLMMiddleware`).

**Spec reference:** `docs/superpowers/specs/2026-04-07-tree-refinement-generation-design.md` — sections: Stage 4 (TreeRefinementStage), Frozen Tree Schema, Refinement cache artifacts, Tree Refinement Stage / Output / Recursion Rule, Split Criteria.

**Out of scope for Plan 1:**
- Removing the existing `generate_sub_module_documentation` agent tool (Plan 2)
- Identity reuse logic — Plan 1 just creates fresh `module_id`/`path`/`title` for new nodes (Plan 3)
- Parent doc segment cache (Plan 4)
- Incremental thresholds, resume semantics, orphan cleanup, schema bump (Plan 5)

---

## File Structure

### New files

| Path | Responsibility |
|------|----------------|
| `codewiki/src/be/stages/tree_refinement.py` | `TreeRefinementStage` class — pipeline stage adapter; delegates to `tree_refiner` for actual logic |
| `codewiki/src/be/tree_refiner.py` | Pure refinement logic: split decision, recursion, LLM call orchestration, filename assignment, refinement cache integration |
| `codewiki/src/be/refinement_cache.py` | Helpers: `refinement_artifact_id`, `normalized_doc_id`, `refinement_output_path`, `compute_refinement_input_hash`, `load_refinement_payload`, `save_refinement_payload` |
| `tests/test_refinement_cache.py` | Unit tests for the helpers in `refinement_cache.py` |
| `tests/test_tree_refiner.py` | Unit tests for split decision and recursive refinement (LLM mocked) |
| `tests/test_tree_refinement_stage.py` | Integration test: stage in pipeline, end-to-end mocked LLM |

### Modified files

| Path | Change |
|------|--------|
| `codewiki/src/codewiki_config.py` | Add `RefinementConfig` nested model + field on `CodeWikiConfig` |
| `codewiki/src/config_loader.py` | Extend `_build_codewiki_config` (around line 221–310) to read a `[refinement]` TOML section and build a `RefinementConfig`. Without this, the new fields are only reachable by constructing `CodeWikiConfig` in Python — TOML and CLI paths will silently drop them. |
| `codewiki/src/config.py` | Add `REFINEMENT_DIR = "_refinement"` constant |
| `codewiki/src/be/prompt_template.py` | Add `REFINEMENT_PROMPT_VERSION` constant + `format_refinement_prompt(...)` helper |
| `codewiki/src/be/stages/__init__.py` | Insert `TreeRefinementStage` in `DEFAULT_STAGES` after `ClusteringStage`, before `StateInitStage` |
| `codewiki/src/be/documentation_generator.py` | Stop calling `freeze_doc_filenames` inside `_cluster_modules`; refinement stage owns filename assignment now. Save final tree after refinement, not after clustering. |
| `codewiki/src/be/documentation_tree_utils.py` | `build_generation_tasks` emits `kind="module"` for every non-root node; only the synthetic root task keeps `kind="overview"`. See Task 15b for rationale (unifies parent artifact namespace so Plan 4's segment pipeline and StateInitStage's planning do not disagree). |
| `tests/test_documentation_generator_helpers.py` | Update `test_cluster_modules_uses_cached_tree_when_commit_matches` and friends to no longer assert filename freeze inside clustering |

### Test fixture pattern (used throughout)

All new test files use the `tmp_path` pytest fixture and a `_make_config(tmp_path)` helper. LLM calls are mocked with `unittest.mock.patch` of `LLMMiddleware.call` returning a `MagicMock` whose `text` attribute holds canned JSON. This matches the existing pattern in `tests/test_clustering_pipeline.py`.

---

## Task 0: Sanity check current pipeline before changes

- [ ] **Step 1: Run the existing test suite**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/ -x -q 2>&1 | tail -30`
Expected: all tests pass (1319+ tests). If any fail, fix or document them before proceeding — Plan 1 must start from a green baseline.

- [ ] **Step 2: Note the current `DEFAULT_STAGES` order**

Read `codewiki/src/be/stages/__init__.py` and confirm the order is: `GraphBuildStage`, `IndexBuildStage`, `ClusteringStage`, `StateInitStage`, `ModuleGenerationStage`, `GuideStage`, `PostprocessStage`, `MetadataStage`. If different, update Task 11 below to match.

- [ ] **Step 3: Commit a baseline marker**

```bash
cd /home/dengqi/Source/langs/python/CodeWiki
git status
git log --oneline -1
```

No commit needed — just record the baseline commit hash so reverts are easy if needed.

---

## Task 1: Add `REFINEMENT_DIR` constant

**Files:**
- Modify: `codewiki/src/config.py`
- Test: `tests/test_refinement_cache.py` (created in Task 3)

- [ ] **Step 1: Add the constant**

Open `codewiki/src/config.py` and add near the other directory constants:

```python
REFINEMENT_DIR = "_refinement"
```

- [ ] **Step 2: Confirm it's importable**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run python -c "from codewiki.src.config import REFINEMENT_DIR; print(REFINEMENT_DIR)"`
Expected: `_refinement`

- [ ] **Step 3: Commit**

```bash
git add codewiki/src/config.py
git commit -m "feat(refinement): add REFINEMENT_DIR constant"
```

---

## Task 2: Add `RefinementConfig` to `CodeWikiConfig`

**Files:**
- Modify: `codewiki/src/codewiki_config.py`
- Test: `tests/test_codewiki_config_refinement.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_codewiki_config_refinement.py`:

```python
from codewiki.src.codewiki_config import CodeWikiConfig, RefinementConfig


def test_refinement_config_defaults():
    cfg = CodeWikiConfig(
        repo_path="/tmp/repo",
        docs_dir="/tmp/docs",
        llm_base_url="http://localhost",
        llm_api_key="x",
        main_model="m",
        cluster_model="c",
    )
    assert cfg.refinement.max_depth == 3
    assert cfg.refinement.min_components_for_split == 6
    assert cfg.refinement.min_distinct_files_for_split == 4
    assert cfg.refinement.max_cluster_components == 1000
    assert cfg.refinement.identity_reuse_threshold == 0.70


def test_refinement_config_override():
    cfg = CodeWikiConfig(
        repo_path="/tmp/repo",
        docs_dir="/tmp/docs",
        llm_base_url="http://localhost",
        llm_api_key="x",
        main_model="m",
        cluster_model="c",
        refinement=RefinementConfig(
            max_depth=5,
            min_components_for_split=10,
            min_distinct_files_for_split=6,
            max_cluster_components=500,
            identity_reuse_threshold=0.80,
        ),
    )
    assert cfg.refinement.max_depth == 5
    assert cfg.refinement.min_components_for_split == 10
```

- [ ] **Step 2: Run test, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_codewiki_config_refinement.py -v`
Expected: FAIL (`ImportError: cannot import name 'RefinementConfig'`)

- [ ] **Step 3: Implement `RefinementConfig`**

Open `codewiki/src/codewiki_config.py`. Near the top with other nested models, add:

```python
class RefinementConfig(BaseModel):
    """Tree refinement stage configuration. See spec §Split Criteria."""

    max_depth: int = 3
    min_components_for_split: int = 6
    min_distinct_files_for_split: int = 4
    max_cluster_components: int = 1000
    identity_reuse_threshold: float = 0.70
```

In the `CodeWikiConfig` class body, add:

```python
    refinement: RefinementConfig = Field(default_factory=RefinementConfig)
```

- [ ] **Step 4: Run test, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_codewiki_config_refinement.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Make sure existing config tests still pass**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_cli_generate_config_file.py tests/test_codewiki_config_refinement.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add codewiki/src/codewiki_config.py tests/test_codewiki_config_refinement.py
git commit -m "feat(refinement): add RefinementConfig with default split thresholds"
```

> **Note on `max_depth`:** `CodeWikiConfig.max_depth` (top-level) already exists and is used by clustering. Do **not** delete it. `cfg.refinement.max_depth` is the new authoritative value for refinement; the top-level field stays for backward compat and is no longer consulted by the new stage. A future cleanup can deprecate the top-level field, but not in this plan.

---

## Task 2b: Wire `RefinementConfig` through `config_loader.py`

**Motivation.** Task 2 only touched the `CodeWikiConfig` dataclass. The real TOML→config path lives in `codewiki/src/config_loader.py:221–310` (`_build_codewiki_config`). It currently hand-maps every field from the parsed TOML into `CodeWikiConfig(...)`. Without extending it, a user's `config.toml` with a `[refinement]` section is silently ignored — TreeRefinementStage will always see the hard-coded defaults. Lock this down now so downstream tasks can assume TOML-configured thresholds actually reach the stage.

**Files:**
- Modify: `codewiki/src/config_loader.py`
- Test: `tests/test_config_loader_refinement.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_loader_refinement.py`:

```python
import textwrap
from pathlib import Path

from codewiki.src.config_loader import load_config


def _write_config(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_refinement_section_loads_from_toml(tmp_path):
    config_path = _write_config(
        tmp_path,
        """
        [runtime]
        output_dir = "docs"
        max_depth = 2

        [generation]
        main_model = "m"
        cluster_model = "c"

        [refinement]
        max_depth = 4
        min_components_for_split = 8
        min_distinct_files_for_split = 5
        max_cluster_components = 800
        identity_reuse_threshold = 0.85
        """,
    )
    cfg = load_config(str(config_path), str(tmp_path), resolve_secrets=False)
    assert cfg.refinement.max_depth == 4
    assert cfg.refinement.min_components_for_split == 8
    assert cfg.refinement.min_distinct_files_for_split == 5
    assert cfg.refinement.max_cluster_components == 800
    assert cfg.refinement.identity_reuse_threshold == 0.85


def test_refinement_section_absent_uses_defaults(tmp_path):
    config_path = _write_config(
        tmp_path,
        """
        [runtime]
        output_dir = "docs"

        [generation]
        main_model = "m"
        cluster_model = "c"
        """,
    )
    cfg = load_config(str(config_path), str(tmp_path), resolve_secrets=False)
    assert cfg.refinement.max_depth == 3
    assert cfg.refinement.identity_reuse_threshold == 0.70
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_config_loader_refinement.py -v`
Expected: FAIL — `cfg.refinement.max_depth` is still `3` (default) even after the TOML set it to `4`, because the loader drops the section.

- [ ] **Step 3: Extend `_build_codewiki_config`**

Open `codewiki/src/config_loader.py`. Near the top of `_build_codewiki_config` (around line 229–232), add:

```python
    refinement_section = cast(dict[str, Any], data.get("refinement", {}))
```

Import `RefinementConfig` at the top of the file:

```python
from codewiki.src.codewiki_config import (
    CodeWikiConfig,
    PostprocessConfig,
    ProviderConfig,
    RefinementConfig,
)
```

Build the config object from the section (right before the `return CodeWikiConfig(...)` call):

```python
    refinement_cfg = RefinementConfig(
        max_depth=int(refinement_section.get("max_depth", 3)),
        min_components_for_split=int(
            refinement_section.get("min_components_for_split", 6)
        ),
        min_distinct_files_for_split=int(
            refinement_section.get("min_distinct_files_for_split", 4)
        ),
        max_cluster_components=int(
            refinement_section.get("max_cluster_components", 1000)
        ),
        identity_reuse_threshold=float(
            refinement_section.get("identity_reuse_threshold", 0.70)
        ),
    )
```

In the `return CodeWikiConfig(...)` call, add:

```python
        refinement=refinement_cfg,
```

Place the new kwarg near the other nested models (e.g., after `postprocess=postprocess_config,`).

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_config_loader_refinement.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run existing loader tests to confirm no regression**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_cli_generate_config_file.py tests/test_cli_generate_command.py -v`
Expected: PASS. If any existing test constructs a `CodeWikiConfig` that now requires `refinement`, it should still work because `refinement` has a default_factory.

- [ ] **Step 6: Commit**

```bash
git add codewiki/src/config_loader.py tests/test_config_loader_refinement.py
git commit -m "feat(refinement): load [refinement] section from TOML config"
```

---

## Task 3: `refinement_cache.py` — id helpers

**Files:**
- Create: `codewiki/src/be/refinement_cache.py`
- Test: `tests/test_refinement_cache.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_refinement_cache.py`:

```python
import json
import os

import pytest

from codewiki.src.be.refinement_cache import (
    refinement_artifact_id,
    normalized_doc_id,
    refinement_output_path,
)


def test_refinement_artifact_id_adds_prefix():
    assert refinement_artifact_id("auth_layer") == "refinement:auth_layer"


def test_refinement_artifact_id_idempotent():
    assert refinement_artifact_id("refinement:auth_layer") == "refinement:auth_layer"


def test_refinement_artifact_id_root():
    assert refinement_artifact_id("root") == "refinement:root"


@pytest.mark.parametrize(
    "doc_id,expected",
    [
        ("auth_layer", "auth_layer"),
        ("Backend Services & Integrations", "backend_services_and_integrations"),
        ("auth/layer", "auth_layer"),
        ("Auth.Layer", "auth_layer"),
        ("a" * 200, "a" * 120),
    ],
)
def test_normalized_doc_id(doc_id, expected):
    assert normalized_doc_id(doc_id) == expected


def test_refinement_output_path(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    path = refinement_output_path(str(cache_dir), "Backend Services")
    assert path.endswith(os.path.join("_refinement", "backend_services.json"))
    assert path.startswith(str(cache_dir))
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_refinement_cache.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement helpers**

Create `codewiki/src/be/refinement_cache.py`:

```python
"""Helpers for the refinement:{doc_id} cache artifact type."""

from __future__ import annotations

import json
import os
import re

from codewiki.src.config import REFINEMENT_DIR

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_MAX_NORMALIZED_LEN = 120


def refinement_artifact_id(doc_id: str) -> str:
    """Return the cache artifact id for a refinement entry."""
    if doc_id.startswith("refinement:"):
        return doc_id
    return f"refinement:{doc_id}"


def normalized_doc_id(doc_id: str) -> str:
    """Filesystem-safe lower-snake-case identifier for a doc id.

    Truncates to ``_MAX_NORMALIZED_LEN`` characters to avoid platform path
    limits. Two distinct doc ids that collide post-normalization are caller's
    problem — refinement uses globally-unique doc ids by construction.
    """
    lowered = doc_id.lower()
    cleaned = _NORMALIZE_RE.sub("_", lowered).strip("_")
    return cleaned[:_MAX_NORMALIZED_LEN]


def refinement_output_path(cache_dir: str, doc_id: str) -> str:
    """Return the absolute JSON output path for a refinement subtree."""
    return os.path.join(cache_dir, REFINEMENT_DIR, f"{normalized_doc_id(doc_id)}.json")
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_refinement_cache.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/refinement_cache.py tests/test_refinement_cache.py
git commit -m "feat(refinement): add refinement artifact id and output path helpers"
```

---

## Task 4: `refinement_cache.py` — input hash

**Files:**
- Modify: `codewiki/src/be/refinement_cache.py`
- Test: `tests/test_refinement_cache.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_refinement_cache.py`:

```python
from codewiki.src.be.refinement_cache import compute_refinement_input_hash


def _make_node(component_id, source_code):
    from codewiki.src.be.dependency_analyzer.models.core import Node

    return Node(
        id=component_id,
        name=component_id.split("::")[-1],
        component_type="function",
        file_path=component_id.split("::")[0],
        relative_path=component_id.split("::")[0],
        source_code=source_code,
    )


def test_compute_refinement_input_hash_stable_for_same_inputs():
    components = {
        "a.py::Foo": _make_node("a.py::Foo", "def foo(): pass"),
        "a.py::Bar": _make_node("a.py::Bar", "def bar(): pass"),
    }
    h1 = compute_refinement_input_hash(
        component_ids=["a.py::Foo", "a.py::Bar"],
        components=components,
        current_depth=1,
        max_depth=3,
        min_components_for_split=6,
        min_distinct_files_for_split=4,
        max_cluster_components=1000,
        identity_reuse_threshold=0.70,
        output_language="en",
    )
    h2 = compute_refinement_input_hash(
        component_ids=["a.py::Bar", "a.py::Foo"],  # different order
        components=components,
        current_depth=1,
        max_depth=3,
        min_components_for_split=6,
        min_distinct_files_for_split=4,
        max_cluster_components=1000,
        identity_reuse_threshold=0.70,
        output_language="en",
    )
    assert h1 == h2  # order independent


def test_compute_refinement_input_hash_changes_when_source_changes():
    base_kwargs = dict(
        component_ids=["a.py::Foo"],
        current_depth=1,
        max_depth=3,
        min_components_for_split=6,
        min_distinct_files_for_split=4,
        max_cluster_components=1000,
        identity_reuse_threshold=0.70,
        output_language="en",
    )
    h_old = compute_refinement_input_hash(
        components={"a.py::Foo": _make_node("a.py::Foo", "def foo(): pass")},
        **base_kwargs,
    )
    h_new = compute_refinement_input_hash(
        components={"a.py::Foo": _make_node("a.py::Foo", "def foo(): return 42")},
        **base_kwargs,
    )
    assert h_old != h_new


def test_compute_refinement_input_hash_changes_when_threshold_changes():
    components = {"a.py::Foo": _make_node("a.py::Foo", "def foo(): pass")}
    base_kwargs = dict(
        component_ids=["a.py::Foo"],
        components=components,
        current_depth=1,
        max_depth=3,
        min_components_for_split=6,
        min_distinct_files_for_split=4,
        max_cluster_components=1000,
        output_language="en",
    )
    h1 = compute_refinement_input_hash(identity_reuse_threshold=0.70, **base_kwargs)
    h2 = compute_refinement_input_hash(identity_reuse_threshold=0.80, **base_kwargs)
    assert h1 != h2
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_refinement_cache.py -v`
Expected: FAIL on the new tests with `ImportError`.

- [ ] **Step 3: Implement `compute_refinement_input_hash`**

Append to `codewiki/src/be/refinement_cache.py`:

```python
import hashlib

from codewiki.src.be.prompt_template import REFINEMENT_PROMPT_VERSION


def compute_refinement_input_hash(
    *,
    component_ids: list[str],
    components: dict,
    current_depth: int,
    max_depth: int,
    min_components_for_split: int,
    min_distinct_files_for_split: int,
    max_cluster_components: int,
    identity_reuse_threshold: float,
    output_language: str,
) -> str:
    """SHA256 of all inputs that should invalidate a refinement cache entry.

    See spec §Refinement cache artifacts.
    """
    h = hashlib.sha256()
    sorted_ids = sorted(component_ids)
    for cid in sorted_ids:
        h.update(b"\x00cid\x00")
        h.update(cid.encode("utf-8"))
        node = components.get(cid)
        source = (node.source_code or "") if node is not None else ""
        h.update(b"\x00src\x00")
        h.update(hashlib.sha256(source.encode("utf-8")).hexdigest().encode("ascii"))
    h.update(b"\x00depth\x00")
    h.update(str(current_depth).encode("ascii"))
    h.update(b"\x00max_depth\x00")
    h.update(str(max_depth).encode("ascii"))
    h.update(b"\x00min_comp\x00")
    h.update(str(min_components_for_split).encode("ascii"))
    h.update(b"\x00min_files\x00")
    h.update(str(min_distinct_files_for_split).encode("ascii"))
    h.update(b"\x00max_cluster\x00")
    h.update(str(max_cluster_components).encode("ascii"))
    h.update(b"\x00reuse\x00")
    h.update(f"{identity_reuse_threshold:.4f}".encode("ascii"))
    h.update(b"\x00lang\x00")
    h.update(output_language.encode("utf-8"))
    h.update(b"\x00prompt\x00")
    h.update(REFINEMENT_PROMPT_VERSION.encode("ascii"))
    return h.hexdigest()
```

This import will fail until Task 5 adds `REFINEMENT_PROMPT_VERSION`. That's fine — Task 5 lands the constant before we re-run.

- [ ] **Step 4: Stage but don't commit yet**

Skip running tests until Task 5 adds the constant.

---

## Task 5: `REFINEMENT_PROMPT_VERSION` constant

**Files:**
- Modify: `codewiki/src/be/prompt_template.py`

- [ ] **Step 1: Add the constant**

Open `codewiki/src/be/prompt_template.py` and near the existing `PROMPT_VERSION` line (line 5), add:

```python
REFINEMENT_PROMPT_VERSION = "refinement-v1"
```

- [ ] **Step 2: Run Task 4's tests now**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_refinement_cache.py -v`
Expected: PASS (all tests including the new hash tests).

- [ ] **Step 3: Commit Tasks 4 + 5 together**

```bash
git add codewiki/src/be/refinement_cache.py codewiki/src/be/prompt_template.py tests/test_refinement_cache.py
git commit -m "feat(refinement): add input hash and REFINEMENT_PROMPT_VERSION"
```

---

## Task 6: `refinement_cache.py` — payload load/save

**Files:**
- Modify: `codewiki/src/be/refinement_cache.py`
- Test: `tests/test_refinement_cache.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_refinement_cache.py`:

```python
from codewiki.src.be.refinement_cache import (
    load_refinement_payload,
    save_refinement_payload,
)


def test_save_and_load_refinement_payload_roundtrip(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    payload = {
        "module_id": "auth_layer",
        "title": "Auth Layer",
        "path": "auth_layer",
        "description": "Authentication and session management.",
        "_doc_filename": "auth_layer.md",
        "components": ["src/auth.py::AuthManager"],
        "children": {},
    }

    saved_path = save_refinement_payload(str(cache_dir), "auth_layer", payload)
    assert os.path.exists(saved_path)

    loaded = load_refinement_payload(str(cache_dir), "auth_layer")
    assert loaded == payload


def test_load_refinement_payload_returns_none_when_missing(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    assert load_refinement_payload(str(cache_dir), "missing") is None
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_refinement_cache.py::test_save_and_load_refinement_payload_roundtrip -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement load/save**

Append to `codewiki/src/be/refinement_cache.py`:

```python
def save_refinement_payload(cache_dir: str, doc_id: str, payload: dict) -> str:
    """Persist a refinement subtree to disk and return its path."""
    path = refinement_output_path(cache_dir, doc_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def load_refinement_payload(cache_dir: str, doc_id: str) -> dict | None:
    """Read a previously persisted refinement subtree, or None if missing/corrupt."""
    path = refinement_output_path(cache_dir, doc_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_refinement_cache.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/refinement_cache.py tests/test_refinement_cache.py
git commit -m "feat(refinement): persist refinement payloads as JSON"
```

---

## Task 7: Refinement prompt formatter

**Files:**
- Modify: `codewiki/src/be/prompt_template.py`
- Test: `tests/test_prompt_template_refinement.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_prompt_template_refinement.py`:

```python
from codewiki.src.be.prompt_template import (
    REFINEMENT_PROMPT_VERSION,
    format_refinement_prompt,
)


def test_format_refinement_prompt_includes_constraints():
    prompt = format_refinement_prompt(
        parent_title="Auth Layer",
        parent_path="auth_layer",
        components_block="component listing here",
        current_depth=1,
        max_depth=3,
        min_components_for_split=6,
        min_distinct_files_for_split=4,
        output_language="en",
    )
    assert "Auth Layer" in prompt
    assert "auth_layer" in prompt
    assert "component listing here" in prompt
    assert "max_depth" in prompt or "depth 3" in prompt
    assert "6" in prompt  # min_components
    assert "4" in prompt  # min_distinct_files


def test_format_refinement_prompt_respects_language():
    prompt_en = format_refinement_prompt(
        parent_title="Auth",
        parent_path="auth",
        components_block="x",
        current_depth=1,
        max_depth=2,
        min_components_for_split=6,
        min_distinct_files_for_split=4,
        output_language="en",
    )
    prompt_zh = format_refinement_prompt(
        parent_title="Auth",
        parent_path="auth",
        components_block="x",
        current_depth=1,
        max_depth=2,
        min_components_for_split=6,
        min_distinct_files_for_split=4,
        output_language="zh",
    )
    assert prompt_en != prompt_zh


def test_refinement_prompt_version_constant():
    assert REFINEMENT_PROMPT_VERSION == "refinement-v1"
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_prompt_template_refinement.py -v`
Expected: FAIL with `ImportError: cannot import name 'format_refinement_prompt'`.

- [ ] **Step 3: Implement `format_refinement_prompt`**

Append to `codewiki/src/be/prompt_template.py`:

```python
_REFINEMENT_PROMPT_TEMPLATE = """You are refining a software module into focused sub-modules.

Parent module: {parent_title}
Parent path: {parent_path}
Current depth: {current_depth} of max_depth {max_depth}
Output language: {output_language}

Constraints:
- Only propose a split if the parent has at least {min_components_for_split} components
  AND spans at least {min_distinct_files_for_split} distinct files.
- Do NOT exceed max_depth {max_depth}; if current_depth equals max_depth, return an empty
  children object.
- Each child must have a non-empty `module_id` (snake_case), a human `title`, a `path`
  (lowercase, snake_case, no slashes), a one-sentence `description`, and a non-empty
  `components` list whose entries are exact ids from the listing below.
- Do not invent component ids. Do not reuse a component in two children.

Components in this parent:
{components_block}

Return STRICT JSON in this exact shape:
{{
  "should_split": <true|false>,
  "children": {{
    "<title>": {{
      "module_id": "<snake_case>",
      "title": "<title>",
      "path": "<snake_case_path>",
      "description": "<one sentence>",
      "components": ["<exact id>", ...]
    }}
  }}
}}

If should_split is false, return an empty children object.
"""


def format_refinement_prompt(
    *,
    parent_title: str,
    parent_path: str,
    components_block: str,
    current_depth: int,
    max_depth: int,
    min_components_for_split: int,
    min_distinct_files_for_split: int,
    output_language: str,
) -> str:
    return _REFINEMENT_PROMPT_TEMPLATE.format(
        parent_title=parent_title,
        parent_path=parent_path,
        components_block=components_block,
        current_depth=current_depth,
        max_depth=max_depth,
        min_components_for_split=min_components_for_split,
        min_distinct_files_for_split=min_distinct_files_for_split,
        output_language=output_language,
    )
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_prompt_template_refinement.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/prompt_template.py tests/test_prompt_template_refinement.py
git commit -m "feat(refinement): add refinement prompt formatter"
```

---

## Task 8: `tree_refiner.py` — split decision

**Files:**
- Create: `codewiki/src/be/tree_refiner.py`
- Test: `tests/test_tree_refiner.py`

The split decision is pure logic — given the components for a parent and the refinement config, decide whether to attempt a split. This is the smallest, most testable unit.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tree_refiner.py`:

```python
from codewiki.src.be.dependency_analyzer.models.core import Node
from codewiki.src.be.tree_refiner import should_attempt_split
from codewiki.src.codewiki_config import RefinementConfig


def _node(component_id: str, file_path: str) -> Node:
    return Node(
        id=component_id,
        name=component_id.split("::")[-1],
        component_type="function",
        file_path=file_path,
        relative_path=file_path,
        source_code="pass",
    )


def _components(*pairs: tuple[str, str]) -> dict[str, Node]:
    return {cid: _node(cid, fp) for cid, fp in pairs}


def test_should_attempt_split_too_few_components():
    cfg = RefinementConfig(min_components_for_split=6, min_distinct_files_for_split=4)
    comps = _components(("a.py::A", "a.py"), ("b.py::B", "b.py"))
    assert should_attempt_split(["a.py::A", "b.py::B"], comps, cfg, current_depth=1) is False


def test_should_attempt_split_too_few_distinct_files():
    cfg = RefinementConfig(min_components_for_split=4, min_distinct_files_for_split=4)
    comps = _components(
        ("a.py::A", "a.py"),
        ("a.py::B", "a.py"),
        ("a.py::C", "a.py"),
        ("a.py::D", "a.py"),
    )
    assert should_attempt_split(["a.py::A", "a.py::B", "a.py::C", "a.py::D"], comps, cfg, 1) is False


def test_should_attempt_split_meets_thresholds():
    cfg = RefinementConfig(min_components_for_split=4, min_distinct_files_for_split=3)
    comps = _components(
        ("a.py::A", "a.py"),
        ("b.py::B", "b.py"),
        ("c.py::C", "c.py"),
        ("d.py::D", "d.py"),
    )
    assert should_attempt_split(["a.py::A", "b.py::B", "c.py::C", "d.py::D"], comps, cfg, 1) is True


def test_should_attempt_split_max_depth_reached():
    cfg = RefinementConfig(max_depth=2, min_components_for_split=2, min_distinct_files_for_split=2)
    comps = _components(("a.py::A", "a.py"), ("b.py::B", "b.py"))
    # current_depth equals max_depth → no split
    assert should_attempt_split(["a.py::A", "b.py::B"], comps, cfg, current_depth=2) is False


def test_should_attempt_split_below_max_depth():
    cfg = RefinementConfig(max_depth=2, min_components_for_split=2, min_distinct_files_for_split=2)
    comps = _components(("a.py::A", "a.py"), ("b.py::B", "b.py"))
    assert should_attempt_split(["a.py::A", "b.py::B"], comps, cfg, current_depth=1) is True
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_tree_refiner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codewiki.src.be.tree_refiner'`.

- [ ] **Step 3: Implement `should_attempt_split`**

Create `codewiki/src/be/tree_refiner.py`:

```python
"""Tree refinement: pure logic for the split decision and recursive refinement."""

from __future__ import annotations

import logging
from typing import Any

from codewiki.src.codewiki_config import RefinementConfig

logger = logging.getLogger(__name__)


def should_attempt_split(
    component_ids: list[str],
    components: dict[str, Any],
    refinement_cfg: RefinementConfig,
    current_depth: int,
) -> bool:
    """Decide whether a parent node should be split further.

    See spec §Recursion Rule and §Split Criteria.
    """
    if current_depth >= refinement_cfg.max_depth:
        return False
    if len(component_ids) < refinement_cfg.min_components_for_split:
        return False
    distinct_files = {
        components[cid].file_path
        for cid in component_ids
        if cid in components and getattr(components[cid], "file_path", None)
    }
    if len(distinct_files) < refinement_cfg.min_distinct_files_for_split:
        return False
    return True
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_tree_refiner.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/tree_refiner.py tests/test_tree_refiner.py
git commit -m "feat(refinement): add should_attempt_split decision"
```

---

## Task 9: `tree_refiner.py` — collision-safe filename assignment

**Files:**
- Modify: `codewiki/src/be/tree_refiner.py`
- Test: `tests/test_tree_refiner.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tree_refiner.py`:

```python
from codewiki.src.be.tree_refiner import assign_doc_filename


def test_assign_doc_filename_simple():
    used: dict[str, str] = {}
    name = assign_doc_filename(
        used_files=used,
        artifact_id="module:auth_layer",
        preferred_stem="auth_layer",
    )
    assert name == "auth_layer.md"
    assert used["auth_layer.md"] == "module:auth_layer"


def test_assign_doc_filename_collision_with_other_artifact():
    used = {"auth_layer.md": "module:other_thing"}
    name = assign_doc_filename(
        used_files=used,
        artifact_id="module:auth_layer",
        preferred_stem="auth_layer",
    )
    assert name == "auth_layer_2.md"
    assert used["auth_layer_2.md"] == "module:auth_layer"
    assert used["auth_layer.md"] == "module:other_thing"


def test_assign_doc_filename_idempotent_for_same_artifact():
    used = {"auth_layer.md": "module:auth_layer"}
    name = assign_doc_filename(
        used_files=used,
        artifact_id="module:auth_layer",
        preferred_stem="auth_layer",
    )
    assert name == "auth_layer.md"


def test_assign_doc_filename_walks_until_free():
    used = {
        "auth_layer.md": "module:other_a",
        "auth_layer_2.md": "module:other_b",
        "auth_layer_3.md": "module:other_c",
    }
    name = assign_doc_filename(
        used_files=used,
        artifact_id="module:auth_layer",
        preferred_stem="auth_layer",
    )
    assert name == "auth_layer_4.md"
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_tree_refiner.py -v`
Expected: FAIL with `ImportError: cannot import name 'assign_doc_filename'`.

- [ ] **Step 3: Implement `assign_doc_filename`**

Append to `codewiki/src/be/tree_refiner.py`:

```python
def assign_doc_filename(
    *,
    used_files: dict[str, str],
    artifact_id: str,
    preferred_stem: str,
) -> str:
    """Assign a collision-free doc filename and record ownership.

    ``used_files`` maps ``filename -> artifact_id`` and is mutated in place.
    If ``artifact_id`` already owns a filename, return it (idempotent). Otherwise
    walk ``preferred_stem.md``, ``preferred_stem_2.md``, ... until a free name is
    found.
    """
    # Idempotent: if already owned, return existing.
    for existing_name, owner in used_files.items():
        if owner == artifact_id:
            return existing_name

    candidate = f"{preferred_stem}.md"
    if candidate not in used_files:
        used_files[candidate] = artifact_id
        return candidate

    suffix = 2
    while True:
        candidate = f"{preferred_stem}_{suffix}.md"
        if candidate not in used_files:
            used_files[candidate] = artifact_id
            return candidate
        suffix += 1
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_tree_refiner.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/tree_refiner.py tests/test_tree_refiner.py
git commit -m "feat(refinement): add collision-safe doc filename assignment"
```

---

## Task 10: `tree_refiner.py` — LLM call wrapper with cache

**Files:**
- Modify: `codewiki/src/be/tree_refiner.py`
- Test: `tests/test_tree_refiner.py`

This task wraps a single LLM refinement call with cache check + cache write. It does **not** yet recurse — that's Task 11.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tree_refiner.py`:

```python
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from codewiki.src.be.cache_manager import CacheManager
from codewiki.src.be.tree_refiner import refine_one_node


@pytest.fixture
def cache_dir(tmp_path):
    p = tmp_path / ".codewiki"
    p.mkdir()
    return str(p)


def _llm_returning(payload: dict):
    """Build a fake middleware whose .call returns canned JSON text."""
    fake_result = MagicMock()
    fake_result.text = json.dumps(payload)
    fake_result.model = "fake-model"
    middleware = MagicMock()
    middleware.call = AsyncMock(return_value=fake_result)
    return middleware


@pytest.mark.asyncio
async def test_refine_one_node_calls_llm_when_no_cache(cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    components = {
        "a.py::A": _node("a.py::A", "a.py"),
        "b.py::B": _node("b.py::B", "b.py"),
        "c.py::C": _node("c.py::C", "c.py"),
        "d.py::D": _node("d.py::D", "d.py"),
    }
    cfg = RefinementConfig(
        max_depth=3, min_components_for_split=2, min_distinct_files_for_split=2
    )
    middleware = _llm_returning(
        {
            "should_split": True,
            "children": {
                "Group A": {
                    "module_id": "group_a",
                    "title": "Group A",
                    "path": "group_a",
                    "description": "First half.",
                    "components": ["a.py::A", "b.py::B"],
                },
                "Group B": {
                    "module_id": "group_b",
                    "title": "Group B",
                    "path": "group_b",
                    "description": "Second half.",
                    "components": ["c.py::C", "d.py::D"],
                },
            },
        }
    )

    used_files: dict[str, str] = {}
    children = await refine_one_node(
        parent_doc_id="root",
        parent_title="Root",
        parent_path="root",
        component_ids=list(components.keys()),
        components=components,
        current_depth=1,
        refinement_cfg=cfg,
        output_language="en",
        cluster_model="cluster",
        middleware=middleware,
        cache_manager=cache,
        cache_dir=cache_dir,
        used_files=used_files,
    )

    assert middleware.call.await_count == 1
    assert set(children.keys()) == {"Group A", "Group B"}
    assert children["Group A"]["module_id"] == "group_a"
    assert children["Group A"]["_doc_filename"] == "group_a.md"
    assert children["Group B"]["_doc_filename"] == "group_b.md"
    # Cache entry persisted
    entry = cache.get_entry("refinement:root")
    assert entry is not None
    assert entry.status == "valid"


@pytest.mark.asyncio
async def test_refine_one_node_uses_cache_when_valid(cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    components = {"a.py::A": _node("a.py::A", "a.py"), "b.py::B": _node("b.py::B", "b.py")}
    cfg = RefinementConfig(
        max_depth=3, min_components_for_split=2, min_distinct_files_for_split=2
    )

    # Pre-seed cache by running once
    middleware1 = _llm_returning(
        {
            "should_split": True,
            "children": {
                "G": {
                    "module_id": "g",
                    "title": "G",
                    "path": "g",
                    "description": "All.",
                    "components": ["a.py::A", "b.py::B"],
                }
            },
        }
    )
    used: dict[str, str] = {}
    await refine_one_node(
        parent_doc_id="root",
        parent_title="Root",
        parent_path="root",
        component_ids=list(components.keys()),
        components=components,
        current_depth=1,
        refinement_cfg=cfg,
        output_language="en",
        cluster_model="cluster",
        middleware=middleware1,
        cache_manager=cache,
        cache_dir=cache_dir,
        used_files=used,
    )
    assert middleware1.call.await_count == 1

    # Second run with new middleware mock — must NOT call LLM
    middleware2 = _llm_returning({"should_split": False, "children": {}})
    used2: dict[str, str] = {}
    children = await refine_one_node(
        parent_doc_id="root",
        parent_title="Root",
        parent_path="root",
        component_ids=list(components.keys()),
        components=components,
        current_depth=1,
        refinement_cfg=cfg,
        output_language="en",
        cluster_model="cluster",
        middleware=middleware2,
        cache_manager=cache,
        cache_dir=cache_dir,
        used_files=used2,
    )
    assert middleware2.call.await_count == 0
    assert "G" in children
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_tree_refiner.py -v`
Expected: FAIL with `ImportError: cannot import name 'refine_one_node'`.

- [ ] **Step 3: Implement `refine_one_node`**

Append to `codewiki/src/be/tree_refiner.py`:

```python
import json

from codewiki.src.be.cache_manager import CacheManager
from codewiki.src.be.prompt_template import format_refinement_prompt
from codewiki.src.be.refinement_cache import (
    compute_refinement_input_hash,
    load_refinement_payload,
    refinement_artifact_id,
    refinement_output_path,
    save_refinement_payload,
)


def _format_components_block(component_ids: list[str], components: dict) -> str:
    lines = []
    for cid in sorted(component_ids):
        node = components.get(cid)
        if node is None:
            continue
        lines.append(f"- {cid} ({node.file_path})")
    return "\n".join(lines)


def _parse_refinement_response(text: str) -> dict:
    text = text.strip()
    # Strip ```json fences if present
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    return json.loads(text)


async def refine_one_node(
    *,
    parent_doc_id: str,
    parent_title: str,
    parent_path: str,
    component_ids: list[str],
    components: dict,
    current_depth: int,
    refinement_cfg: RefinementConfig,
    output_language: str,
    cluster_model: str,
    middleware,
    cache_manager: CacheManager,
    cache_dir: str,
    used_files: dict[str, str],
) -> dict[str, Any]:
    """Refine a single parent node into children, using the refinement cache.

    Returns the children dict (possibly empty when the node should not split).
    Mutates ``used_files`` to record assigned ``_doc_filename`` values.
    """
    artifact_id = refinement_artifact_id(parent_doc_id)
    input_hash = compute_refinement_input_hash(
        component_ids=component_ids,
        components=components,
        current_depth=current_depth,
        max_depth=refinement_cfg.max_depth,
        min_components_for_split=refinement_cfg.min_components_for_split,
        min_distinct_files_for_split=refinement_cfg.min_distinct_files_for_split,
        max_cluster_components=refinement_cfg.max_cluster_components,
        identity_reuse_threshold=refinement_cfg.identity_reuse_threshold,
        output_language=output_language,
    )

    # Cache hit?
    if cache_manager.is_valid(artifact_id, input_hash):
        cached = load_refinement_payload(cache_dir, parent_doc_id)
        if cached is not None:
            children = cached.get("children", {}) or {}
            # Re-register filenames in used_files so siblings still collide-check.
            for child in children.values():
                fn = child.get("_doc_filename")
                if fn:
                    used_files.setdefault(fn, f"module:{child.get('module_id', '')}")
            return children
        logger.warning(
            "refinement cache entry %s marked valid but payload missing — recomputing",
            artifact_id,
        )

    # Decide whether to split at all.
    if not should_attempt_split(component_ids, components, refinement_cfg, current_depth):
        cache_manager.plan_task(artifact_id, output_file=refinement_output_path(cache_dir, parent_doc_id))
        cache_manager.mark_running(artifact_id)
        save_refinement_payload(cache_dir, parent_doc_id, {"children": {}})
        cache_manager.mark_done(
            artifact_id,
            input_hash=input_hash,
            output_path=refinement_output_path(cache_dir, parent_doc_id),
            model="",
        )
        return {}

    # LLM call.
    cache_manager.plan_task(artifact_id, output_file=refinement_output_path(cache_dir, parent_doc_id))
    cache_manager.mark_running(artifact_id)
    prompt = format_refinement_prompt(
        parent_title=parent_title,
        parent_path=parent_path,
        components_block=_format_components_block(component_ids, components),
        current_depth=current_depth,
        max_depth=refinement_cfg.max_depth,
        min_components_for_split=refinement_cfg.min_components_for_split,
        min_distinct_files_for_split=refinement_cfg.min_distinct_files_for_split,
        output_language=output_language,
    )
    try:
        result = await middleware.call(prompt, model=cluster_model, temperature=0.0)
    except Exception as exc:
        cache_manager.mark_failed(artifact_id, error=str(exc))
        raise

    try:
        parsed = _parse_refinement_response(result.text)
    except Exception as exc:
        cache_manager.mark_failed(artifact_id, error=f"parse: {exc}")
        raise

    if not parsed.get("should_split"):
        save_refinement_payload(cache_dir, parent_doc_id, {"children": {}})
        cache_manager.mark_done(
            artifact_id,
            input_hash=input_hash,
            output_path=refinement_output_path(cache_dir, parent_doc_id),
            model=getattr(result, "model", ""),
        )
        return {}

    children_raw = parsed.get("children", {}) or {}
    children: dict[str, Any] = {}
    for title, child in children_raw.items():
        module_id = child.get("module_id") or title.lower().replace(" ", "_")
        child_artifact = f"module:{module_id}"
        doc_filename = assign_doc_filename(
            used_files=used_files,
            artifact_id=child_artifact,
            preferred_stem=module_id,
        )
        children[title] = {
            "module_id": module_id,
            "title": child.get("title", title),
            "path": child.get("path", module_id),
            "description": child.get("description", ""),
            "_doc_filename": doc_filename,
            "components": list(child.get("components", [])),
            "children": {},
        }

    save_refinement_payload(
        cache_dir,
        parent_doc_id,
        {"children": children},
    )
    cache_manager.mark_done(
        artifact_id,
        input_hash=input_hash,
        output_path=refinement_output_path(cache_dir, parent_doc_id),
        model=getattr(result, "model", ""),
    )
    return children
```

- [ ] **Step 4: Add pytest-asyncio config if needed**

Check `pyproject.toml` for `[tool.pytest.ini_options]` and confirm `asyncio_mode = "auto"` is set, OR that tests use `@pytest.mark.asyncio`. The tests above use `@pytest.mark.asyncio` explicitly, which works in either mode.

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_tree_refiner.py -v`
Expected: PASS for the new tests.

If the cache test fails because `output_file` is taken (it's a path, not a filename), confirm the test asserts pass — the `plan_task` collision check is on `output_file` exact strings, and our paths are unique per `doc_id`, so this is fine.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/tree_refiner.py tests/test_tree_refiner.py
git commit -m "feat(refinement): add refine_one_node with refinement cache hit/miss"
```

---

## Task 11: `tree_refiner.py` — recursive tree walk

**Files:**
- Modify: `codewiki/src/be/tree_refiner.py`
- Test: `tests/test_tree_refiner.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tree_refiner.py`:

```python
from codewiki.src.be.tree_refiner import refine_tree


@pytest.mark.asyncio
async def test_refine_tree_recurses_until_max_depth(cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    components = {
        f"f{i}.py::C{i}": _node(f"f{i}.py::C{i}", f"f{i}.py") for i in range(8)
    }
    top = {
        "Top": {
            "module_id": "top",
            "title": "Top",
            "path": "top",
            "description": "All.",
            "components": list(components.keys()),
            "children": {},
        }
    }
    cfg = RefinementConfig(
        max_depth=2, min_components_for_split=2, min_distinct_files_for_split=2
    )

    # Middleware splits "top" into "left"/"right" and stops there.
    call_log: list[str] = []

    async def fake_call(prompt, model=None, temperature=0.0, **_):
        call_log.append(prompt[:40])
        if "Top" in prompt:
            return MagicMock(
                text=json.dumps(
                    {
                        "should_split": True,
                        "children": {
                            "Left": {
                                "module_id": "left",
                                "title": "Left",
                                "path": "left",
                                "description": "L.",
                                "components": [f"f{i}.py::C{i}" for i in range(4)],
                            },
                            "Right": {
                                "module_id": "right",
                                "title": "Right",
                                "path": "right",
                                "description": "R.",
                                "components": [f"f{i}.py::C{i}" for i in range(4, 8)],
                            },
                        },
                    }
                ),
                model="fake",
            )
        return MagicMock(text=json.dumps({"should_split": False, "children": {}}), model="fake")

    middleware = MagicMock()
    middleware.call = fake_call

    refined = await refine_tree(
        module_tree=top,
        components=components,
        refinement_cfg=cfg,
        output_language="en",
        cluster_model="c",
        middleware=middleware,
        cache_manager=cache,
        cache_dir=cache_dir,
    )

    assert "Top" in refined
    assert set(refined["Top"]["children"].keys()) == {"Left", "Right"}
    # Children at depth 2 do NOT recurse further (max_depth=2).
    assert refined["Top"]["children"]["Left"]["children"] == {}
    assert refined["Top"]["children"]["Right"]["children"] == {}
    # _doc_filename present on every level
    assert refined["Top"]["_doc_filename"]
    assert refined["Top"]["children"]["Left"]["_doc_filename"]


@pytest.mark.asyncio
async def test_refine_tree_collision_against_existing_cache(cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    # Pre-seed cache with an unrelated module:* artifact owning "top.md"
    cache.plan_task("module:legacy", output_file="top.md")

    components = {"a.py::A": _node("a.py::A", "a.py"), "b.py::B": _node("b.py::B", "b.py")}
    top = {
        "Top": {
            "module_id": "top",
            "title": "Top",
            "path": "top",
            "description": ".",
            "components": list(components.keys()),
            "children": {},
        }
    }
    cfg = RefinementConfig(
        max_depth=1, min_components_for_split=2, min_distinct_files_for_split=2
    )
    middleware = MagicMock()
    middleware.call = AsyncMock(
        return_value=MagicMock(text=json.dumps({"should_split": False, "children": {}}), model="fake")
    )

    refined = await refine_tree(
        module_tree=top,
        components=components,
        refinement_cfg=cfg,
        output_language="en",
        cluster_model="c",
        middleware=middleware,
        cache_manager=cache,
        cache_dir=cache_dir,
    )
    # Top must NOT take "top.md" — it should rename
    assert refined["Top"]["_doc_filename"] != "top.md"
    assert refined["Top"]["_doc_filename"].startswith("top_")
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_tree_refiner.py::test_refine_tree_recurses_until_max_depth -v`
Expected: FAIL with `ImportError: cannot import name 'refine_tree'`.

- [ ] **Step 3: Implement `refine_tree`**

Append to `codewiki/src/be/tree_refiner.py`:

```python
def _seed_used_files_from_cache(cache_manager: CacheManager) -> dict[str, str]:
    """Seed used_files with all current output_file → artifact_id mappings.

    This ensures TreeRefinementStage will not reuse a filename owned by an
    existing cache entry from a previous run (the orphan-from-rename case
    we already hit at #7926).
    """
    used: dict[str, str] = {}
    for output_file, artifact_id in cache_manager.output_file_assignments().items():
        if not output_file:
            continue
        # Refinement output paths live under .codewiki/_refinement/, not in docs/.
        # Only filenames that look like flat doc names ('foo.md') matter for collision.
        if "/" in output_file or "\\" in output_file:
            continue
        used[output_file] = artifact_id
    return used


async def refine_tree(
    *,
    module_tree: dict[str, Any],
    components: dict,
    refinement_cfg: RefinementConfig,
    output_language: str,
    cluster_model: str,
    middleware,
    cache_manager: CacheManager,
    cache_dir: str,
) -> dict[str, Any]:
    """Walk a top-level module tree and recursively refine each node.

    Returns the same dict (mutated in place) with full ``_doc_filename`` and
    ``children`` populated according to the refinement cache.
    """
    used_files = _seed_used_files_from_cache(cache_manager)

    async def _walk(node: dict[str, Any], depth: int) -> None:
        module_id = node.get("module_id") or node.get("path") or "node"
        artifact_id = f"module:{module_id}"
        preferred_stem = node.get("path") or module_id
        node["_doc_filename"] = assign_doc_filename(
            used_files=used_files,
            artifact_id=artifact_id,
            preferred_stem=preferred_stem,
        )

        component_ids = list(node.get("components") or [])
        children = await refine_one_node(
            parent_doc_id=module_id,
            parent_title=node.get("title", module_id),
            parent_path=node.get("path", module_id),
            component_ids=component_ids,
            components=components,
            current_depth=depth,
            refinement_cfg=refinement_cfg,
            output_language=output_language,
            cluster_model=cluster_model,
            middleware=middleware,
            cache_manager=cache_manager,
            cache_dir=cache_dir,
            used_files=used_files,
        )
        node["children"] = children
        for child in children.values():
            await _walk(child, depth + 1)

    for top_node in module_tree.values():
        await _walk(top_node, depth=1)

    return module_tree
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_tree_refiner.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/tree_refiner.py tests/test_tree_refiner.py
git commit -m "feat(refinement): recursive refine_tree with collision-safe filenames"
```

---

## Task 12: `TreeRefinementStage` — pipeline adapter

**Files:**
- Create: `codewiki/src/be/stages/tree_refinement.py`
- Test: `tests/test_tree_refinement_stage.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tree_refinement_stage.py`:

```python
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from codewiki.src.be.cache_manager import CacheManager
from codewiki.src.be.dependency_analyzer.models.core import Node
from codewiki.src.be.pipeline import PipelineContext
from codewiki.src.be.stages.tree_refinement import TreeRefinementStage
from codewiki.src.codewiki_config import CodeWikiConfig, RefinementConfig


def _node(component_id: str, file_path: str) -> Node:
    return Node(
        id=component_id,
        name=component_id.split("::")[-1],
        component_type="function",
        file_path=file_path,
        relative_path=file_path,
        source_code="pass",
    )


def _make_context(tmp_path):
    cache_dir = tmp_path / "docs" / ".codewiki"
    cache_dir.mkdir(parents=True)
    cfg = CodeWikiConfig(
        repo_path=str(tmp_path / "repo"),
        docs_dir=str(tmp_path / "docs"),
        llm_base_url="http://localhost",
        llm_api_key="x",
        main_model="m",
        cluster_model="c",
        refinement=RefinementConfig(
            max_depth=2,
            min_components_for_split=2,
            min_distinct_files_for_split=2,
        ),
    )
    cache = CacheManager(str(cache_dir), flush_interval=60)
    components = {
        f"f{i}.py::C{i}": _node(f"f{i}.py::C{i}", f"f{i}.py") for i in range(4)
    }
    module_tree = {
        "Top": {
            "module_id": "top",
            "title": "Top",
            "path": "top",
            "description": ".",
            "components": list(components.keys()),
            "children": {},
        }
    }
    middleware = MagicMock()
    middleware.call = AsyncMock(
        return_value=MagicMock(
            text=json.dumps({"should_split": False, "children": {}}),
            model="fake",
        )
    )
    ctx = PipelineContext(
        config=cfg,
        working_dir=str(tmp_path / "docs"),
        components=components,
        leaf_nodes=list(components.keys()),
        module_tree=module_tree,
        cache_manager=cache,
    )
    # Stages access middleware via ctx.generator.middleware in some places;
    # use the simplest possible holder.
    ctx.generator = MagicMock()
    ctx.generator.middleware = middleware
    return ctx, middleware


@pytest.mark.asyncio
async def test_tree_refinement_stage_assigns_filenames_to_top_nodes(tmp_path):
    ctx, _ = _make_context(tmp_path)
    stage = TreeRefinementStage()
    await stage.execute(ctx)
    assert ctx.module_tree["Top"]["_doc_filename"] == "top.md"


@pytest.mark.asyncio
async def test_tree_refinement_stage_writes_refinement_cache_entry(tmp_path):
    ctx, _ = _make_context(tmp_path)
    stage = TreeRefinementStage()
    await stage.execute(ctx)
    entry = ctx.cache_manager.get_entry("refinement:top")
    assert entry is not None
    assert entry.status == "valid"


@pytest.mark.asyncio
async def test_tree_refinement_stage_is_idempotent_on_second_run(tmp_path):
    ctx, middleware = _make_context(tmp_path)
    stage = TreeRefinementStage()
    await stage.execute(ctx)
    first_calls = middleware.call.await_count
    await stage.execute(ctx)
    second_calls = middleware.call.await_count
    assert second_calls == first_calls  # cached, no new LLM calls
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_tree_refinement_stage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codewiki.src.be.stages.tree_refinement'`.

- [ ] **Step 3: Implement `TreeRefinementStage`**

Create `codewiki/src/be/stages/tree_refinement.py`:

```python
"""TreeRefinementStage — recursive module-tree refinement before doc generation.

See spec docs/superpowers/specs/2026-04-07-tree-refinement-generation-design.md §Stage 4.
"""

from __future__ import annotations

import logging

from codewiki.src.be.pipeline import PipelineContext
from codewiki.src.be.tree_refiner import refine_tree

logger = logging.getLogger(__name__)


class TreeRefinementStage:
    name = "TreeRefinementStage"
    failure_policy = "fail_fast"

    async def execute(self, ctx: PipelineContext) -> None:
        if not ctx.module_tree:
            logger.info("TreeRefinementStage: empty module_tree, nothing to refine")
            return
        if ctx.cache_manager is None:
            raise RuntimeError("TreeRefinementStage requires ctx.cache_manager")
        middleware = getattr(ctx.generator, "middleware", None) if ctx.generator else None
        if middleware is None:
            raise RuntimeError("TreeRefinementStage requires ctx.generator.middleware")

        cache_dir = ctx.cache_manager._cache_dir  # type: ignore[attr-defined]
        await refine_tree(
            module_tree=ctx.module_tree,
            components=ctx.components,
            refinement_cfg=ctx.config.refinement,
            output_language=ctx.config.output_language,
            cluster_model=ctx.config.cluster_model,
            middleware=middleware,
            cache_manager=ctx.cache_manager,
            cache_dir=cache_dir,
        )
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_tree_refinement_stage.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/stages/tree_refinement.py tests/test_tree_refinement_stage.py
git commit -m "feat(refinement): add TreeRefinementStage pipeline adapter"
```

---

## Task 13: Wire `TreeRefinementStage` into `DEFAULT_STAGES`

**Files:**
- Modify: `codewiki/src/be/stages/__init__.py`
- Test: `tests/test_pipeline_stages_order.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_pipeline_stages_order.py`:

```python
from codewiki.src.be.stages import DEFAULT_STAGES


def test_tree_refinement_runs_after_clustering_and_before_state_init():
    names = [stage.name for stage in DEFAULT_STAGES]
    assert "ClusteringStage" in names
    assert "TreeRefinementStage" in names
    assert "StateInitStage" in names
    clustering_idx = names.index("ClusteringStage")
    refinement_idx = names.index("TreeRefinementStage")
    state_init_idx = names.index("StateInitStage")
    assert clustering_idx < refinement_idx < state_init_idx
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_pipeline_stages_order.py -v`
Expected: FAIL — `TreeRefinementStage` not in `DEFAULT_STAGES`.

- [ ] **Step 3: Insert into `DEFAULT_STAGES`**

Open `codewiki/src/be/stages/__init__.py`. Add the import:

```python
from codewiki.src.be.stages.tree_refinement import TreeRefinementStage
```

In the `DEFAULT_STAGES` list, insert `TreeRefinementStage()` between `ClusteringStage()` and `StateInitStage()`:

```python
DEFAULT_STAGES = [
    GraphBuildStage(),
    IndexBuildStage(),
    ClusteringStage(),
    TreeRefinementStage(),
    StateInitStage(),
    ModuleGenerationStage(),
    GuideStage(),
    PostprocessStage(),
    MetadataStage(),
]
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_pipeline_stages_order.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full pipeline integration tests to make sure nothing else broke**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_documentation_generator_helpers.py -v`
Expected: most tests still PASS. `test_cluster_modules_uses_cached_tree_when_commit_matches` may now fail because clustering no longer freezes filenames — that's the next task.

- [ ] **Step 6: Commit**

```bash
git add codewiki/src/be/stages/__init__.py tests/test_pipeline_stages_order.py
git commit -m "feat(refinement): wire TreeRefinementStage into DEFAULT_STAGES"
```

---

## Task 14: Stop freezing filenames inside `_cluster_modules`

The cluster stage previously called `freeze_doc_filenames(tree)` after cluster output. With Plan 1 in place, that's now `TreeRefinementStage`'s job. Removing it from clustering avoids double-assignment.

**Files:**
- Modify: `codewiki/src/be/documentation_generator.py`
- Test: `tests/test_documentation_generator_helpers.py` (update existing tests)

- [ ] **Step 1: Read the current `_cluster_modules` implementation**

Read `codewiki/src/be/documentation_generator.py` lines 389–469. Identify the call to `freeze_doc_filenames(...)` (somewhere around line 425–441 per the codebase map).

- [ ] **Step 2: Remove the `freeze_doc_filenames` call from `_cluster_modules`**

Use `Edit` to remove the single line invocation:

```python
freeze_doc_filenames(module_tree)
```

If `freeze_doc_filenames` is also imported at the top of the file purely for this site, leave the import alone — `documentation_generator.py` may still call it from `_initialize_cache_from_tree` as a defensive double-assign. Don't break that.

- [ ] **Step 3: Update the failing test**

Open `tests/test_documentation_generator_helpers.py`. Find `test_cluster_modules_uses_cached_tree_when_commit_matches`. The test currently asserts that `freeze_doc_filenames` is called from `_cluster_modules`. Change the assertion to assert it is **not** called from `_cluster_modules`:

```python
        patch("codewiki.src.be.documentation_generator.freeze_doc_filenames") as freeze_in_clustering,
```

After `asyncio.run(gen._cluster_modules(ctx))` add:

```python
    freeze_in_clustering.assert_not_called()
```

If the test currently passes the patch in to verify it's called, invert the assertion. Read carefully: test should now confirm clustering does NOT freeze filenames.

- [ ] **Step 4: Run the test**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_documentation_generator_helpers.py::test_cluster_modules_uses_cached_tree_when_commit_matches -v`
Expected: PASS.

- [ ] **Step 5: Run all helper tests to confirm no regressions**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_documentation_generator_helpers.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add codewiki/src/be/documentation_generator.py tests/test_documentation_generator_helpers.py
git commit -m "refactor(refinement): clustering no longer freezes filenames"
```

---

## Task 15: Save module_tree.json AFTER refinement, not after clustering

The frozen tree is now produced at the end of `TreeRefinementStage`, not at the end of `ClusteringStage`. Move the persistence accordingly so a partial pipeline run (e.g., refinement crash) does not leave a stale `module_tree.json` on disk.

**Files:**
- Modify: `codewiki/src/be/documentation_generator.py`
- Modify: `codewiki/src/be/stages/tree_refinement.py`
- Test: `tests/test_tree_refinement_stage.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tree_refinement_stage.py`:

```python
import os

from codewiki.src.config import MODULE_TREE_FILENAME


@pytest.mark.asyncio
async def test_tree_refinement_stage_writes_module_tree_json(tmp_path):
    ctx, _ = _make_context(tmp_path)
    stage = TreeRefinementStage()
    await stage.execute(ctx)
    module_tree_path = os.path.join(ctx.working_dir, MODULE_TREE_FILENAME)
    assert os.path.exists(module_tree_path)
    with open(module_tree_path, "r", encoding="utf-8") as fh:
        loaded = json.load(fh)
    assert "Top" in loaded
    assert loaded["Top"]["_doc_filename"] == "top.md"
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_tree_refinement_stage.py::test_tree_refinement_stage_writes_module_tree_json -v`
Expected: FAIL — `module_tree.json` does not exist at that path.

- [ ] **Step 3: Have `TreeRefinementStage.execute` write `module_tree.json` at the end**

Modify `codewiki/src/be/stages/tree_refinement.py`:

```python
import json
import os

from codewiki.src.config import MODULE_TREE_FILENAME

# ... inside execute(), after the await refine_tree(...) call:

        module_tree_path = os.path.join(ctx.working_dir, MODULE_TREE_FILENAME)
        os.makedirs(os.path.dirname(module_tree_path) or ".", exist_ok=True)
        with open(module_tree_path, "w", encoding="utf-8") as fh:
            json.dump(ctx.module_tree, fh, ensure_ascii=False, indent=2)
```

- [ ] **Step 4: Confirm `_cluster_modules` no longer writes the same file**

Read `codewiki/src/be/documentation_generator.py` `_cluster_modules`. If it currently calls `file_manager.save_json(..., MODULE_TREE_FILENAME, ...)`, **leave that call in place** but also have it write the FIRST_MODULE_TREE_FILENAME as before. The clustering stage's job is now: produce top-level structure, save it as `first_module_tree.json` for diagnostics, do NOT save `module_tree.json`. The refinement stage owns `module_tree.json`.

If both call sites currently exist, remove only the `MODULE_TREE_FILENAME` save from `_cluster_modules`. Leave `FIRST_MODULE_TREE_FILENAME` alone.

- [ ] **Step 5: Run all stage and helper tests**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_tree_refinement_stage.py tests/test_documentation_generator_helpers.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add codewiki/src/be/stages/tree_refinement.py codewiki/src/be/documentation_generator.py tests/test_tree_refinement_stage.py
git commit -m "refactor(refinement): TreeRefinementStage writes module_tree.json"
```

---

## Task 15b: Unify parent artifact namespace — `build_generation_tasks` emits `kind="module"` for non-root parents

**Motivation.** The current `build_generation_tasks` in `codewiki/src/be/documentation_tree_utils.py:302–321` marks *every* non-leaf (i.e. every parent that has children) as `kind="overview"`. Downstream, `_initialize_cache_from_tree` maps that to `overview:{doc_id}`. After Plan 4 lands, parent docs are assembled via `parent_segments.generate_or_assemble_parent_doc` which unconditionally uses the `module:{doc_id}` artifact namespace. If we do not fix `build_generation_tasks` first, the scheduler will plan every parent under `overview:{doc_id}` while Plan 4's code path writes to `module:{doc_id}`. They will never intersect — the parent cache will never be reused and the scheduler will double-dispatch.

The right invariant is:

- **`overview:root`** is reserved exclusively for the repo-level overview (`overview.md`, rendered by `documentation_overview._overview_parts` — spec §Root overview generation keeps this path separate).
- **Every other node in the tree — leaf or internal parent — is `module:{doc_id}`.**

**Files:**
- Modify: `codewiki/src/be/documentation_tree_utils.py`
- Test: `tests/test_build_generation_tasks_namespace.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_build_generation_tasks_namespace.py`:

```python
from codewiki.src.be.documentation_tree_utils import build_generation_tasks
from codewiki.src.codewiki_config import CodeWikiConfig


def _cfg(tmp_path):
    return CodeWikiConfig(
        repo_path=str(tmp_path / "repo"),
        docs_dir=str(tmp_path / "docs"),
        llm_base_url="http://localhost",
        llm_api_key="x",
        main_model="m",
        cluster_model="c",
        output_language="en",
    )


def test_leaves_get_kind_module(tmp_path):
    tree = {
        "Leaf": {
            "module_id": "leaf",
            "path": "leaf",
            "_doc_filename": "leaf.md",
            "components": ["a.py::A"],
            "children": {},
        }
    }
    tasks = build_generation_tasks(tree, _cfg(tmp_path))
    leaf_tasks = [t for t in tasks if t.doc_id != "overview:root"]
    assert len(leaf_tasks) == 1
    assert leaf_tasks[0].kind == "module"


def test_internal_parents_get_kind_module_not_overview(tmp_path):
    tree = {
        "Top": {
            "module_id": "top",
            "path": "top",
            "_doc_filename": "top.md",
            "components": [],
            "children": {
                "Left": {
                    "module_id": "left",
                    "path": "left",
                    "_doc_filename": "top-left.md",
                    "components": ["a.py::A"],
                    "children": {},
                }
            },
        }
    }
    tasks = build_generation_tasks(tree, _cfg(tmp_path))
    by_doc_id = {t.doc_id: t for t in tasks if t.doc_id != "overview:root"}
    # Top is an internal parent — must be kind="module", NOT "overview"
    top = next(t for doc_id, t in by_doc_id.items() if "top" in doc_id.lower() and "left" not in doc_id.lower())
    assert top.kind == "module", (
        f"internal parent marked kind={top.kind!r}; expected 'module'. "
        "Plan 1 Task 15b requires every non-root parent to live in the module: namespace."
    )


def test_only_the_synthetic_root_task_is_kind_overview(tmp_path):
    tree = {
        "Top": {
            "module_id": "top",
            "path": "top",
            "_doc_filename": "top.md",
            "components": [],
            "children": {
                "Leaf": {
                    "module_id": "leaf",
                    "path": "leaf",
                    "_doc_filename": "top-leaf.md",
                    "components": ["a.py::A"],
                    "children": {},
                }
            },
        }
    }
    tasks = build_generation_tasks(tree, _cfg(tmp_path))
    overview_tasks = [t for t in tasks if t.kind == "overview"]
    assert len(overview_tasks) == 1
    assert overview_tasks[0].doc_id == "overview:root"
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_build_generation_tasks_namespace.py -v`
Expected: FAIL on `test_internal_parents_get_kind_module_not_overview` because `build_generation_tasks` currently returns `kind="overview"` for every parent.

- [ ] **Step 3: Flip the kind predicate**

Open `codewiki/src/be/documentation_tree_utils.py` and find the `_walk` function inside `build_generation_tasks` (around line 294–323). Locate the single line:

```python
                    kind="module" if not nested_child_ids else "overview",
```

Replace with:

```python
                    kind="module",
```

Internal parents now share the `module:{doc_id}` namespace with leaves. The synthetic root task appended after `_walk` (around line 326–336) still has `kind="overview"` and `doc_id="overview:root"` — leave it alone.

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_build_generation_tasks_namespace.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the rest of the helper tests**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_documentation_generator_helpers.py tests/test_tree_refinement_stage.py -v`
Expected: PASS. If any test asserted `overview_artifact_id` for an internal parent (via `_initialize_cache_from_tree`), update the assertion to `module_artifact_id` — that's the new invariant.

- [ ] **Step 6: Commit**

```bash
git add codewiki/src/be/documentation_tree_utils.py tests/test_build_generation_tasks_namespace.py
git commit -m "refactor(refinement): unify parent artifact namespace under module:"
```

> **Downstream implications (informational, no action in this task):**
>
> - Plans 2, 4, 5 all reference this invariant. The `_initialize_cache_from_tree` snippet (Plan 2 Task 7, Plan 5 Task 13) routes `task.kind == "overview"` to `overview_artifact_id` and everything else to `module_artifact_id` — unchanged in text, but now only the synthetic root task hits the overview branch. That is correct.
> - Plan 4's `generate_or_assemble_parent_doc` uses `module_artifact_id(parent_doc_id)` at the mark-done site — this now matches what StateInitStage planned. No change to Plan 4 from Task 15b itself.
> - The scheduler does not need special handling for "is this the root?" because the synthetic `overview:root` task already has its own dispatch path via the existing root overview generator (spec §Root overview generation).

---

## Task 16: Make `DocumentationGenerator` expose `middleware` for the stage

`TreeRefinementStage` reads `ctx.generator.middleware`. Confirm this attribute exists on `DocumentationGenerator` (per the codebase map, `self.middleware` is set in `__init__`). The stage already accesses it via `ctx.generator.middleware`, and the `_build_initial_context` method already sets `ctx.generator = self`. Verify, don't change.

- [ ] **Step 1: Write a small confirmation test**

Append to `tests/test_documentation_generator_helpers.py`:

```python
def test_initial_context_exposes_middleware_for_stages(tmp_path):
    gen = _make_generator(tmp_path)
    ctx = gen._build_initial_context()
    assert ctx.generator is gen
    assert getattr(ctx.generator, "middleware", None) is not None
```

- [ ] **Step 2: Run**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_documentation_generator_helpers.py::test_initial_context_exposes_middleware_for_stages -v`
Expected: PASS (no code change needed; it should already work).

If it fails, add `self.middleware = LLMMiddleware(config, usage_stats=self.usage_stats)` to `DocumentationGenerator.__init__` (the codebase map says it's already there). Don't add a duplicate.

- [ ] **Step 3: Commit**

```bash
git add tests/test_documentation_generator_helpers.py
git commit -m "test(refinement): assert generator exposes middleware to stages"
```

---

## Task 17: Smoke-test the full pipeline end-to-end with refinement enabled

This is the integration test that proves Plan 1 produces working software.

**Files:**
- Test: `tests/test_pipeline_with_refinement_smoke.py` (new)

- [ ] **Step 1: Write the smoke test**

Create `tests/test_pipeline_with_refinement_smoke.py`:

```python
"""Smoke test: full pipeline with TreeRefinementStage in place.

We don't run the real LLM. We patch ModuleGenerationStage and the LLM middleware
so the test focuses on pipeline wiring and refinement persistence.
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codewiki.src.be.documentation_generator import DocumentationGenerator
from codewiki.src.codewiki_config import CodeWikiConfig, RefinementConfig
from codewiki.src.config import MODULE_TREE_FILENAME


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


def test_pipeline_runs_with_refinement_stage_in_place(tmp_path):
    gen = _make_gen(tmp_path)

    components = {
        "a.py::A": MagicMock(file_path="a.py", source_code="x"),
        "b.py::B": MagicMock(file_path="b.py", source_code="y"),
    }
    cluster_tree = {
        "Root": {
            "module_id": "root",
            "title": "Root",
            "path": "root",
            "description": ".",
            "components": list(components.keys()),
            "children": {},
        }
    }

    # Stub graph build
    gen.graph_builder.build_dependency_graph = MagicMock(
        return_value=(components, list(components.keys()))
    )

    # Stub clustering output by patching cluster_modules
    with (
        patch(
            "codewiki.src.be.documentation_generator.cluster_modules",
            return_value=cluster_tree,
        ),
        patch(
            "codewiki.src.be.documentation_generator.heal_module_tree_components",
            return_value=cluster_tree,
        ),
        patch.object(
            gen.middleware,
            "call",
            new=AsyncMock(
                return_value=MagicMock(
                    text=json.dumps({"should_split": False, "children": {}}),
                    model="fake",
                )
            ),
        ),
        patch(
            "codewiki.src.be.stages.module_generation.ModuleGenerationStage.execute",
            new=AsyncMock(),
        ),
        patch(
            "codewiki.src.be.stages.guide.GuideStage.execute",
            new=AsyncMock(),
        ),
        patch(
            "codewiki.src.be.stages.postprocess.PostprocessStage.execute",
            new=AsyncMock(),
        ),
        patch(
            "codewiki.src.be.stages.metadata.MetadataStage.execute",
            new=AsyncMock(),
        ),
        patch(
            "codewiki.src.be.stages.index_build.IndexBuildStage.execute",
            new=AsyncMock(),
        ),
    ):
        result = asyncio.run(gen.run())

    assert result is not None
    # module_tree.json was written by TreeRefinementStage
    module_tree_path = os.path.join(gen.config.docs_dir, MODULE_TREE_FILENAME)
    assert os.path.exists(module_tree_path)
    with open(module_tree_path, "r", encoding="utf-8") as fh:
        loaded = json.load(fh)
    assert "Root" in loaded
    assert loaded["Root"]["_doc_filename"] == "root.md"
    # refinement cache entry exists
    refinement_entry = gen.cache_manager.get_entry("refinement:root")
    assert refinement_entry is not None
    assert refinement_entry.status == "valid"
```

- [ ] **Step 2: Run the smoke test**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_pipeline_with_refinement_smoke.py -v`
Expected: PASS.

If `gen.middleware` is `None` because `LLMMiddleware` is created lazily, force-create it before the test by calling `gen._build_initial_context()` or by reading `documentation_generator.py` `__init__` to confirm `self.middleware` is unconditionally constructed. Adjust the patch target accordingly.

- [ ] **Step 3: Run the entire test suite**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/ -x -q 2>&1 | tail -30`
Expected: all tests PASS. If any pre-existing test breaks, debug before committing.

- [ ] **Step 4: Commit**

```bash
git add tests/test_pipeline_with_refinement_smoke.py
git commit -m "test(refinement): smoke test full pipeline with TreeRefinementStage"
```

---

## Task 18: Ensure existing runtime sub-module tool still functions (no removal in Plan 1)

Plan 2 removes `generate_sub_module_documentation`. Plan 1 must leave it functional so we can ship Plan 1 to production safely without losing the existing escape hatch.

- [ ] **Step 1: Confirm `generate_sub_module_documentation` is still imported and callable**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run python -c "from codewiki.src.be.agent_tools.generate_sub_module_documentations import generate_sub_module_documentation; print(generate_sub_module_documentation.__name__)"`
Expected: `generate_sub_module_documentation`.

- [ ] **Step 2: Run the existing agent orchestrator tests**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_agent_orchestrator_behavior.py -v`
Expected: PASS.

- [ ] **Step 3: No code change. No commit.**

This task is purely a verification step. If anything fails, do **not** delete or modify the sub-module tool — debug the regression instead.

---

## Task 19: Final integration sanity

- [ ] **Step 1: Run the entire test suite**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/ -q 2>&1 | tail -20`
Expected: all tests pass. Total count should be ≥ baseline + ~25 new tests from Plan 1.

- [ ] **Step 2: Run any linters the project uses**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run ruff check codewiki/src/be/tree_refiner.py codewiki/src/be/refinement_cache.py codewiki/src/be/stages/tree_refinement.py 2>&1 | tail -20`
Expected: no errors. If ruff is not configured for the project, skip.

- [ ] **Step 3: Verify no committed file accidentally hard-codes a secret or API key**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && git diff main --stat | tail -30`
Expected: only Plan 1 files changed; no `.env` or config with secrets.

- [ ] **Step 4: Tag the Plan 1 milestone (optional)**

```bash
git tag tree-refinement-plan-1-complete
```

No push required.

---

## Acceptance Criteria for Plan 1

Plan 1 is complete when **all** of the following are true:

1. `TreeRefinementStage` exists, is registered in `DEFAULT_STAGES` between `ClusteringStage` and `StateInitStage`, and has its own test file.
2. Running the pipeline produces `module_tree.json` with `_doc_filename` populated on every node, written by `TreeRefinementStage` (not by `_cluster_modules`).
3. A `refinement:{doc_id}` cache entry exists for every refined parent, with `status == "valid"` and a JSON payload at `.codewiki/_refinement/<normalized_doc_id>.json`.
4. Running the same pipeline a second time with no inputs changed makes **zero** new LLM calls in `refine_one_node` (cache hit verified by `await_count` assertion).
5. `_doc_filename` collisions against existing cache `output_file` values are resolved by suffixing (`name_2.md`), not by raising.
6. All previously-passing tests still pass.
7. The runtime `generate_sub_module_documentation` agent tool still works (Plan 2 removes it; Plan 1 leaves it).
8. New `RefinementConfig` is accessible via `cfg.refinement.*` with documented defaults.

---

## Self-Review Notes

**Spec coverage check:**
- ✅ Stage 4 TreeRefinementStage — Tasks 12, 13
- ✅ Frozen Tree Schema — Task 11 (`refine_tree` populates `module_id`/`title`/`path`/`description`/`components`/`children`/`_doc_filename`)
- ✅ Refinement cache artifacts (id, output path, input hash, payload) — Tasks 3, 4, 5, 6
- ✅ Recursion Rule (max_depth, min_components, min_distinct_files) — Tasks 8, 11
- ✅ Split Criteria config — Task 2
- ✅ `_doc_filename` assigned by TreeRefinementStage — Task 9, used in Tasks 10–11
- ✅ Filename collision-safety against existing cache entries — Task 11 (`_seed_used_files_from_cache`)
- ❌ Identity reuse — deferred to Plan 3
- ❌ Parent segment cache — deferred to Plan 4
- ❌ Schema bump / orphan cleanup — deferred to Plan 5
- ❌ Resume semantics — deferred to Plan 5
- ❌ Removing runtime agent tool — deferred to Plan 2
- ❌ Bottom-up scheduler enforcement — deferred to Plan 2

**Type/name consistency:**
- `RefinementConfig` is the dataclass name everywhere (Tasks 2, 8, 11, 12)
- `refinement_artifact_id`, `normalized_doc_id`, `refinement_output_path`, `compute_refinement_input_hash`, `load_refinement_payload`, `save_refinement_payload` — names stable across Tasks 3–6 and used unchanged in Tasks 10–11
- `refine_one_node`, `refine_tree`, `should_attempt_split`, `assign_doc_filename` — pure-logic functions in `tree_refiner.py`, signatures stable across Tasks 8–11
- `TreeRefinementStage.execute(ctx)` matches the `PipelineStage` protocol

**Placeholder scan:** No "TBD", "implement later", or "similar to Task N" found. Every step has either real code or a real command with expected output.
