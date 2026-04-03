# Architecture Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pipeline-based generation with explicit degradation status, unified pydantic config, and non-blocking web routes.

**Architecture:** Four phases following the spec's dependency order: A4 (data models) → A2 (pipeline) → A1 (config) → A5 (async). Each phase produces independently testable, committable work. A2 depends on A4's types; A1 depends on A2's new `run()` signature.

**Tech Stack:** Python 3.13, pydantic v2, pytest, asyncio

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `codewiki/src/be/pipeline.py` | Create | PipelineStage protocol, PipelineContext, PipelineRunner, GenerationResult, ModuleSummary, ModuleFailure, ModuleSkip |
| `codewiki/src/be/stages/` | Create (dir) | 8 stage implementations |
| `codewiki/src/be/stages/__init__.py` | Create | Exports |
| `codewiki/src/be/stages/graph_build.py` | Create | GraphBuildStage |
| `codewiki/src/be/stages/index_build.py` | Create | IndexBuildStage |
| `codewiki/src/be/stages/clustering.py` | Create | ClusteringStage |
| `codewiki/src/be/stages/state_init.py` | Create | StateInitStage |
| `codewiki/src/be/stages/module_generation.py` | Create | ModuleGenerationStage |
| `codewiki/src/be/stages/guide.py` | Create | GuideStage |
| `codewiki/src/be/stages/postprocess.py` | Create | PostprocessStage |
| `codewiki/src/be/stages/metadata.py` | Create | MetadataStage |
| `codewiki/src/be/documentation_generator.py` | Modify | Slim to pipeline orchestration |
| `codewiki/src/be/documentation_scheduler.py` | Modify | Return ModuleSummary |
| `codewiki/cli/adapters/doc_generator.py` | Modify | Consume GenerationResult, display status |
| `codewiki/src/codewiki_config.py` | Create | CodeWikiConfig pydantic model |
| `codewiki/src/config_loader.py` | Modify | Output CodeWikiConfig instead of AppConfig→Config |
| `codewiki/src/config.py` | Modify | Delete Config dataclass, keep constants only |
| `codewiki/cli/models/config.py` | Delete | Replaced by CodeWikiConfig |
| `codewiki/cli/config_manager.py` | Delete | Keyring+JSON removed |
| `codewiki/cli/commands/config.py` | Modify | Rewrite for TOML read/write |
| `codewiki/cli/commands/generate.py` | Modify | Use CodeWikiConfig |
| `codewiki/src/fe/routes.py` | Modify | Wrap sync pipeline in to_thread |
| `codewiki/src/fe/visualise_docs.py` | Modify | Wrap sync pipeline in to_thread |

---

## Phase 1: A4 — Degradation Data Models

### Task 1: GenerationResult + ModuleSummary types

**Files:**
- Create: `codewiki/src/be/pipeline.py`
- Create: `tests/test_pipeline_types.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_pipeline_types.py
import pytest
from dataclasses import fields


class TestModuleSummary:
    def test_empty_summary(self):
        from codewiki.src.be.pipeline import ModuleSummary
        s = ModuleSummary(completed=[], failed=[], skipped=[],
                         retried_then_succeeded=[], total=0)
        assert s.total == 0

    def test_with_failures(self):
        from codewiki.src.be.pipeline import ModuleSummary, ModuleFailure
        s = ModuleSummary(
            completed=["module:a"],
            failed=[ModuleFailure(doc_id="module:b", error="timeout", retried=True)],
            skipped=[],
            retried_then_succeeded=[],
            total=2,
        )
        assert len(s.failed) == 1
        assert s.failed[0].retried is True


class TestGenerationResult:
    def test_complete_result(self):
        from codewiki.src.be.pipeline import GenerationResult, ModuleSummary
        r = GenerationResult(
            status="complete",
            warnings=[],
            module_summary=ModuleSummary(
                completed=["a"], failed=[], skipped=[],
                retried_then_succeeded=[], total=1,
            ),
            metadata={},
        )
        assert r.status == "complete"

    def test_degraded_result(self):
        from codewiki.src.be.pipeline import GenerationResult, ModuleSummary, ModuleFailure
        r = GenerationResult(
            status="degraded",
            warnings=["IndexBuildStage failed: timeout"],
            module_summary=ModuleSummary(
                completed=["a"], failed=[ModuleFailure("b", "err", False)],
                skipped=[], retried_then_succeeded=[], total=2,
            ),
            metadata={},
        )
        assert r.status == "degraded"
        assert len(r.warnings) == 1

    def test_to_metadata_dict(self):
        from codewiki.src.be.pipeline import GenerationResult, ModuleSummary
        r = GenerationResult(
            status="complete",
            warnings=[],
            module_summary=ModuleSummary(
                completed=["a"], failed=[], skipped=[],
                retried_then_succeeded=[], total=1,
            ),
            metadata={"existing": "data"},
        )
        d = r.to_metadata_dict()
        assert d["generation_status"] == "complete"
        assert "module_summary" in d


class TestPipelineContext:
    def test_context_creation(self):
        from codewiki.src.be.pipeline import PipelineContext
        ctx = PipelineContext(config=None, working_dir="/tmp")
        assert ctx.working_dir == "/tmp"
        assert ctx.result.status == "complete"  # starts optimistic
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `pytest tests/test_pipeline_types.py -v`

- [ ] **Step 3: Implement pipeline.py**

```python
# codewiki/src/be/pipeline.py
"""Pipeline framework for documentation generation.

Defines the stage protocol, execution context, result types,
and the runner that sequences stages with failure policy enforcement.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Literal, Optional, Protocol

logger = logging.getLogger(__name__)


@dataclass
class ModuleFailure:
    doc_id: str
    error: str
    retried: bool


@dataclass
class ModuleSkip:
    doc_id: str
    reason: str


@dataclass
class ModuleSummary:
    completed: list[str] = field(default_factory=list)
    failed: list[ModuleFailure] = field(default_factory=list)
    skipped: list[ModuleSkip] = field(default_factory=list)
    retried_then_succeeded: list[str] = field(default_factory=list)
    total: int = 0

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "completed": list(self.completed),
            "failed": [asdict(f) for f in self.failed],
            "skipped": [asdict(s) for s in self.skipped],
            "retried_then_succeeded": list(self.retried_then_succeeded),
        }


@dataclass
class GenerationResult:
    status: Literal["complete", "degraded", "failed"] = "complete"
    warnings: list[str] = field(default_factory=list)
    module_summary: ModuleSummary = field(default_factory=ModuleSummary)
    metadata: dict = field(default_factory=dict)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)
        if self.status == "complete":
            self.status = "degraded"

    def mark_failed(self, msg: str) -> None:
        self.warnings.append(msg)
        self.status = "failed"

    def to_metadata_dict(self) -> dict:
        return {
            "generation_status": self.status,
            "degradation_reasons": list(self.warnings),
            "module_summary": self.module_summary.to_dict(),
        }


@dataclass
class PipelineContext:
    """Mutable context passed through pipeline stages."""
    config: Any
    working_dir: str = ""
    components: dict = field(default_factory=dict)
    leaf_nodes: list = field(default_factory=list)
    module_tree: dict = field(default_factory=dict)
    index_products: Any = None
    gen_state: Any = None
    state_mgr: Any = None
    tree_manager: Any = None
    usage_stats: Any = None
    graph_builder: Any = None       # DependencyGraphBuilder instance
    agent_orchestrator: Any = None
    commit_id: str = ""
    result: GenerationResult = field(default_factory=GenerationResult)


class PipelineStage(Protocol):
    """Protocol for a pipeline stage."""
    name: str
    failure_policy: Literal["fail_fast", "degraded_ok"]

    async def execute(self, ctx: PipelineContext) -> None: ...


class PipelineRunner:
    """Execute stages in sequence with failure policy enforcement."""

    def __init__(self, stages: list[PipelineStage]):
        self._stages = stages

    async def execute(self, ctx: PipelineContext) -> GenerationResult:
        for stage in self._stages:
            try:
                logger.info("▶ Stage: %s", stage.name)
                await stage.execute(ctx)
                logger.info("✓ Stage: %s complete", stage.name)
            except Exception as exc:
                msg = f"{stage.name} failed: {exc}"
                if stage.failure_policy == "fail_fast":
                    logger.error("✗ %s (fail_fast — aborting pipeline)", msg)
                    ctx.result.mark_failed(msg)
                    break
                else:
                    logger.warning("⚠ %s (degraded_ok — continuing)", msg)
                    ctx.result.add_warning(msg)
        return ctx.result
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_pipeline_types.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/pipeline.py tests/test_pipeline_types.py
git commit -m "feat(pipeline): add GenerationResult, ModuleSummary, PipelineRunner framework"
```

---

### Task 2: Scheduler returns ModuleSummary

**Files:**
- Modify: `codewiki/src/be/documentation_scheduler.py`
- Create: `tests/test_scheduler_module_summary.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_scheduler_module_summary.py
import asyncio
import pytest
from codewiki.src.be.pipeline import ModuleSummary


class _NoopProgress:
    def update(self, n=1): pass
    def set_postfix_str(self, s, refresh=False): pass
    def close(self): pass


@pytest.mark.asyncio
async def test_run_module_queue_returns_module_summary():
    from codewiki.src.be.documentation_scheduler import run_module_queue

    async def mock_process(name, components, core_ids, path, working_dir,
                           tree_manager, **kwargs):
        if name == "FailModule":
            raise RuntimeError("test failure")
        return {}, "mock-model"

    tree = {
        "GoodModule": {"components": ["a"], "children": {}},
        "FailModule": {"components": ["b"], "children": {}},
    }

    class FakeConfig:
        max_concurrent = 2

    summary = await run_module_queue(
        config=FakeConfig(),
        graph_tree=tree,
        components={"a": None, "b": None},
        working_dir="/tmp/fake",
        tree_manager=None,
        process_module=mock_process,
        include_root=False,
        progress_factory=lambda **kw: _NoopProgress(),
    )

    assert isinstance(summary, ModuleSummary)
    assert summary.total == 2
    assert len(summary.failed) >= 1
```

- [ ] **Step 2: Run test — expect FAIL** (current `run_module_queue` returns None)

- [ ] **Step 3: Modify `run_module_queue` to track and return `ModuleSummary`**

In `documentation_scheduler.py`, add a `ModuleSummary` accumulator inside the coordinator. Track completed/failed/skipped doc_ids. Return the summary at the end.

Key changes:
- Import `ModuleSummary`, `ModuleFailure`, `ModuleSkip` from `pipeline.py`
- Coordinator tracks: when `success=True`, append to `summary.completed`; when `success=False`, append `ModuleFailure`; when parent is skipped because children failed, append `ModuleSkip`
- `run_module_queue` return type changes from `None` to `ModuleSummary`
- `total` = number of non-root tasks in `all_tasks`

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_scheduler_module_summary.py tests/test_scheduler_coordinator.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/documentation_scheduler.py tests/test_scheduler_module_summary.py
git commit -m "feat(scheduler): return ModuleSummary with completed/failed/skipped tracking"
```

---

## Phase 2: A2 — Pipeline Stages

### Task 3: Extract 8 stages from DocumentationGenerator.run()

**Files:**
- Create: `codewiki/src/be/stages/__init__.py`
- Create: `codewiki/src/be/stages/graph_build.py`
- Create: `codewiki/src/be/stages/index_build.py`
- Create: `codewiki/src/be/stages/clustering.py`
- Create: `codewiki/src/be/stages/state_init.py`
- Create: `codewiki/src/be/stages/module_generation.py`
- Create: `codewiki/src/be/stages/guide.py`
- Create: `codewiki/src/be/stages/postprocess.py`
- Create: `codewiki/src/be/stages/metadata.py`
- Modify: `codewiki/src/be/documentation_generator.py`
- Modify: `codewiki/cli/adapters/doc_generator.py` (consume GenerationResult)
- Modify: `codewiki/src/fe/background_worker.py` (consume GenerationResult)
- Modify: `codewiki/src/be/main.py` (consume GenerationResult)
- Create: `tests/test_pipeline_stages.py`

This is the largest task. Each stage extracts a block from `run()` into a class with `async execute(ctx: PipelineContext)`. All three callers of `run()` must be updated in the same task to avoid a half-migrated state.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_pipeline_stages.py
import pytest
from unittest.mock import MagicMock, AsyncMock
from codewiki.src.be.pipeline import PipelineContext, PipelineRunner, GenerationResult


class TestPipelineRunner:
    @pytest.mark.asyncio
    async def test_all_stages_succeed_gives_complete(self):
        class OkStage:
            name = "ok"
            failure_policy = "degraded_ok"
            async def execute(self, ctx): pass

        runner = PipelineRunner([OkStage(), OkStage()])
        ctx = PipelineContext(config=None)
        result = await runner.execute(ctx)
        assert result.status == "complete"
        assert len(result.warnings) == 0

    @pytest.mark.asyncio
    async def test_degraded_ok_failure_gives_degraded(self):
        class FailStage:
            name = "index"
            failure_policy = "degraded_ok"
            async def execute(self, ctx):
                raise RuntimeError("index failed")

        class OkStage:
            name = "next"
            failure_policy = "degraded_ok"
            async def execute(self, ctx): pass

        runner = PipelineRunner([FailStage(), OkStage()])
        ctx = PipelineContext(config=None)
        result = await runner.execute(ctx)
        assert result.status == "degraded"
        assert "index failed" in result.warnings[0]

    @pytest.mark.asyncio
    async def test_fail_fast_aborts_pipeline(self):
        executed = []

        class FailFast:
            name = "graph"
            failure_policy = "fail_fast"
            async def execute(self, ctx):
                raise RuntimeError("no graph")

        class NeverReached:
            name = "cluster"
            failure_policy = "fail_fast"
            async def execute(self, ctx):
                executed.append("cluster")

        runner = PipelineRunner([FailFast(), NeverReached()])
        ctx = PipelineContext(config=None)
        result = await runner.execute(ctx)
        assert result.status == "failed"
        assert "cluster" not in executed

    @pytest.mark.asyncio
    async def test_stage_order_preserved(self):
        order = []

        class Stage:
            def __init__(self, n):
                self.name = n
                self.failure_policy = "degraded_ok"
            async def execute(self, ctx):
                order.append(self.name)

        stages = [Stage("a"), Stage("b"), Stage("c")]
        runner = PipelineRunner(stages)
        await runner.execute(PipelineContext(config=None))
        assert order == ["a", "b", "c"]
```

- [ ] **Step 2: Run tests — expect PASS** (PipelineRunner already implemented in Task 1)

- [ ] **Step 3: Create stage files**

Each stage file follows this pattern:

```python
# codewiki/src/be/stages/graph_build.py
from codewiki.src.be.pipeline import PipelineContext

class GraphBuildStage:
    name = "GraphBuild"
    failure_policy = "fail_fast"

    async def execute(self, ctx: PipelineContext) -> None:
        # graph_builder lives on PipelineContext directly (from DocumentationGenerator),
        # NOT on agent_orchestrator.
        components, leaf_nodes = ctx.graph_builder.build_dependency_graph()
        ctx.components = components
        ctx.leaf_nodes = leaf_nodes
```

Create all 8 stage files, each extracting the corresponding block from `run()`. The `failure_policy` values match the spec table:
- GraphBuild: `fail_fast`
- IndexBuild: `degraded_ok`
- Clustering: `fail_fast`
- StateInit: `fail_fast`
- ModuleGeneration: special (uses ModuleSummary from scheduler)
- Guide: `degraded_ok`
- Postprocess: `degraded_ok`
- Metadata: `degraded_ok`

`ModuleGenerationStage.execute()` calls `run_module_queue()`, receives `ModuleSummary`, writes it to `ctx.result.module_summary`. If all modules failed, raises to trigger fail_fast handling in the runner.

`MetadataStage` is last — it reads `ctx.usage_stats` which now includes guide + postprocess token usage.

- [ ] **Step 4: Create `__init__.py` with stage list export**

```python
# codewiki/src/be/stages/__init__.py
from codewiki.src.be.stages.graph_build import GraphBuildStage
from codewiki.src.be.stages.index_build import IndexBuildStage
from codewiki.src.be.stages.clustering import ClusteringStage
from codewiki.src.be.stages.state_init import StateInitStage
from codewiki.src.be.stages.module_generation import ModuleGenerationStage
from codewiki.src.be.stages.guide import GuideStage
from codewiki.src.be.stages.postprocess import PostprocessStage
from codewiki.src.be.stages.metadata import MetadataStage

DEFAULT_STAGES = [
    GraphBuildStage(),
    IndexBuildStage(),
    ClusteringStage(),
    StateInitStage(),
    ModuleGenerationStage(),
    GuideStage(),
    PostprocessStage(),
    MetadataStage(),
]
```

- [ ] **Step 5: Rewrite DocumentationGenerator.run()**

```python
# codewiki/src/be/documentation_generator.py — new run()
async def run(self) -> GenerationResult:
    from codewiki.src.be.pipeline import PipelineContext, PipelineRunner
    from codewiki.src.be.stages import DEFAULT_STAGES

    ctx = self._build_initial_context()
    runner = PipelineRunner(DEFAULT_STAGES)
    result = await runner.execute(ctx)
    return result

def _build_initial_context(self) -> PipelineContext:
    from codewiki.src.be.pipeline import PipelineContext
    return PipelineContext(
        config=self.config,
        working_dir=os.path.abspath(self.config.docs_dir),
        graph_builder=self.graph_builder,
        agent_orchestrator=self.agent_orchestrator,
        usage_stats=self.usage_stats,
        commit_id=self.commit_id,
    )
```

- [ ] **Step 6: Update ALL callers of run() to consume GenerationResult**

Three call sites exist:
1. `codewiki/cli/adapters/doc_generator.py` — CLI adapter
2. `codewiki/src/fe/background_worker.py:270` — web worker (`loop.run_until_complete(doc_generator.run())`)
3. `codewiki/src/be/main.py:61` — standalone entry (`await doc_generator.run()`)

All three must handle the new `GenerationResult` return value.

In `codewiki/cli/adapters/doc_generator.py`, after `result = await doc_generator.run()`:

```python
if result.status == "complete":
    logger.info("Generation complete")
elif result.status == "degraded":
    logger.warning("Generation completed with issues:")
    for w in result.warnings:
        logger.warning("  - %s", w)
    for f in result.module_summary.failed:
        logger.warning("  - Module %s failed: %s", f.doc_id, f.error)
elif result.status == "failed":
    logger.error("Generation failed:")
    for w in result.warnings:
        logger.error("  - %s", w)
```

In `codewiki/src/fe/background_worker.py:270`, update to handle result:
```python
result = loop.run_until_complete(doc_generator.run())
if result.status == "failed":
    raise RuntimeError(f"Generation failed: {result.warnings}")
```

In `codewiki/src/be/main.py:61`, update similarly:
```python
result = await doc_generator.run()
if result.status == "failed":
    raise SystemExit(1)
```

- [ ] **Step 7: Run full regression**

Run: `pytest tests/ -q -k "not network" --timeout=60`

- [ ] **Step 8: Commit**

```bash
git add codewiki/src/be/stages/ codewiki/src/be/pipeline.py codewiki/src/be/documentation_generator.py codewiki/cli/adapters/doc_generator.py codewiki/src/fe/background_worker.py codewiki/src/be/main.py tests/test_pipeline_stages.py
git commit -m "refactor(pipeline): extract 8 stages from DocumentationGenerator.run()"
```

---

## Phase 3: A1 — Config Unification

### Task 4: Create CodeWikiConfig pydantic model

**Files:**
- Create: `codewiki/src/codewiki_config.py`
- Create: `tests/test_codewiki_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_codewiki_config.py
import pytest


class TestCodeWikiConfig:
    def test_minimal_creation(self):
        from codewiki.src.codewiki_config import CodeWikiConfig
        cfg = CodeWikiConfig(repo_path="/tmp/repo", docs_dir="/tmp/docs")
        assert cfg.repo_path == "/tmp/repo"
        assert cfg.context == "cli"  # default

    def test_context_field(self):
        from codewiki.src.codewiki_config import CodeWikiConfig
        cfg = CodeWikiConfig(repo_path="/tmp", docs_dir="/tmp", context="web")
        assert cfg.context == "web"

    def test_provider_list(self):
        from codewiki.src.codewiki_config import CodeWikiConfig
        cfg = CodeWikiConfig(
            repo_path="/tmp", docs_dir="/tmp",
            providers=[{"name": "openai", "type": "openai_compatible",
                       "base_url": "https://api.openai.com/v1",
                       "api_keys": ["test-key"], "models": ["gpt-4o"]}],
        )
        assert len(cfg.providers) == 1

    def test_cli_override_via_model_copy(self):
        from codewiki.src.codewiki_config import CodeWikiConfig
        base = CodeWikiConfig(repo_path="/tmp", docs_dir="/tmp", main_model="gpt-4o")
        overridden = base.model_copy(update={"main_model": "claude-sonnet"})
        assert overridden.main_model == "claude-sonnet"
        assert base.main_model == "gpt-4o"  # immutable

    def test_include_exclude_patterns(self):
        from codewiki.src.codewiki_config import CodeWikiConfig
        cfg = CodeWikiConfig(
            repo_path="/tmp", docs_dir="/tmp",
            agent_instructions={"include_patterns": ["src/**"], "exclude_patterns": ["tests/**"]},
        )
        assert cfg.include_patterns == ["src/**"]
        assert cfg.exclude_patterns == ["tests/**"]
```

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement CodeWikiConfig**

```python
# codewiki/src/codewiki_config.py
"""Unified configuration model for CodeWiki.

Single pydantic model replacing Config (dataclass), AppConfig (dataclass),
Configuration (CLI model), and ConfigManager (keyring+JSON).
"""
from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    """Must match all fields used by llm_services and config_loader."""
    name: str
    type: str = "openai_compatible"
    base_url: Optional[str] = None
    endpoint: Optional[str] = None
    api_keys: list[Any] = Field(default_factory=list)
    model_list: list[str] = Field(default_factory=list)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    api_version: Optional[str] = None
    deployment: Optional[str] = None
    anthropic_version: Optional[str] = None
    project_id: Optional[str] = None
    location: Optional[str] = None
    credentials_path: Optional[str] = None


class CodeWikiConfig(BaseModel):
    """Canonical configuration — loaded from TOML, optionally overridden by CLI flags."""

    # Required
    repo_path: str
    docs_dir: str

    # Derived paths
    output_dir: str = ""
    dependency_graph_dir: str = ""

    # Context
    context: Literal["cli", "web"] = "cli"

    # LLM
    main_model: str = ""
    cluster_model: str = ""
    fallback_model: str = ""
    long_context_model: Optional[str] = None
    long_context_threshold: int = 200_000
    llm_base_url: str = ""
    llm_api_key: str = ""

    # Token limits
    max_tokens: int = 32_768
    max_token_per_module: int = 36_369
    max_token_per_leaf_module: int = 16_000

    # Concurrency
    max_concurrent: int = 3
    max_retries: int = 2
    max_depth: int = 2

    # Output
    output_language: str = "en"
    postprocess_strict: bool = False
    postprocess_fix_links: bool = True

    # Agent
    agent_instructions: Optional[dict[str, Any]] = None

    # Providers
    providers: list[ProviderConfig] = Field(default_factory=list)

    @property
    def include_patterns(self) -> Optional[list[str]]:
        if self.agent_instructions:
            return self.agent_instructions.get("include_patterns")
        return None

    @property
    def exclude_patterns(self) -> Optional[list[str]]:
        if self.agent_instructions:
            return self.agent_instructions.get("exclude_patterns")
        return None

    @property
    def custom_instructions(self) -> Optional[str]:
        if self.agent_instructions:
            return self.agent_instructions.get("custom_instructions")
        return None

    model_config = {"extra": "ignore"}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_codewiki_config.py -v`

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/codewiki_config.py tests/test_codewiki_config.py
git commit -m "feat(config): add CodeWikiConfig pydantic model"
```

---

### Task 5: Migrate config_loader to output CodeWikiConfig

**Files:**
- Modify: `codewiki/src/config_loader.py`
- Create: `tests/test_config_loader_unified.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_config_loader_unified.py
import pytest


def test_load_config_returns_codewiki_config(tmp_path):
    from codewiki.src.codewiki_config import CodeWikiConfig

    toml_content = '''
[runtime]
output_dir = "/tmp/output"
output_language = "zh"

[generation]
main_model = "gpt-4o"

[[providers]]
name = "openai"
type = "openai_compatible"
base_url = "https://api.openai.com/v1"
api_keys = ["test-key"]
model_list = ["gpt-4o"]
'''
    config_file = tmp_path / "config.toml"
    config_file.write_text(toml_content)

    from codewiki.src.config_loader import load_config
    cfg = load_config(str(config_file), repo_path="/tmp/repo")

    assert isinstance(cfg, CodeWikiConfig)
    assert cfg.main_model == "gpt-4o"
    assert cfg.output_language == "zh"
    assert len(cfg.providers) == 1
```

- [ ] **Step 2: Implement `load_config()` → CodeWikiConfig**

`load_config()` becomes the **sole public entry point** in `config_loader.py`. It does NOT wrap `load_app_config()` — it replaces it:

1. `tomllib.load()` → raw dict
2. `_resolve_env_secrets()` — walk values, expand `env:VAR` prefixes
3. `_resolve_providers()` — build ProviderConfig list, validate model refs
4. `CodeWikiConfig.model_validate(resolved_dict)` with `repo_path` injected

Internal parsing helpers (`_resolve_env_secrets`, `_resolve_providers`, `resolve_model_ref`) are kept as private functions. `AppConfig`, `load_app_config()`, `RuntimeSection`, `TokensSection`, `GenerationSection`, `AgentSection`, and `to_runtime_config()` are all deleted in this same task — not deferred to Task 6. The old intermediate model chain does not survive this step.

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_config_loader_unified.py -v`

- [ ] **Step 4: Commit**

```bash
git add codewiki/src/config_loader.py tests/test_config_loader_unified.py
git commit -m "feat(config): add load_config() returning CodeWikiConfig"
```

---

### Task 6: Migrate all Config consumers to CodeWikiConfig

**Files:**
- Modify: `codewiki/src/config.py` (delete Config dataclass, keep constants)
- Modify: `codewiki/cli/commands/generate.py`
- Modify: `codewiki/cli/adapters/doc_generator.py`
- Modify: `codewiki/src/be/documentation_generator.py`
- Modify: `codewiki/src/be/llm_services.py`
- Modify: `codewiki/src/be/agent_orchestrator.py`
- Modify: `codewiki/src/be/guide_generator.py`
- Modify: `codewiki/src/fe/background_worker.py`
- Delete: `codewiki/cli/models/config.py`
- Delete: `codewiki/cli/config_manager.py`

This is a mechanical migration — every `from codewiki.src.config import Config` becomes `from codewiki.src.codewiki_config import CodeWikiConfig`. Every `Config` type hint becomes `CodeWikiConfig`.

- [ ] **Step 1: Delete old models and manager**

Remove `codewiki/cli/models/config.py` and `codewiki/cli/config_manager.py`. Remove `Config` dataclass from `codewiki/src/config.py` (keep constants + `internal_file_path()`). Remove `set_cli_context` / `is_cli_context` / `_CLI_CONTEXT`.

- [ ] **Step 2: Update all import sites**

Find and replace across the codebase:
```bash
grep -rn "from codewiki.src.config import Config" codewiki/
grep -rn "from codewiki.cli.config_manager import" codewiki/
grep -rn "from codewiki.cli.models.config import" codewiki/
```

Replace with `from codewiki.src.codewiki_config import CodeWikiConfig`.

- [ ] **Step 3: Rewrite CLI config commands**

In `codewiki/cli/commands/config.py`, replace all JSON/keyring operations with TOML read/write:
- `config set` → read TOML, update key, write TOML
- `config get` → read TOML, print key
- `config validate` → `load_config()` + `validate_llm_credentials()`
- Legacy keyring paths → error with migration guidance

- [ ] **Step 4: Run full regression**

Run: `pytest tests/ -q -k "not network" --timeout=60`

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/config.py codewiki/src/config_loader.py codewiki/src/codewiki_config.py \
  codewiki/cli/commands/config.py codewiki/cli/commands/generate.py \
  codewiki/cli/adapters/doc_generator.py \
  codewiki/src/be/documentation_generator.py codewiki/src/be/llm_services.py \
  codewiki/src/be/agent_orchestrator.py codewiki/src/be/guide_generator.py \
  codewiki/src/fe/background_worker.py codewiki/src/fe/visualise_docs.py
git rm codewiki/cli/models/config.py codewiki/cli/config_manager.py
git commit -m "refactor(config)!: unify to CodeWikiConfig, remove legacy Config/ConfigManager

BREAKING CHANGE: ~/.codewiki/config.json and keyring are no longer supported.
Use config.toml with env: syntax for secrets."
```

---

## Phase 4: A5 — Async Boundary

### Task 7: Wrap sync route pipelines in asyncio.to_thread

**Files:**
- Modify: `codewiki/src/fe/routes.py`
- Modify: `codewiki/src/fe/visualise_docs.py`
- Create: `tests/test_async_routes.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_async_routes.py
import asyncio
import pytest


def test_routes_doc_view_uses_to_thread():
    """The doc view route must use asyncio.to_thread for sync I/O."""
    import inspect
    from codewiki.src.fe.routes import WebRoutes

    source = inspect.getsource(WebRoutes.view_docs)
    assert "to_thread" in source, (
        "view_docs route does not use asyncio.to_thread — "
        "sync I/O will block the event loop"
    )


def test_visualise_docs_uses_to_thread():
    """The visualise endpoint must use asyncio.to_thread for sync I/O."""
    import inspect
    from codewiki.src.fe.visualise_docs import app

    # Find the main doc-serving route handler
    for route in app.routes:
        if hasattr(route, "endpoint"):
            source = inspect.getsource(route.endpoint)
            if "markdown_to_html" in source:
                assert "to_thread" in source, (
                    "Visualise doc route does not use asyncio.to_thread"
                )
                return
    pytest.skip("No markdown-rendering route found")
```

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Refactor routes.py**

Extract the sync pipeline (lines 236-316 of `view_docs`) into a standalone sync function:

```python
def _load_and_render_doc(docs_path, filename, module_tree, metadata, repo_url, job_id):
    """Sync pipeline: load file, render markdown, build template context."""
    # ... all the sync I/O: file_manager.load_text, markdown_to_html,
    # find_module_doc, render_template ...
    return html_string
```

Then in the async route:
```python
async def view_docs(self, request, job_id, filename):
    # ... validate path, get docs_path (fast, no I/O) ...
    html = await asyncio.to_thread(
        _load_and_render_doc, docs_path, filename,
        module_tree, metadata, repo_url, job_id,
    )
    return HTMLResponse(content=html)
```

- [ ] **Step 4: Refactor visualise_docs.py**

Same pattern: extract sync rendering into a function, call via `to_thread`.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_async_routes.py -v`

- [ ] **Step 6: Run full regression**

Run: `pytest tests/ -q -k "not network" --timeout=60`

- [ ] **Step 7: Commit**

```bash
git add codewiki/src/fe/routes.py codewiki/src/fe/visualise_docs.py tests/test_async_routes.py
git commit -m "perf(web): wrap sync doc rendering in asyncio.to_thread"
```

---

### Task 8: Full regression verification

- [ ] **Step 1: Compile all new/modified files**

```bash
python3 -m py_compile codewiki/src/be/pipeline.py codewiki/src/codewiki_config.py codewiki/src/be/stages/graph_build.py codewiki/src/be/stages/index_build.py codewiki/src/be/stages/clustering.py codewiki/src/be/stages/state_init.py codewiki/src/be/stages/module_generation.py codewiki/src/be/stages/guide.py codewiki/src/be/stages/postprocess.py codewiki/src/be/stages/metadata.py codewiki/src/be/documentation_generator.py codewiki/src/fe/routes.py codewiki/src/fe/visualise_docs.py
```

- [ ] **Step 2: Run all new tests**

```bash
pytest -v tests/test_pipeline_types.py tests/test_pipeline_stages.py tests/test_scheduler_module_summary.py tests/test_codewiki_config.py tests/test_config_loader_unified.py tests/test_async_routes.py
```

- [ ] **Step 3: Run full regression**

```bash
make test
```

- [ ] **Step 4: Commit if fixes needed**

```bash
git commit -m "test: verify architecture audit fixes"
```
