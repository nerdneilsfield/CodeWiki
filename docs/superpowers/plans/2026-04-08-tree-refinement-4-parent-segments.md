# Tree Refinement Phase 4: Parent Document Segment Cache

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert parent module doc generation from a single monolithic LLM call into three independently-cached segments — `opening`, `overview`, and one `child:{child_doc_id}` per direct child — backed by `.codewiki/_module_parts/{doc_stem}/`. The assembled parent doc is rebuilt from segments only when at least one segment is stale. When the parent change ratio exceeds the threshold (Plan 5), all segments are force-invalidated and rewritten coherently.

**Architecture:** A new module `parent_segments.py` owns the segment artifact ids, paths, input hashes, generation, and assembly. `ModuleGenerationStage` (specifically the parent generation path inside the scheduler) is rewired to call `parent_segments.generate_or_assemble_parent_doc(...)` for any node that has children. Leaf docs are unchanged. Root overview keeps its existing `_overview_parts` mechanism (per spec — separate path).

**Tech Stack:** Python 3.10+, asyncio, pytest.

**Spec reference:** §Parent Document Segments, §Cache Semantics §What changes (parent input hash → see §Parent Document Segments), §Risks Risk 3 mitigation.

**Prerequisite:** Plans 1, 2, 3 merged. All tests green on `main`.

**Out of scope for Plan 4:**
- The "force-invalidate when ratio exceeds threshold" trigger logic — Plan 5 wires the threshold; Plan 4 implements the force-invalidate function.
- Resume semantics, orphan cleanup, schema bump — Plan 5.

---

## File Structure

### New files

| Path | Responsibility |
|------|----------------|
| `codewiki/src/be/parent_segments.py` | Segment artifact id helpers, segment input hash functions, segment file path helpers, `generate_or_assemble_parent_doc`, `force_invalidate_parent_segments` |
| `tests/test_parent_segments.py` | Unit tests for id helpers, hash determinism, file paths, force-invalidate |
| `tests/test_parent_segments_generation.py` | Integration tests for the segment generation pipeline (LLM mocked) |

### Modified files

| Path | Change |
|------|--------|
| `codewiki/src/be/cache_manager.py` | No code change — confirm `plan_task` works with the longer `module:{doc_id}:segment:opening`-style ids (which it already does) |
| `codewiki/src/be/documentation_scheduler.py` | When a parent task is dispatched, route through `parent_segments.generate_or_assemble_parent_doc` instead of the current monolithic parent-doc generator |
| `codewiki/src/be/documentation_generator.py` | If parent doc generation today goes through `documentation_overview.generate_parent_module_docs_impl`, redirect that call site to the new segment path. Leaves unchanged. |
| `codewiki/src/be/documentation_tree_utils.py` | `compute_module_input_hash` is no longer the parent doc input hash — leave it for leaves; add `compute_parent_doc_input_hash` that delegates to `parent_segments` |
| `codewiki/src/config.py` | Add `MODULE_PARTS_DIR = "_module_parts"` constant |
| `codewiki/src/be/prompt_template.py` | Add three new prompts: `format_parent_opening_prompt`, `format_parent_overview_prompt`, `format_parent_child_summary_prompt`, plus their PROMPT_VERSION-included formatting |

---

## Task 0: Baseline check

- [ ] **Step 1: Confirm Plans 1+2+3 merged**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && git log --oneline -40 | grep -i "refinement\|identity"`
Expected: see commits from all three previous plans.

- [ ] **Step 2: Suite green**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/ -q 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 3: Locate the current monolithic parent doc generator**

Read `codewiki/src/be/documentation_overview.py`. Find `generate_parent_module_docs_impl` (or similar). Note its signature, what it produces, and how it ties into `documentation_scheduler`. The current implementation likely calls a single LLM, returns a full markdown blob, and writes it to `<parent>.md`. The Plan 4 implementation moves this to `parent_segments`.

---

## Task 1: `MODULE_PARTS_DIR` constant

**Files:**
- Modify: `codewiki/src/config.py`

- [ ] **Step 1: Add the constant**

Add to `codewiki/src/config.py`:

```python
MODULE_PARTS_DIR = "_module_parts"
```

- [ ] **Step 2: Confirm import**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run python -c "from codewiki.src.config import MODULE_PARTS_DIR; print(MODULE_PARTS_DIR)"`
Expected: `_module_parts`

- [ ] **Step 3: Commit**

```bash
git add codewiki/src/config.py
git commit -m "feat(refinement): add MODULE_PARTS_DIR constant"
```

---

## Task 2: Segment artifact id and path helpers

**Files:**
- Create: `codewiki/src/be/parent_segments.py`
- Test: `tests/test_parent_segments.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_parent_segments.py`:

```python
import os

import pytest

from codewiki.src.be.parent_segments import (
    parent_opening_artifact_id,
    parent_overview_artifact_id,
    parent_child_segment_artifact_id,
    doc_stem_from_filename,
    parent_segment_dir,
    parent_segment_path,
)


def test_parent_opening_artifact_id():
    assert parent_opening_artifact_id("auth_layer") == "module:auth_layer:segment:opening"


def test_parent_overview_artifact_id():
    assert parent_overview_artifact_id("auth_layer") == "module:auth_layer:segment:overview"


def test_parent_child_segment_artifact_id():
    aid = parent_child_segment_artifact_id("auth_layer", "login_flow")
    assert aid == "module:auth_layer:segment:child:login_flow"


def test_doc_stem_from_filename():
    assert doc_stem_from_filename("auth_layer.md") == "auth_layer"
    assert doc_stem_from_filename("backend-services.md") == "backend-services"


def test_parent_segment_dir(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    d = parent_segment_dir(str(cache_dir), "auth_layer")
    assert d.endswith(os.path.join("_module_parts", "auth_layer"))


def test_parent_segment_path_opening(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    p = parent_segment_path(str(cache_dir), "auth_layer", "opening")
    assert p.endswith(os.path.join("_module_parts", "auth_layer", "opening.md"))


def test_parent_segment_path_overview(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    p = parent_segment_path(str(cache_dir), "auth_layer", "overview")
    assert p.endswith(os.path.join("_module_parts", "auth_layer", "overview.md"))


def test_parent_segment_path_child(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    p = parent_segment_path(
        str(cache_dir), "auth_layer", "child", child_doc_stem="login_flow"
    )
    assert p.endswith(os.path.join("_module_parts", "auth_layer", "child_login_flow.md"))


def test_parent_segment_path_child_requires_stem(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    with pytest.raises(ValueError):
        parent_segment_path(str(cache_dir), "auth_layer", "child")
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_parent_segments.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the helpers**

Create `codewiki/src/be/parent_segments.py`:

```python
"""Parent module doc segment cache.

A parent doc is composed of three segment types:
  - opening:  per-parent opening/summary
  - overview: per-parent architecture overview
  - child:    one per direct child

Each segment is its own cache artifact and lives at a deterministic path under
``.codewiki/_module_parts/{doc_stem}/``. See spec §Parent Document Segments.
"""

from __future__ import annotations

import os

from codewiki.src.config import MODULE_PARTS_DIR


def parent_opening_artifact_id(doc_id: str) -> str:
    return f"module:{doc_id}:segment:opening"


def parent_overview_artifact_id(doc_id: str) -> str:
    return f"module:{doc_id}:segment:overview"


def parent_child_segment_artifact_id(parent_doc_id: str, child_doc_id: str) -> str:
    return f"module:{parent_doc_id}:segment:child:{child_doc_id}"


def doc_stem_from_filename(doc_filename: str) -> str:
    """Strip the .md (or .markdown) extension from a filename.

    ``doc_stem`` is the disk-side identifier used in segment paths. It is
    related to but NOT interchangeable with ``doc_id`` (which is the artifact
    identity). See spec §Parent Document Segments §Mapping note.
    """
    return os.path.splitext(doc_filename)[0]


def parent_segment_dir(cache_dir: str, doc_stem: str) -> str:
    """Directory holding all segment files for a given parent."""
    return os.path.join(cache_dir, MODULE_PARTS_DIR, doc_stem)


def parent_segment_path(
    cache_dir: str,
    doc_stem: str,
    segment_type: str,
    child_doc_stem: str | None = None,
) -> str:
    """Path to a single segment file.

    ``segment_type`` is one of: "opening", "overview", "child".
    For "child", ``child_doc_stem`` must be provided.
    """
    base = parent_segment_dir(cache_dir, doc_stem)
    if segment_type == "opening":
        return os.path.join(base, "opening.md")
    if segment_type == "overview":
        return os.path.join(base, "overview.md")
    if segment_type == "child":
        if not child_doc_stem:
            raise ValueError("child_doc_stem is required for segment_type='child'")
        return os.path.join(base, f"child_{child_doc_stem}.md")
    raise ValueError(f"unknown segment_type: {segment_type!r}")
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_parent_segments.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/parent_segments.py tests/test_parent_segments.py
git commit -m "feat(refinement): parent segment artifact ids and file paths"
```

---

## Task 3: Segment input hash functions

Three different hashes per the spec:
- opening: `[title, path, description, output_language, PROMPT_VERSION]`
- overview: `[title, path, description, direct_child_ids..., direct_child_input_hashes..., output_language, PROMPT_VERSION]`
- child segment: `[child.module_id, child.title, child.path, child.description, child.input_hash, output_language, PROMPT_VERSION]`

**Files:**
- Modify: `codewiki/src/be/parent_segments.py`
- Test: `tests/test_parent_segments.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parent_segments.py`:

```python
from codewiki.src.be.parent_segments import (
    compute_opening_input_hash,
    compute_overview_input_hash,
    compute_child_segment_input_hash,
    compute_assembled_parent_input_hash,
)


def test_opening_hash_stable_for_same_inputs():
    a = compute_opening_input_hash(
        title="Auth", path="auth", description="Authentication.", output_language="en"
    )
    b = compute_opening_input_hash(
        title="Auth", path="auth", description="Authentication.", output_language="en"
    )
    assert a == b


def test_opening_hash_changes_when_description_changes():
    a = compute_opening_input_hash(
        title="Auth", path="auth", description="Authentication.", output_language="en"
    )
    b = compute_opening_input_hash(
        title="Auth", path="auth", description="Authentication and sessions.", output_language="en"
    )
    assert a != b


def test_overview_hash_includes_child_input_hashes():
    base = dict(
        title="Auth", path="auth", description="x", output_language="en"
    )
    a = compute_overview_input_hash(
        **base,
        direct_child_pairs=[("login", "h1"), ("logout", "h2")],
    )
    b = compute_overview_input_hash(
        **base,
        direct_child_pairs=[("login", "h1"), ("logout", "h3")],  # h2→h3
    )
    assert a != b


def test_overview_hash_independent_of_child_order():
    base = dict(
        title="Auth", path="auth", description="x", output_language="en"
    )
    a = compute_overview_input_hash(
        **base,
        direct_child_pairs=[("login", "h1"), ("logout", "h2")],
    )
    b = compute_overview_input_hash(
        **base,
        direct_child_pairs=[("logout", "h2"), ("login", "h1")],
    )
    assert a == b


def test_child_segment_hash_components():
    a = compute_child_segment_input_hash(
        child_module_id="login",
        child_title="Login",
        child_path="login",
        child_description="Login flow.",
        child_input_hash="abcd",
        output_language="en",
    )
    b = compute_child_segment_input_hash(
        child_module_id="login",
        child_title="Login",
        child_path="login",
        child_description="Login flow.",
        child_input_hash="abcd",
        output_language="en",
    )
    assert a == b
    c = compute_child_segment_input_hash(
        child_module_id="login",
        child_title="Login",
        child_path="login",
        child_description="Login flow.",
        child_input_hash="zzzz",
        output_language="en",
    )
    assert a != c


def test_assembled_parent_hash_combines_segment_hashes():
    a = compute_assembled_parent_input_hash(
        opening_hash="o1",
        overview_hash="v1",
        child_segment_hashes=["c1", "c2"],
        output_language="en",
    )
    b = compute_assembled_parent_input_hash(
        opening_hash="o1",
        overview_hash="v1",
        child_segment_hashes=["c2", "c1"],  # order independent
        output_language="en",
    )
    assert a == b
    c = compute_assembled_parent_input_hash(
        opening_hash="o1",
        overview_hash="v2",  # overview changed
        child_segment_hashes=["c1", "c2"],
        output_language="en",
    )
    assert a != c
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_parent_segments.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the hash functions**

Append to `codewiki/src/be/parent_segments.py`:

```python
import hashlib

from codewiki.src.be.prompt_template import PROMPT_VERSION


def _h(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(b"\x00")
        h.update(p.encode("utf-8"))
    return h.hexdigest()


def compute_opening_input_hash(
    *,
    title: str,
    path: str,
    description: str,
    output_language: str,
) -> str:
    """opening segment hash = stable_hash([title, path, description, lang, PROMPT_VERSION])."""
    return _h("opening", title, path, description, output_language, PROMPT_VERSION)


def compute_overview_input_hash(
    *,
    title: str,
    path: str,
    description: str,
    direct_child_pairs: list[tuple[str, str]],
    output_language: str,
) -> str:
    """overview segment hash includes direct child ids AND direct child input hashes.

    ``direct_child_pairs`` is a list of ``(child_doc_id, child_input_hash)``.
    Sorting makes the hash order-independent.
    """
    sorted_pairs = sorted(direct_child_pairs, key=lambda p: p[0])
    flat: list[str] = ["overview", title, path, description]
    for child_id, child_hash in sorted_pairs:
        flat.append(f"child:{child_id}")
        flat.append(f"hash:{child_hash}")
    flat.append(output_language)
    flat.append(PROMPT_VERSION)
    return _h(*flat)


def compute_child_segment_input_hash(
    *,
    child_module_id: str,
    child_title: str,
    child_path: str,
    child_description: str,
    child_input_hash: str,
    output_language: str,
) -> str:
    """child segment hash = stable_hash([child fields, child.input_hash, lang, PROMPT_VERSION])."""
    return _h(
        "child",
        child_module_id,
        child_title,
        child_path,
        child_description,
        child_input_hash,
        output_language,
        PROMPT_VERSION,
    )


def compute_assembled_parent_input_hash(
    *,
    opening_hash: str,
    overview_hash: str,
    child_segment_hashes: list[str],
    output_language: str,
) -> str:
    """Parent doc input hash = stable_hash([opening_hash, overview_hash, sorted child hashes, lang, PROMPT_VERSION])."""
    sorted_children = sorted(child_segment_hashes)
    return _h(
        "assembled",
        opening_hash,
        overview_hash,
        *sorted_children,
        output_language,
        PROMPT_VERSION,
    )
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_parent_segments.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/parent_segments.py tests/test_parent_segments.py
git commit -m "feat(refinement): segment input hash functions"
```

---

## Task 4: Segment-level prompts

**Files:**
- Modify: `codewiki/src/be/prompt_template.py`
- Test: `tests/test_prompt_template_segments.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_prompt_template_segments.py`:

```python
from codewiki.src.be.prompt_template import (
    format_parent_opening_prompt,
    format_parent_overview_prompt,
    format_parent_child_summary_prompt,
)


def test_opening_prompt_uses_parent_metadata():
    p = format_parent_opening_prompt(
        title="Auth Layer",
        path="auth_layer",
        description="Authentication module.",
        output_language="en",
    )
    assert "Auth Layer" in p
    assert "auth_layer" in p
    assert "Authentication module." in p


def test_overview_prompt_lists_children():
    p = format_parent_overview_prompt(
        title="Auth Layer",
        path="auth_layer",
        description="Auth.",
        children=[
            {"title": "Login", "path": "login", "description": "Login flow."},
            {"title": "Logout", "path": "logout", "description": "Logout."},
        ],
        output_language="en",
    )
    assert "Login" in p
    assert "Logout" in p
    assert "login" in p
    assert "Login flow." in p


def test_child_summary_prompt_focuses_on_one_child():
    p = format_parent_child_summary_prompt(
        parent_title="Auth Layer",
        child_title="Login",
        child_path="login",
        child_description="Login flow.",
        child_doc_excerpt="The login module handles user authentication tokens...",
        output_language="en",
    )
    assert "Login" in p
    assert "Auth Layer" in p
    assert "Login flow." in p
    assert "login module handles" in p
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_prompt_template_segments.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the three prompt formatters**

Append to `codewiki/src/be/prompt_template.py`:

```python
_PARENT_OPENING_TEMPLATE = """Write a 2-3 sentence opening paragraph for a parent module documentation page.

Parent module: {title}
Path: {path}
Description: {description}
Output language: {output_language}

The opening should:
- Introduce the parent module's role in the system
- Avoid listing children (the next section does that)
- Be plain markdown, no headings, no code blocks
"""


def format_parent_opening_prompt(
    *, title: str, path: str, description: str, output_language: str
) -> str:
    return _PARENT_OPENING_TEMPLATE.format(
        title=title, path=path, description=description, output_language=output_language
    )


_PARENT_OVERVIEW_TEMPLATE = """Write an architecture overview for a parent module that summarizes how its children fit together.

Parent module: {title}
Path: {path}
Description: {description}
Output language: {output_language}

Direct children:
{children_block}

The overview should:
- Explain how the children relate to each other
- Identify any obvious dependency direction
- Be plain markdown, may include a small mermaid diagram
- Be no more than ~400 words
"""


def format_parent_overview_prompt(
    *,
    title: str,
    path: str,
    description: str,
    children: list[dict],
    output_language: str,
) -> str:
    children_block = "\n".join(
        f"- **{c['title']}** (`{c['path']}`): {c.get('description', '')}"
        for c in children
    )
    return _PARENT_OVERVIEW_TEMPLATE.format(
        title=title,
        path=path,
        description=description,
        children_block=children_block,
        output_language=output_language,
    )


_PARENT_CHILD_SUMMARY_TEMPLATE = """Write a one-paragraph summary of a single child module within a parent doc.

Parent module: {parent_title}
Child module: {child_title}
Child path: {child_path}
Child description: {child_description}
Output language: {output_language}

Excerpt from the child's own documentation:
{child_doc_excerpt}

Write 2-4 sentences. Mention what the child does, what it depends on, and how
it fits into the parent. Plain markdown only.
"""


def format_parent_child_summary_prompt(
    *,
    parent_title: str,
    child_title: str,
    child_path: str,
    child_description: str,
    child_doc_excerpt: str,
    output_language: str,
) -> str:
    return _PARENT_CHILD_SUMMARY_TEMPLATE.format(
        parent_title=parent_title,
        child_title=child_title,
        child_path=child_path,
        child_description=child_description,
        child_doc_excerpt=child_doc_excerpt[:2000],
        output_language=output_language,
    )
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_prompt_template_segments.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/prompt_template.py tests/test_prompt_template_segments.py
git commit -m "feat(refinement): segment-level prompt templates"
```

---

## Task 5: Single-segment generator

A small async function that generates one segment, persists it to disk, and updates the cache. Reused for opening, overview, and per-child segments.

**Files:**
- Modify: `codewiki/src/be/parent_segments.py`
- Test: `tests/test_parent_segments_generation.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_parent_segments_generation.py`:

```python
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from codewiki.src.be.cache_manager import CacheManager
from codewiki.src.be.parent_segments import (
    generate_segment,
    parent_segment_path,
)


@pytest.fixture
def cache_dir(tmp_path):
    p = tmp_path / ".codewiki"
    p.mkdir()
    return str(p)


@pytest.mark.asyncio
async def test_generate_segment_writes_file_and_marks_cache_done(cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    middleware = MagicMock()
    middleware.call = AsyncMock(
        return_value=MagicMock(text="Generated opening text.", model="fake")
    )

    output_path = parent_segment_path(cache_dir, "auth", "opening")
    await generate_segment(
        artifact_id="module:auth:segment:opening",
        input_hash="h1",
        prompt="Write an opening.",
        model="m",
        middleware=middleware,
        cache_manager=cache,
        output_path=output_path,
    )

    assert os.path.exists(output_path)
    with open(output_path, "r", encoding="utf-8") as f:
        assert f.read() == "Generated opening text."
    entry = cache.get_entry("module:auth:segment:opening")
    assert entry is not None
    assert entry.status == "valid"
    assert entry.input_hash == "h1"


@pytest.mark.asyncio
async def test_generate_segment_marks_failed_on_llm_error(cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    middleware = MagicMock()
    middleware.call = AsyncMock(side_effect=RuntimeError("boom"))

    output_path = parent_segment_path(cache_dir, "auth", "opening")
    with pytest.raises(RuntimeError):
        await generate_segment(
            artifact_id="module:auth:segment:opening",
            input_hash="h1",
            prompt="x",
            model="m",
            middleware=middleware,
            cache_manager=cache,
            output_path=output_path,
        )

    entry = cache.get_entry("module:auth:segment:opening")
    assert entry is not None
    assert entry.status == "failed"
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_parent_segments_generation.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `generate_segment`**

Append to `codewiki/src/be/parent_segments.py`:

```python
import logging

from codewiki.src.be.cache_manager import CacheManager

logger = logging.getLogger(__name__)


async def generate_segment(
    *,
    artifact_id: str,
    input_hash: str,
    prompt: str,
    model: str,
    middleware,
    cache_manager: CacheManager,
    output_path: str,
) -> str:
    """Generate one segment via the LLM and persist it.

    Returns the segment text. Marks the cache entry valid on success, failed
    on exception.
    """
    cache_manager.plan_task(artifact_id, output_file=output_path)
    cache_manager.mark_running(artifact_id)
    try:
        result = await middleware.call(prompt, model=model, temperature=0.0)
    except Exception as exc:
        cache_manager.mark_failed(artifact_id, error=str(exc))
        raise

    text = getattr(result, "text", "")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp = output_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, output_path)

    cache_manager.mark_done(
        artifact_id,
        input_hash=input_hash,
        output_path=output_path,
        model=getattr(result, "model", model),
    )
    return text
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_parent_segments_generation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/parent_segments.py tests/test_parent_segments_generation.py
git commit -m "feat(refinement): single-segment generator with cache integration"
```

---

## Task 6: `generate_or_assemble_parent_doc` — orchestrator

The orchestrator function that:
1. For each segment (opening, overview, one child each), checks `cache.is_valid(...)`. If valid, reads the existing segment file. Otherwise generates a fresh segment via `generate_segment`.
2. Concatenates all segments into the final parent doc.
3. Writes the assembled doc to its `_doc_filename` and marks `module:{doc_id}` valid in the cache.

**Files:**
- Modify: `codewiki/src/be/parent_segments.py`
- Test: `tests/test_parent_segments_generation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_parent_segments_generation.py`:

```python
from codewiki.src.be.parent_segments import generate_or_assemble_parent_doc


def _make_node(title, path, description, doc_filename, components, children):
    return {
        "module_id": path,
        "title": title,
        "path": path,
        "description": description,
        "_doc_filename": doc_filename,
        "components": components,
        "children": children,
    }


@pytest.mark.asyncio
async def test_generate_or_assemble_parent_doc_writes_assembled(tmp_path, cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    parent = _make_node(
        title="Auth Layer",
        path="auth_layer",
        description="Authentication.",
        doc_filename="auth_layer.md",
        components=[],
        children={
            "Login": _make_node("Login", "login", "Login.", "auth_layer-login.md", ["a.py::A"], {}),
            "Logout": _make_node("Logout", "logout", "Logout.", "auth_layer-logout.md", ["b.py::B"], {}),
        },
    )
    # Pre-write the child docs so the child summary prompt has something to read
    (docs_dir / "auth_layer-login.md").write_text("# Login\n\nLogin flow content.", encoding="utf-8")
    (docs_dir / "auth_layer-logout.md").write_text("# Logout\n\nLogout flow content.", encoding="utf-8")

    # Pre-mark child module artifacts as valid in cache (Plan 5 will compute these
    # via the real bottom-up pipeline; for this test we shortcut)
    cache.plan_task("module:login", output_file="auth_layer-login.md")
    cache.mark_done("module:login", input_hash="h_login", output_path="x", model="m")
    cache.plan_task("module:logout", output_file="auth_layer-logout.md")
    cache.mark_done("module:logout", input_hash="h_logout", output_path="x", model="m")

    middleware = MagicMock()
    call_log: list[str] = []

    async def fake_call(prompt, model=None, temperature=0.0, **_):
        call_log.append(prompt[:30])
        if "opening paragraph" in prompt:
            return MagicMock(text="OPENING TEXT", model="fake")
        if "architecture overview" in prompt:
            return MagicMock(text="OVERVIEW TEXT", model="fake")
        if "Login" in prompt and "summary" in prompt:
            return MagicMock(text="LOGIN SUMMARY", model="fake")
        if "Logout" in prompt and "summary" in prompt:
            return MagicMock(text="LOGOUT SUMMARY", model="fake")
        return MagicMock(text="UNKNOWN", model="fake")

    middleware.call = fake_call

    result = await generate_or_assemble_parent_doc(
        parent_doc_id="auth_layer",
        parent_node=parent,
        working_dir=str(docs_dir),
        cache_dir=cache_dir,
        cache_manager=cache,
        middleware=middleware,
        cluster_model="m",
        output_language="en",
    )

    assert result.output_path == str(docs_dir / "auth_layer.md")
    assert result.input_hash  # non-empty assembled hash
    assert result.model == "m"
    assembled = (docs_dir / "auth_layer.md").read_text(encoding="utf-8")
    assert "OPENING TEXT" in assembled
    assert "OVERVIEW TEXT" in assembled
    assert "LOGIN SUMMARY" in assembled
    assert "LOGOUT SUMMARY" in assembled

    # Critical: generate_or_assemble_parent_doc must NOT have marked the
    # parent artifact itself. The scheduler is the single owner.
    parent_entry = cache.get_entry("module:auth_layer")
    assert parent_entry is None or parent_entry.status != "valid", (
        "generate_or_assemble_parent_doc must not call mark_done — that is "
        "the scheduler's job (see Task 8). Double marks would break AC 5."
    )


@pytest.mark.asyncio
async def test_generate_or_assemble_parent_doc_reuses_cached_segments(tmp_path, cache_dir):
    """Second call with no input change must NOT call the LLM."""
    cache = CacheManager(cache_dir, flush_interval=60)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    parent = _make_node(
        title="Auth Layer",
        path="auth_layer",
        description="Authentication.",
        doc_filename="auth_layer.md",
        components=[],
        children={
            "Login": _make_node("Login", "login", "Login.", "auth_layer-login.md", [], {}),
        },
    )
    (docs_dir / "auth_layer-login.md").write_text("# Login\n\n", encoding="utf-8")

    cache.plan_task("module:login", output_file="auth_layer-login.md")
    cache.mark_done("module:login", input_hash="h_login", output_path="x", model="m")

    middleware = MagicMock()
    middleware.call = AsyncMock(return_value=MagicMock(text="X", model="fake"))

    await generate_or_assemble_parent_doc(
        parent_doc_id="auth_layer",
        parent_node=parent,
        working_dir=str(docs_dir),
        cache_dir=cache_dir,
        cache_manager=cache,
        middleware=middleware,
        cluster_model="m",
        output_language="en",
    )
    first_count = middleware.call.await_count

    await generate_or_assemble_parent_doc(
        parent_doc_id="auth_layer",
        parent_node=parent,
        working_dir=str(docs_dir),
        cache_dir=cache_dir,
        cache_manager=cache,
        middleware=middleware,
        cluster_model="m",
        output_language="en",
    )
    second_count = middleware.call.await_count
    assert second_count == first_count, "second call should hit cache for all segments"
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_parent_segments_generation.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `generate_or_assemble_parent_doc`**

Append to `codewiki/src/be/parent_segments.py`:

```python
from codewiki.src.be.cache_manager import module_artifact_id
from codewiki.src.be.prompt_template import (
    format_parent_child_summary_prompt,
    format_parent_opening_prompt,
    format_parent_overview_prompt,
)


def _read_text(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _read_or_generate_marker(path: str) -> str:
    """Read a segment file from disk; return empty string if missing."""
    return _read_text(path)


async def _ensure_segment(
    *,
    artifact_id: str,
    input_hash: str,
    prompt: str,
    model: str,
    middleware,
    cache_manager: CacheManager,
    output_path: str,
) -> str:
    """Return segment text — from cache if valid, otherwise generate fresh."""
    if cache_manager.is_valid(artifact_id, input_hash):
        existing = _read_text(output_path)
        if existing:
            return existing
        logger.warning(
            "segment %s marked valid but file %s missing — regenerating",
            artifact_id,
            output_path,
        )
    return await generate_segment(
        artifact_id=artifact_id,
        input_hash=input_hash,
        prompt=prompt,
        model=model,
        middleware=middleware,
        cache_manager=cache_manager,
        output_path=output_path,
    )


async def generate_or_assemble_parent_doc(
    *,
    parent_doc_id: str,
    parent_node: dict,
    working_dir: str,
    cache_dir: str,
    cache_manager: CacheManager,
    middleware,
    cluster_model: str,
    output_language: str,
) -> "ParentAssemblyResult":
    """Generate (or reuse) parent doc segments and assemble the final markdown.

    Returns a ``ParentAssemblyResult`` with the final path, the assembled input
    hash, and the model used. The caller (scheduler) is responsible for
    recording the result via ``cache_manager.mark_done`` — this function does
    NOT call mark_done for the parent artifact.
    """
    title = parent_node.get("title", parent_doc_id)
    path = parent_node.get("path", parent_doc_id)
    description = parent_node.get("description", "")
    doc_filename = parent_node["_doc_filename"]
    doc_stem = doc_stem_from_filename(doc_filename)
    children = parent_node.get("children") or {}

    # ----- Opening segment -----
    opening_hash = compute_opening_input_hash(
        title=title, path=path, description=description, output_language=output_language
    )
    opening_path = parent_segment_path(cache_dir, doc_stem, "opening")
    opening_text = await _ensure_segment(
        artifact_id=parent_opening_artifact_id(parent_doc_id),
        input_hash=opening_hash,
        prompt=format_parent_opening_prompt(
            title=title, path=path, description=description, output_language=output_language
        ),
        model=cluster_model,
        middleware=middleware,
        cache_manager=cache_manager,
        output_path=opening_path,
    )

    # ----- Build child input hash pairs from cache -----
    direct_child_pairs: list[tuple[str, str]] = []
    child_segment_hashes: list[str] = []
    for child_title, child in children.items():
        child_doc_id = child.get("module_id") or child_title
        child_input_hash = cache_manager.get_input_hash(module_artifact_id(child_doc_id)) or ""
        direct_child_pairs.append((child_doc_id, child_input_hash))

    # ----- Overview segment -----
    overview_hash = compute_overview_input_hash(
        title=title,
        path=path,
        description=description,
        direct_child_pairs=direct_child_pairs,
        output_language=output_language,
    )
    overview_path = parent_segment_path(cache_dir, doc_stem, "overview")
    overview_text = await _ensure_segment(
        artifact_id=parent_overview_artifact_id(parent_doc_id),
        input_hash=overview_hash,
        prompt=format_parent_overview_prompt(
            title=title,
            path=path,
            description=description,
            children=[
                {
                    "title": c.get("title", k),
                    "path": c.get("path", ""),
                    "description": c.get("description", ""),
                }
                for k, c in children.items()
            ],
            output_language=output_language,
        ),
        model=cluster_model,
        middleware=middleware,
        cache_manager=cache_manager,
        output_path=overview_path,
    )

    # ----- Per-child segments -----
    child_texts: list[tuple[str, str]] = []  # (title, segment text)
    for child_title, child in children.items():
        child_doc_id = child.get("module_id") or child_title
        child_input_hash = cache_manager.get_input_hash(module_artifact_id(child_doc_id)) or ""
        child_seg_hash = compute_child_segment_input_hash(
            child_module_id=child_doc_id,
            child_title=child.get("title", child_title),
            child_path=child.get("path", ""),
            child_description=child.get("description", ""),
            child_input_hash=child_input_hash,
            output_language=output_language,
        )
        child_segment_hashes.append(child_seg_hash)

        child_doc_filename = child.get("_doc_filename", "")
        child_excerpt_path = os.path.join(working_dir, child_doc_filename) if child_doc_filename else ""
        child_excerpt = _read_text(child_excerpt_path) if child_excerpt_path else ""

        child_doc_stem = doc_stem_from_filename(child_doc_filename) if child_doc_filename else child_doc_id
        child_seg_path = parent_segment_path(
            cache_dir, doc_stem, "child", child_doc_stem=child_doc_stem
        )
        child_text = await _ensure_segment(
            artifact_id=parent_child_segment_artifact_id(parent_doc_id, child_doc_id),
            input_hash=child_seg_hash,
            prompt=format_parent_child_summary_prompt(
                parent_title=title,
                child_title=child.get("title", child_title),
                child_path=child.get("path", ""),
                child_description=child.get("description", ""),
                child_doc_excerpt=child_excerpt,
                output_language=output_language,
            ),
            model=cluster_model,
            middleware=middleware,
            cache_manager=cache_manager,
            output_path=child_seg_path,
        )
        child_texts.append((child.get("title", child_title), child_text))

    # ----- Assemble -----
    assembled_lines: list[str] = []
    assembled_lines.append(f"# {title}")
    assembled_lines.append("")
    assembled_lines.append(opening_text.rstrip())
    assembled_lines.append("")
    assembled_lines.append("## Architecture Overview")
    assembled_lines.append("")
    assembled_lines.append(overview_text.rstrip())
    assembled_lines.append("")
    assembled_lines.append("## Modules")
    assembled_lines.append("")
    for child_title, child_text in child_texts:
        assembled_lines.append(f"### {child_title}")
        assembled_lines.append("")
        assembled_lines.append(child_text.rstrip())
        assembled_lines.append("")
    assembled = "\n".join(assembled_lines)

    final_path = os.path.join(working_dir, doc_filename)
    os.makedirs(os.path.dirname(final_path) or ".", exist_ok=True)
    with open(final_path, "w", encoding="utf-8") as f:
        f.write(assembled)

    # Compute the parent's assembled input hash and return it to the caller.
    # IMPORTANT: We deliberately do NOT call cache_manager.mark_done for the
    # parent artifact here. The scheduler (documentation_scheduler.py around
    # line 467) is the single owner of mark_done for module:{doc_id}. If this
    # function also called mark_done, parent_artifact.attempt_count would
    # increment to 2 on every run and AC 5 (exactly-once) would fail. Task 8
    # below wires the scheduler to consume the returned input_hash instead of
    # computing its own for parent nodes.
    parent_input_hash = compute_assembled_parent_input_hash(
        opening_hash=opening_hash,
        overview_hash=overview_hash,
        child_segment_hashes=child_segment_hashes,
        output_language=output_language,
    )
    return ParentAssemblyResult(
        output_path=final_path,
        input_hash=parent_input_hash,
        model=cluster_model,
    )
```

Also define the return dataclass near the top of `parent_segments.py`:

```python
from dataclasses import dataclass


@dataclass
class ParentAssemblyResult:
    """Return value of ``generate_or_assemble_parent_doc``.

    The scheduler consumes these fields when it calls ``mark_done`` for the
    parent artifact. We explicitly do not call mark_done inside the assembly
    function — single writer keeps ``attempt_count == 1`` in the happy path.
    """

    output_path: str
    input_hash: str
    model: str
```

And update the `generate_or_assemble_parent_doc` annotated return type accordingly:

```python
async def generate_or_assemble_parent_doc(
    *,
    parent_doc_id: str,
    parent_node: dict,
    working_dir: str,
    cache_dir: str,
    cache_manager: CacheManager,
    middleware,
    cluster_model: str,
    output_language: str,
) -> ParentAssemblyResult:
    ...
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_parent_segments_generation.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/parent_segments.py tests/test_parent_segments_generation.py
git commit -m "feat(refinement): generate_or_assemble_parent_doc orchestrator"
```

---

## Task 7: `force_invalidate_parent_segments`

When the parent change ratio exceeds the threshold (Plan 5 wires the trigger), all segments must be invalidated and rewritten coherently. This task implements the function; Plan 5 calls it.

**Files:**
- Modify: `codewiki/src/be/parent_segments.py`
- Test: `tests/test_parent_segments_generation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_parent_segments_generation.py`:

```python
from codewiki.src.be.parent_segments import force_invalidate_parent_segments


def test_force_invalidate_marks_all_segments_stale(cache_dir, tmp_path):
    cache = CacheManager(cache_dir, flush_interval=60)
    parent = _make_node(
        title="Auth",
        path="auth",
        description=".",
        doc_filename="auth.md",
        components=[],
        children={
            "Login": _make_node("Login", "login", ".", "auth-login.md", [], {}),
            "Logout": _make_node("Logout", "logout", ".", "auth-logout.md", [], {}),
        },
    )

    # Pre-mark all segments valid
    for aid in (
        "module:auth:segment:opening",
        "module:auth:segment:overview",
        "module:auth:segment:child:login",
        "module:auth:segment:child:logout",
    ):
        cache.plan_task(aid, output_file=f"{aid.replace(':', '_')}.md")
        cache.mark_done(aid, input_hash="x", output_path="/tmp/x", model="m")

    force_invalidate_parent_segments(
        parent_doc_id="auth",
        parent_node=parent,
        cache_manager=cache,
    )

    for aid in (
        "module:auth:segment:opening",
        "module:auth:segment:overview",
        "module:auth:segment:child:login",
        "module:auth:segment:child:logout",
    ):
        entry = cache.get_entry(aid)
        assert entry is not None
        assert entry.status == "stale"
```

- [ ] **Step 2: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_parent_segments_generation.py::test_force_invalidate_marks_all_segments_stale -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `codewiki/src/be/parent_segments.py`:

```python
def force_invalidate_parent_segments(
    *,
    parent_doc_id: str,
    parent_node: dict,
    cache_manager: CacheManager,
) -> list[str]:
    """Mark every segment artifact for this parent as stale.

    Used by Plan 5's incremental threshold path: when the parent change ratio
    exceeds the configured limit, segment-level cache reuse is unsafe (the doc
    must be rewritten coherently), so we explicitly invalidate.

    Returns the list of artifact ids that were invalidated.
    """
    children = parent_node.get("children") or {}
    artifact_ids = [
        parent_opening_artifact_id(parent_doc_id),
        parent_overview_artifact_id(parent_doc_id),
    ]
    for child in children.values():
        child_doc_id = child.get("module_id") or child.get("title", "")
        if child_doc_id:
            artifact_ids.append(parent_child_segment_artifact_id(parent_doc_id, child_doc_id))

    for aid in artifact_ids:
        cache_manager.invalidate(aid)
    return artifact_ids
```

- [ ] **Step 4: Run, confirm passes**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_parent_segments_generation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/parent_segments.py tests/test_parent_segments_generation.py
git commit -m "feat(refinement): force_invalidate_parent_segments helper"
```

---

## Task 8: Wire `generate_or_assemble_parent_doc` into the scheduler

This is the trickiest task in Plan 4. It has two interlocking goals:

1. **Route parent nodes to the segment pipeline.** Any tree node with non-empty `children` should be generated by `generate_or_assemble_parent_doc`, not by the existing leaf generator.
2. **Preserve AC 5 (parent `attempt_count == 1`).** The scheduler at `codewiki/src/be/documentation_scheduler.py:467–478` is the single owner of `cache_manager.mark_done(module:{doc_id}, ...)`. `generate_or_assemble_parent_doc` (Task 6) deliberately does **not** call `mark_done`. For this to work, the scheduler must use the `ParentAssemblyResult.input_hash` returned by the parent path instead of its own `compute_module_input_hash(...)` call, which is only correct for leaves.

**Files:**
- Modify: `codewiki/src/be/documentation_scheduler.py` (the real dispatch loop at line 428–478)
- Modify: `codewiki/src/be/documentation_generator.py` only if a helper is needed for the parent-node callback wiring
- Test: `tests/test_parent_segments_in_scheduler.py` (new, end-to-end wiring test)

- [ ] **Step 1: Re-read the scheduler dispatch site**

Before touching anything, read `codewiki/src/be/documentation_scheduler.py` lines 420–490. Confirm the *actual* `process_module` call signature used by the scheduler — it is:

```python
process_args = (
    name,
    components,
    task_component_ids,
    path,
    working_dir,
    tree_manager,
)
process_kwargs = {}
if accepts_cache_manager and cache_manager is not None:
    process_kwargs["cache_manager"] = cache_manager
_, task_models_used = await process_module(*process_args, **process_kwargs)
```

Six positional arguments plus an optional `cache_manager` kwarg. The return value is unpacked as `(_, task_models_used)` — the first element is already discarded, so we have room to evolve the shape.

Confirm also that `info` (the current tree node dict, including `children`) is in scope at that call site via `path, name, info, _ = all_tasks[key]`. It is.

- [ ] **Step 2: Write the failing integration test**

Create `tests/test_parent_segments_in_scheduler.py`:

```python
"""Verify the scheduler routes parent doc generation through parent_segments."""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codewiki.src.be.documentation_generator import DocumentationGenerator
from codewiki.src.codewiki_config import CodeWikiConfig, RefinementConfig


def test_parent_doc_uses_segment_pipeline(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    gen = DocumentationGenerator(
        CodeWikiConfig(
            repo_path=str(tmp_path / "repo"),
            output_dir=str(tmp_path / "out"),
            dependency_graph_dir=str(tmp_path / "graphs"),
            docs_dir=str(docs_dir),
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
                    "description": "Left.",
                    "components": ["a.py::A"],
                },
                "Right": {
                    "module_id": "right",
                    "title": "Right",
                    "path": "right",
                    "description": "Right.",
                    "components": ["b.py::B"],
                },
            },
        }
    )
    leaf_resp = json.dumps({"should_split": False, "children": {}})

    call_count = {"i": 0}

    async def fake_call(prompt, model=None, temperature=0.0, **_):
        call_count["i"] += 1
        if "refining" in prompt or "split" in prompt:
            if "Top" in prompt:
                return MagicMock(text=refinement_resp, model="fake")
            return MagicMock(text=leaf_resp, model="fake")
        if "opening paragraph" in prompt:
            return MagicMock(text="OPEN", model="fake")
        if "architecture overview" in prompt:
            return MagicMock(text="OVR", model="fake")
        if "summary" in prompt:
            return MagicMock(text="CHILD", model="fake")
        # Leaf doc generation: just return some markdown
        return MagicMock(text="# Leaf doc\n", model="fake")

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

    # Top is a parent now → its doc must be assembled from segments.
    top_md = docs_dir / "top.md"
    if not top_md.exists():
        # If the test framework writes elsewhere, search
        produced = list(docs_dir.glob("*.md"))
        assert produced, f"no .md files written; produced={produced}"
    # Check that segment files exist
    parts_root = docs_dir / ".codewiki" / "_module_parts" / "top"
    assert parts_root.exists() or any(
        ".codewiki/_module_parts/top" in str(p) for p in tmp_path.rglob("*")
    )
```

> This test is integration-heavy. If `gen.run()` ends up not exercising parent generation through `parent_segments` (because the wiring isn't done yet), the test fails on the segment-files assertion. Use it to drive the wiring change.

- [ ] **Step 3: Run, confirm fails**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_parent_segments_in_scheduler.py -v`
Expected: FAIL.

- [ ] **Step 4: Modify the scheduler dispatch block to branch on `info.get("children")`**

Open `codewiki/src/be/documentation_scheduler.py`. Locate the dispatch block that currently reads (around lines 434–478):

```python
                        else:
                            path, name, info, _ = all_tasks[key]
                            task_doc_id = doc_id_for_path(graph_tree, path)
                            task_artifact_id = (
                                overview_artifact_id(task_doc_id)
                                if info.get("children")
                                else module_artifact_id(task_doc_id)
                            )
                            task_input_hash = compute_module_input_hash(
                                name,
                                path,
                                info,
                                components,
                                config,
                                assigned_file=info.get("_doc_filename", ""),
                            )
                            task_component_ids = select_effective_component_ids(info, components)
                            if cache_manager:
                                cache_manager.mark_running(task_artifact_id)
                            process_args = (
                                name,
                                components,
                                task_component_ids,
                                path,
                                working_dir,
                                tree_manager,
                            )
                            process_kwargs = {}
                            if accepts_cache_manager and cache_manager is not None:
                                process_kwargs["cache_manager"] = cache_manager
                            _, task_models_used = await process_module(
                                *process_args, **process_kwargs
                            )
                            if cache_manager:
                                output_file = info.get("_doc_filename", "")
                                output_path = (
                                    os.path.join(working_dir, output_file) if output_file else ""
                                )
                                cache_manager.mark_done(
                                    task_artifact_id,
                                    input_hash=task_input_hash,
                                    output_path=output_path,
                                    model=task_models_used,
                                    output_file=output_file,
                                )
```

Replace with:

```python
                        else:
                            path, name, info, _ = all_tasks[key]
                            task_doc_id = doc_id_for_path(graph_tree, path)
                            # Plan 1 Task 15b + Plan 4 Task 8: every non-root
                            # node lives in the module: namespace. Parents no
                            # longer use overview_artifact_id.
                            task_artifact_id = module_artifact_id(task_doc_id)
                            task_component_ids = select_effective_component_ids(info, components)
                            if cache_manager:
                                cache_manager.mark_running(task_artifact_id)

                            is_parent_node = bool(info.get("children"))
                            if is_parent_node:
                                # Parent → segment pipeline. The assembly
                                # function returns ParentAssemblyResult with
                                # the input_hash we need for mark_done. It
                                # does NOT call mark_done itself; this block
                                # is the single writer. See AC 5.
                                from codewiki.src.be.parent_segments import (
                                    generate_or_assemble_parent_doc,
                                )
                                parent_doc_id = info.get("module_id") or task_doc_id
                                parent_middleware = getattr(
                                    getattr(
                                        __import__("contextvars").copy_context(),  # no-op; real middleware comes from ctx
                                        "middleware",
                                        None,
                                    ),
                                    "middleware",
                                    None,
                                )
                                # The scheduler receives middleware via the
                                # `generator` closure that wraps process_module
                                # in documentation_generator._run_module_queue.
                                # See Step 5 for the generator-side change
                                # that exposes middleware here.
                                assembly = await generate_or_assemble_parent_doc(
                                    parent_doc_id=parent_doc_id,
                                    parent_node=info,
                                    working_dir=working_dir,
                                    cache_dir=cache_manager._cache_dir,  # type: ignore[attr-defined]
                                    cache_manager=cache_manager,
                                    middleware=parent_middleware_from_closure,  # see Step 5
                                    cluster_model=config.cluster_model,
                                    output_language=config.output_language,
                                )
                                task_models_used = assembly.model
                                task_input_hash = assembly.input_hash
                                output_path_final = assembly.output_path
                            else:
                                # Leaf → existing process_module path
                                task_input_hash = compute_module_input_hash(
                                    name,
                                    path,
                                    info,
                                    components,
                                    config,
                                    assigned_file=info.get("_doc_filename", ""),
                                )
                                process_args = (
                                    name,
                                    components,
                                    task_component_ids,
                                    path,
                                    working_dir,
                                    tree_manager,
                                )
                                process_kwargs = {}
                                if accepts_cache_manager and cache_manager is not None:
                                    process_kwargs["cache_manager"] = cache_manager
                                _, task_models_used = await process_module(
                                    *process_args, **process_kwargs
                                )
                                output_file = info.get("_doc_filename", "")
                                output_path_final = (
                                    os.path.join(working_dir, output_file) if output_file else ""
                                )

                            if cache_manager:
                                output_file = info.get("_doc_filename", "")
                                cache_manager.mark_done(
                                    task_artifact_id,
                                    input_hash=task_input_hash,
                                    output_path=output_path_final,
                                    model=task_models_used,
                                    output_file=output_file,
                                )
```

Key points:

- `task_artifact_id` is now unconditionally `module_artifact_id(task_doc_id)`. Parents and leaves share the `module:` namespace (relies on Plan 1 Task 15b).
- For parents, we **do not** call `compute_module_input_hash` (that formula is wrong for segment-assembled parents). We call `generate_or_assemble_parent_doc` and use the returned `input_hash`.
- For leaves, the existing path is preserved unchanged, including `compute_module_input_hash`.
- Both branches funnel into the single `mark_done` call at the bottom. AC 5 is enforced by construction — there is exactly one writer.
- The `parent_middleware_from_closure` placeholder is resolved in Step 5 below.

- [ ] **Step 5: Expose middleware to the scheduler via `run_module_queue` signature**

The scheduler does not currently receive `middleware` as a parameter — it relies on `process_module` (closed over the generator) to have it. Since the parent path bypasses `process_module`, we need to thread middleware through explicitly.

In `codewiki/src/be/documentation_scheduler.py`, add an optional `middleware` parameter to `run_module_queue`:

```python
async def run_module_queue(
    *,
    config,
    graph_tree,
    components,
    working_dir,
    tree_manager,
    process_module,
    generate_root_overview=None,
    desc="Generating docs",
    include_root=True,
    cache_manager=None,
    progress_factory=None,
    cancel_token=None,
    middleware=None,  # NEW — required when any parent node exists
):
    ...
```

Replace the `parent_middleware_from_closure` placeholder in Step 4's dispatch block with `middleware`:

```python
                                assembly = await generate_or_assemble_parent_doc(
                                    parent_doc_id=parent_doc_id,
                                    parent_node=info,
                                    working_dir=working_dir,
                                    cache_dir=cache_manager._cache_dir,
                                    cache_manager=cache_manager,
                                    middleware=middleware,
                                    cluster_model=config.cluster_model,
                                    output_language=config.output_language,
                                )
```

Add a guard at the top of the dispatch block, before reading `info`:

```python
if is_parent_node and middleware is None:
    raise RuntimeError(
        "run_module_queue requires middleware= when the tree contains parent "
        "nodes (Plan 4 segment pipeline)."
    )
```

(Place it inside the parent branch so leaf-only trees still work without middleware.)

In `codewiki/src/be/documentation_generator.py`, find `_run_module_queue` (around line 267–294) and pass `middleware=self.middleware` to the `run_module_queue` call:

```python
await run_module_queue(
    config=self.config,
    graph_tree=ctx.module_tree,
    components=ctx.components,
    working_dir=ctx.working_dir,
    tree_manager=None,
    process_module=process_module,
    generate_root_overview=generate_root_overview,
    cache_manager=self.cache_manager,
    cancel_token=self.cancel_token,
    middleware=self.middleware,
)
```

Exact argument list may differ — preserve whatever already exists, only add `middleware=self.middleware`.

- [ ] **Step 6: Delete the obsolete `process_module` import cleanup**

With the parent branch bypassing `process_module`, ensure the `process_module` callable is still only expected to handle leaves. Search its current implementation for any path that hand-writes parent docs and remove that path — it is now unreachable. Use Grep to find call sites of `generate_parent_module_docs_impl`; that function is no longer called from the scheduler, only possibly from the root overview path (which is separate).

- [ ] **Step 7: Run the integration test**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_parent_segments_in_scheduler.py -v`
Expected: PASS.

If it fails:
- **Segment files missing** → the parent branch in Step 4 didn't fire. Check `info.get("children")` is truthy at dispatch time.
- **`middleware is None` error** → Step 5 didn't wire middleware through. Confirm `_run_module_queue` passes `middleware=self.middleware`.
- **`attempt_count == 2`** → `generate_or_assemble_parent_doc` is still calling `mark_done`. Revisit Task 6.

- [ ] **Step 8: Run the parent attempt-count test from Plan 2 as a regression guard**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_parent_attempt_count.py -v`
Expected: PASS. The weak form (`attempt_count <= 1`) must still hold. The strong form (`== 1`) is enforced by Plan 5 Task 12.

- [ ] **Step 9: Run the full suite**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/ -q 2>&1 | tail -20`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add codewiki/src/be/documentation_scheduler.py codewiki/src/be/documentation_generator.py tests/test_parent_segments_in_scheduler.py
git commit -m "feat(refinement): scheduler routes parents to segment pipeline with single mark_done"
```

---

## Task 9: `compute_parent_doc_input_hash` shim in tree_utils

Existing callers of `compute_module_input_hash` (in `documentation_tree_utils.py`) treat it as the universal module input hash. After Plan 4, parents have a different formula. Add a thin alias `compute_parent_doc_input_hash` that delegates to `parent_segments.compute_assembled_parent_input_hash`, so call sites can be migrated incrementally without changing `compute_module_input_hash` (still used for leaves).

**Files:**
- Modify: `codewiki/src/be/documentation_tree_utils.py`
- Test: `tests/test_parent_segments.py`

- [ ] **Step 1: Add the shim**

Append to `codewiki/src/be/documentation_tree_utils.py`:

```python
def compute_parent_doc_input_hash(
    *,
    opening_hash: str,
    overview_hash: str,
    child_segment_hashes: list[str],
    output_language: str,
) -> str:
    """Delegated to parent_segments. Defined here so tree-walking callers don't
    need to import a new module."""
    from codewiki.src.be.parent_segments import compute_assembled_parent_input_hash

    return compute_assembled_parent_input_hash(
        opening_hash=opening_hash,
        overview_hash=overview_hash,
        child_segment_hashes=child_segment_hashes,
        output_language=output_language,
    )
```

- [ ] **Step 2: Add a test**

Append to `tests/test_parent_segments.py`:

```python
def test_compute_parent_doc_input_hash_shim_matches_segments():
    from codewiki.src.be.documentation_tree_utils import compute_parent_doc_input_hash
    from codewiki.src.be.parent_segments import compute_assembled_parent_input_hash

    a = compute_parent_doc_input_hash(
        opening_hash="o",
        overview_hash="v",
        child_segment_hashes=["c1", "c2"],
        output_language="en",
    )
    b = compute_assembled_parent_input_hash(
        opening_hash="o",
        overview_hash="v",
        child_segment_hashes=["c1", "c2"],
        output_language="en",
    )
    assert a == b
```

- [ ] **Step 3: Run**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/test_parent_segments.py::test_compute_parent_doc_input_hash_shim_matches_segments -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add codewiki/src/be/documentation_tree_utils.py tests/test_parent_segments.py
git commit -m "feat(refinement): tree_utils shim for parent doc input hash"
```

---

## Task 10: Final integration

- [ ] **Step 1: Run the full suite**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && uv run pytest tests/ -q 2>&1 | tail -20`
Expected: all tests pass.

- [ ] **Step 2: Manual smoke (recommended)**

Run a small repo through the CLI and inspect:
- `<docs>/some_parent.md` exists and has the new section structure (`Architecture Overview`, `Modules`)
- `<docs>/.codewiki/_module_parts/<doc_stem>/opening.md`, `overview.md`, `child_*.md` exist

- [ ] **Step 3: Tag**

```bash
git tag tree-refinement-plan-4-complete
```

---

## Acceptance Criteria for Plan 4

1. `parent_segments.py` exists and exports: `parent_opening_artifact_id`, `parent_overview_artifact_id`, `parent_child_segment_artifact_id`, `doc_stem_from_filename`, `parent_segment_dir`, `parent_segment_path`, `compute_opening_input_hash`, `compute_overview_input_hash`, `compute_child_segment_input_hash`, `compute_assembled_parent_input_hash`, `generate_segment`, `generate_or_assemble_parent_doc`, `force_invalidate_parent_segments`.
2. Each parent doc is assembled from three segment artifact types: opening, overview, one child segment per direct child.
3. Segment files live under `.codewiki/_module_parts/{doc_stem}/`.
4. The scheduler routes any node with children through `generate_or_assemble_parent_doc`; leaves go through the existing path.
5. Re-running with no input changes produces zero LLM calls in `generate_or_assemble_parent_doc` (verified by `test_generate_or_assemble_parent_doc_reuses_cached_segments`).
6. Changing one child's `input_hash` (simulating a child doc rewrite) invalidates the overview segment and the affected child segment, but leaves the opening and other child segments cached.
7. `force_invalidate_parent_segments` marks every segment for a parent as stale (Plan 5 will call it from threshold logic).
8. All previously-passing tests still pass.

---

## Self-Review Notes

**Spec coverage check:**
- ✅ §Parent Document Segments — entire plan
- ✅ Three segment artifact types — Task 2
- ✅ Three segment file paths under `_module_parts/{doc_stem}/` — Task 2
- ✅ Three input hash formulas including direct_child_input_hashes for overview — Task 3
- ✅ Assembled parent doc input hash referencing segment hashes — Task 3
- ✅ Force-invalidate-all-segments helper — Task 7
- ✅ doc_stem vs doc_id mapping note — Task 2 docstring
- ✅ Generation orchestrator with cache hit path — Task 6
- ❌ Parent change ratio threshold logic — Plan 5 (calls `force_invalidate_parent_segments`)
- ❌ Hard rerun triggers — Plan 5

**Type/name consistency:** every artifact id, hash function, and helper name matches the spec exactly. `doc_stem` is consistently `os.path.splitext(_doc_filename)[0]`.

**Placeholder scan:** none. The only "needs adaptation" point is Task 8 step 4 (the lookup helper depends on existing scheduler internals); this is flagged honestly with instructions to read first.
