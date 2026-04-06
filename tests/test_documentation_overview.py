import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import warnings

import pytest

from codewiki.src.be.cache_manager import CacheManager
from codewiki.src.be.documentation_tree_utils import compute_module_input_hash, stable_hash
from codewiki.src.be.generation_state import DocTask, GenerationState
from codewiki.src.be.llm_usage import LLMCallResult, LLMCallUsage, LLMUsageStats
from codewiki.src.be.prompt_template import PROMPT_VERSION
from codewiki.src.codewiki_config import CodeWikiConfig


def _make_config(tmp_path):
    return CodeWikiConfig(
        repo_path=str(tmp_path / "repo"),
        output_dir=str(tmp_path / "out"),
        dependency_graph_dir=str(tmp_path / "graphs"),
        docs_dir=str(tmp_path / "docs"),
        max_depth=2,
        llm_base_url="http://localhost",
        llm_api_key="x",
        main_model="test/main",
        cluster_model="test/cluster",
        output_language="zh",
    )


def _segment_hash(child_hash: str) -> str:
    return stable_hash([child_hash, PROMPT_VERSION])


def test_collect_child_doc_hashes_prefers_ledger_hashes(tmp_path):
    from codewiki.src.be.documentation_overview import OverviewContext, collect_child_doc_hashes

    tree = {
        "CLI Transport": {
            "module_id": "mod-cli",
            "children": {
                "io_abstractions": {
                    "module_id": "mod-io",
                    "children": {},
                }
            },
        }
    }
    state = GenerationState()
    state._add_task(
        DocTask(
            doc_id="module:mod-io",
            kind="module",
            module_path=["CLI Transport", "io_abstractions"],
            output_file="cli-io.md",
            status="completed",
            content_hash="hash-from-ledger",
        )
    )
    ctx = OverviewContext(
        config=_make_config(tmp_path),
        module_tree=tree,
        working_dir=str(tmp_path),
        gen_state=state,
    )

    hashes = collect_child_doc_hashes(ctx, ["CLI Transport"])

    assert hashes == {"io_abstractions": "hash-from-ledger"}


def test_build_overview_structure_uses_child_docs_from_ledger(tmp_path):
    from codewiki.src.be.documentation_overview import OverviewContext, build_overview_structure

    tree = {
        "CLI Transport": {
            "module_id": "mod-cli",
            "children": {
                "io_abstractions": {
                    "module_id": "mod-io",
                    "children": {},
                }
            },
        }
    }
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "cli-io.md").write_text("# IO\nDetails", encoding="utf-8")
    state = GenerationState()
    state._add_task(
        DocTask(
            doc_id="module:mod-io",
            kind="module",
            module_path=["CLI Transport", "io_abstractions"],
            output_file="cli-io.md",
            status="completed",
        )
    )
    ctx = OverviewContext(
        config=_make_config(tmp_path),
        module_tree=tree,
        working_dir=str(docs_dir),
        gen_state=state,
    )

    result = build_overview_structure(ctx, ["CLI Transport"])

    assert result["CLI Transport"]["children"]["io_abstractions"]["docs"] == "# IO\nDetails"


@pytest.mark.asyncio
async def test_generate_parent_module_docs_skips_when_input_hash_matches(tmp_path, monkeypatch):
    from codewiki.src.be.documentation_overview import OverviewContext, generate_parent_module_docs

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    output_path = docs_dir / "overview.md"
    output_path.write_text("existing overview\n" + ("x" * 200), encoding="utf-8")

    tree = {"CLI Transport": {"module_id": "mod-cli", "children": {}}}
    state = GenerationState()
    state._add_task(
        DocTask(
            doc_id="overview:root",
            kind="overview",
            module_path=[],
            output_file="overview.md",
            status="completed",
            input_hash="same-hash",
        )
    )

    ctx = OverviewContext(
        config=_make_config(tmp_path),
        module_tree=tree,
        working_dir=str(docs_dir),
        gen_state=state,
        middleware=SimpleNamespace(
            call=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("should not call llm")
            )
        ),
    )

    monkeypatch.setattr(
        "codewiki.src.be.documentation_overview.hash_mapping",
        lambda mapping, extra=None: "same-hash",
    )

    result = await generate_parent_module_docs(ctx, [])

    assert result == tree
    assert output_path.read_text(encoding="utf-8").startswith("existing overview")


@pytest.mark.asyncio
async def test_generate_parent_module_docs_marks_completed_after_write(tmp_path):
    from codewiki.src.be.documentation_overview import OverviewContext, generate_parent_module_docs
    from codewiki.src.be.generation_state import GenerationStateManager

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    tree = {"CLI Transport": {"module_id": "mod-cli", "children": {}}}
    state = GenerationState()
    state._add_task(
        DocTask(
            doc_id="overview:root",
            kind="overview",
            module_path=[],
            output_file="overview.md",
            status="ready",
        )
    )
    manager = GenerationStateManager(state, str(docs_dir / "generation_state.json"))

    def _call(_prompt):
        return SimpleNamespace(content="<OVERVIEW>\nGenerated content\n</OVERVIEW>")

    ctx = OverviewContext(
        config=_make_config(tmp_path),
        module_tree=tree,
        working_dir=str(docs_dir),
        gen_state=state,
        state_mgr=manager,
        middleware=SimpleNamespace(call=_call),
    )

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        result = await generate_parent_module_docs(ctx, [])

    assert result == tree
    assert (docs_dir / "overview.md").read_text(encoding="utf-8") == "Generated content"
    assert state.get_task("overview:root").status == "completed"
    deprecations = [w for w in captured if issubclass(w.category, DeprecationWarning)]
    assert deprecations == []


@pytest.mark.asyncio
async def test_generate_parent_module_docs_records_usage_stats(tmp_path):
    from codewiki.src.be.documentation_overview import OverviewContext, generate_parent_module_docs
    from codewiki.src.be.llm_middleware import LLMMiddleware

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    tree = {"CLI Transport": {"module_id": "mod-cli", "children": {}}}
    usage_stats = LLMUsageStats()
    middleware = LLMMiddleware(_make_config(tmp_path), usage_stats=usage_stats)

    ctx = OverviewContext(
        config=_make_config(tmp_path),
        module_tree=tree,
        working_dir=str(docs_dir),
        usage_stats=usage_stats,
        middleware=middleware,
    )

    with patch(
        "codewiki.src.be.llm_middleware.raw_llm_call",
        return_value=LLMCallResult(
            content="<OVERVIEW>\nGenerated content\n</OVERVIEW>",
            usage=LLMCallUsage(input_tokens=11, output_tokens=7),
            model="test/main",
        ),
    ):
        await generate_parent_module_docs(ctx, [])

    assert usage_stats.to_dict() == {
        "total_input_tokens": 11,
        "total_output_tokens": 7,
        "total_requests": 1,
        "by_model": {"test/main": {"input": 11, "output": 7, "requests": 1}},
    }


@pytest.mark.asyncio
async def test_generate_parent_module_docs_cache_manager_skips_when_segments_valid(
    tmp_path,
):
    from codewiki.src.be.documentation_overview import OverviewContext, generate_parent_module_docs

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    tree = {
        "Parent": {
            "module_id": "mod-parent",
            "children": {
                "ChildA": {"module_id": "mod-a", "components": ["a"], "children": {}},
                "ChildB": {"module_id": "mod-b", "components": ["b"], "children": {}},
            },
        }
    }
    components = {
        "a": SimpleNamespace(source_code="print('a')"),
        "b": SimpleNamespace(source_code="print('b')"),
    }
    config = _make_config(tmp_path)
    cache_manager = CacheManager(str(docs_dir / ".codewiki"))

    (docs_dir / "child-a.md").write_text("# ChildA\nbody", encoding="utf-8")
    (docs_dir / "child-b.md").write_text("# ChildB\nbody", encoding="utf-8")
    output_path = docs_dir / "mod-parent.md"
    output_path.write_text("existing overview\n" + ("x" * 200), encoding="utf-8")

    child_a_hash = compute_module_input_hash(
        "ChildA",
        ["Parent", "ChildA"],
        tree["Parent"]["children"]["ChildA"],
        components,
        config,
        assigned_file="child-a.md",
    )
    child_b_hash = compute_module_input_hash(
        "ChildB",
        ["Parent", "ChildB"],
        tree["Parent"]["children"]["ChildB"],
        components,
        config,
        assigned_file="child-b.md",
    )
    cache_manager.mark_done(
        "module:mod-a",
        input_hash=child_a_hash,
        output_path=str(docs_dir / "child-a.md"),
        output_file="child-a.md",
    )
    cache_manager.mark_done(
        "module:mod-b",
        input_hash=child_b_hash,
        output_path=str(docs_dir / "child-b.md"),
        output_file="child-b.md",
    )

    parts_dir = docs_dir / ".codewiki" / "_overview_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    arch_content = "arch section"
    child_a_content = "### ChildA\ncached summary"
    child_b_content = "### ChildB\ncached summary"
    (parts_dir / "overview_module_mod-parent_arch_intro.md").write_text(
        arch_content, encoding="utf-8"
    )
    (parts_dir / "overview_module_mod-parent_child_mod-a.md").write_text(
        child_a_content, encoding="utf-8"
    )
    (parts_dir / "overview_module_mod-parent_child_mod-b.md").write_text(
        child_b_content, encoding="utf-8"
    )

    arch_hash = stable_hash(
        [
            "module:mod-a",
            "module:mod-b",
            _segment_hash(child_a_hash),
            _segment_hash(child_b_hash),
            config.output_language,
            PROMPT_VERSION,
        ]
    )
    cache_manager.mark_done(
        "overview:module:mod-parent:arch_intro",
        input_hash=arch_hash,
        output_path=str(parts_dir / "overview_module_mod-parent_arch_intro.md"),
        output_file="overview_module_mod-parent_arch_intro.md",
    )
    cache_manager.mark_done(
        "overview:module:mod-parent:child:module:mod-a",
        input_hash=_segment_hash(child_a_hash),
        output_path=str(parts_dir / "overview_module_mod-parent_child_mod-a.md"),
        output_file="overview_module_mod-parent_child_mod-a.md",
        depends_on=["module:mod-a"],
    )
    cache_manager.mark_done(
        "overview:module:mod-parent:child:module:mod-b",
        input_hash=_segment_hash(child_b_hash),
        output_path=str(parts_dir / "overview_module_mod-parent_child_mod-b.md"),
        output_file="overview_module_mod-parent_child_mod-b.md",
        depends_on=["module:mod-b"],
    )
    cache_manager.mark_done(
        "overview:module:mod-parent",
        input_hash=stable_hash(
            [
                arch_hash,
                _segment_hash(child_a_hash),
                _segment_hash(child_b_hash),
                PROMPT_VERSION,
            ]
        ),
        output_path=str(output_path),
        output_file="mod-parent.md",
        depends_on=[
            "overview:module:mod-parent:arch_intro",
            "overview:module:mod-parent:child:module:mod-a",
            "overview:module:mod-parent:child:module:mod-b",
        ],
    )

    ctx = OverviewContext(
        config=config,
        module_tree=tree,
        working_dir=str(docs_dir),
        middleware=SimpleNamespace(
            call=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("should not call llm")
            )
        ),
        cache_manager=cache_manager,
    )

    result = await generate_parent_module_docs(ctx, ["Parent"])

    assert result == tree
    assert output_path.read_text(encoding="utf-8").startswith("existing overview")


@pytest.mark.asyncio
async def test_generate_parent_module_docs_cache_manager_regenerates_only_stale_segments(
    tmp_path,
):
    from codewiki.src.be.documentation_overview import OverviewContext, generate_parent_module_docs

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    tree = {
        "Parent": {
            "module_id": "mod-parent",
            "children": {
                "ChildA": {"module_id": "mod-a", "components": ["a"], "children": {}},
                "ChildB": {"module_id": "mod-b", "components": ["b"], "children": {}},
            },
        }
    }
    components = {
        "a": SimpleNamespace(source_code="print('a')"),
        "b": SimpleNamespace(source_code="print('b changed')"),
    }
    config = _make_config(tmp_path)
    cache_manager = CacheManager(str(docs_dir / ".codewiki"))
    cache_manager.OVERVIEW_REGENERATE_THRESHOLD = 0.8
    cache_manager.plan_task("overview:module:mod-parent", output_file="mod-parent.md")

    (docs_dir / "child-a.md").write_text("# ChildA\nbody", encoding="utf-8")
    (docs_dir / "child-b.md").write_text("# ChildB\nbody", encoding="utf-8")

    child_a_hash = compute_module_input_hash(
        "ChildA",
        ["Parent", "ChildA"],
        tree["Parent"]["children"]["ChildA"],
        components,
        config,
        assigned_file="child-a.md",
    )
    child_b_hash = compute_module_input_hash(
        "ChildB",
        ["Parent", "ChildB"],
        tree["Parent"]["children"]["ChildB"],
        components,
        config,
        assigned_file="child-b.md",
    )
    cache_manager.mark_done(
        "module:mod-a",
        input_hash=child_a_hash,
        output_path=str(docs_dir / "child-a.md"),
        output_file="child-a.md",
    )
    cache_manager.mark_done(
        "module:mod-b",
        input_hash=child_b_hash,
        output_path=str(docs_dir / "child-b.md"),
        output_file="child-b.md",
    )

    parts_dir = docs_dir / ".codewiki" / "_overview_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    (parts_dir / "overview_module_mod-parent_child_mod-a.md").write_text(
        "### ChildA\ncached summary", encoding="utf-8"
    )
    cache_manager.mark_done(
        "overview:module:mod-parent:child:module:mod-a",
        input_hash=_segment_hash(child_a_hash),
        output_path=str(parts_dir / "overview_module_mod-parent_child_mod-a.md"),
        output_file="overview_module_mod-parent_child_mod-a.md",
        depends_on=["module:mod-a"],
    )

    prompts: list[str] = []

    def _call(prompt: str):
        prompts.append(prompt)
        if "architecture introduction" in prompt:
            return SimpleNamespace(content="generated arch intro")
        if '"ChildB"' in prompt:
            return SimpleNamespace(content="### ChildB\ngenerated summary")
        raise AssertionError(prompt)

    ctx = OverviewContext(
        config=config,
        module_tree=tree,
        working_dir=str(docs_dir),
        middleware=SimpleNamespace(call=_call),
        cache_manager=cache_manager,
    )

    result = await generate_parent_module_docs(ctx, ["Parent"])

    assert result == tree
    assert len(prompts) == 2
    overview_text = (docs_dir / "mod-parent.md").read_text(encoding="utf-8")
    assert "generated arch intro" in overview_text
    assert "### ChildA\ncached summary" in overview_text
    assert "### ChildB\ngenerated summary" in overview_text
    parent_hash = cache_manager.get_input_hash("overview:module:mod-parent")
    arch_hash = cache_manager.get_input_hash("overview:module:mod-parent:arch_intro")
    assert parent_hash == stable_hash(
        [
            arch_hash,
            _segment_hash(child_a_hash),
            _segment_hash(child_b_hash),
            PROMPT_VERSION,
        ]
    )


@pytest.mark.asyncio
async def test_generate_parent_module_docs_cache_hit_requires_segment_files(
    tmp_path,
):
    from codewiki.src.be.documentation_overview import OverviewContext, generate_parent_module_docs

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    tree = {
        "Parent": {
            "module_id": "mod-parent",
            "children": {
                "ChildA": {"module_id": "mod-a", "components": ["a"], "children": {}},
            },
        }
    }
    components = {"a": SimpleNamespace(source_code="print('a')")}
    config = _make_config(tmp_path)
    cache_manager = CacheManager(str(docs_dir / ".codewiki"))
    cache_manager.plan_task("overview:module:mod-parent", output_file="mod-parent.md")
    (docs_dir / "child-a.md").write_text("# ChildA\nbody", encoding="utf-8")
    (docs_dir / "mod-parent.md").write_text("existing overview\n" + ("x" * 200), encoding="utf-8")

    child_hash = compute_module_input_hash(
        "ChildA",
        ["Parent", "ChildA"],
        tree["Parent"]["children"]["ChildA"],
        components,
        config,
        assigned_file="child-a.md",
    )
    cache_manager.mark_done(
        "module:mod-a",
        input_hash=child_hash,
        output_path=str(docs_dir / "child-a.md"),
        output_file="child-a.md",
    )
    parts_dir = docs_dir / ".codewiki" / "_overview_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    arch_path = parts_dir / "overview_module_mod-parent_arch_intro.md"
    child_path = parts_dir / "overview_module_mod-parent_child_module_mod-a.md"
    arch_path.write_text("arch section", encoding="utf-8")
    child_path.write_text("### ChildA\ncached summary", encoding="utf-8")
    arch_hash = stable_hash(
        ["module:mod-a", _segment_hash(child_hash), config.output_language, PROMPT_VERSION]
    )
    cache_manager.mark_done(
        "overview:module:mod-parent:arch_intro",
        input_hash=arch_hash,
        output_path=str(arch_path),
        output_file=arch_path.name,
    )
    cache_manager.mark_done(
        "overview:module:mod-parent:child:module:mod-a",
        input_hash=_segment_hash(child_hash),
        output_path=str(child_path),
        output_file=child_path.name,
        depends_on=["module:mod-a"],
    )
    cache_manager.mark_done(
        "overview:module:mod-parent",
        input_hash=stable_hash([arch_hash, _segment_hash(child_hash), PROMPT_VERSION]),
        output_path=str(docs_dir / "mod-parent.md"),
        output_file="mod-parent.md",
        depends_on=[
            "overview:module:mod-parent:arch_intro",
            "overview:module:mod-parent:child:module:mod-a",
        ],
    )
    child_path.unlink()

    prompts: list[str] = []

    def _call(prompt: str):
        prompts.append(prompt)
        if '"ChildA"' in prompt:
            return SimpleNamespace(content="### ChildA\nregenerated summary")
        if "architecture introduction" in prompt:
            return SimpleNamespace(content="arch section")
        raise AssertionError(prompt)

    ctx = OverviewContext(
        config=config,
        module_tree=tree,
        working_dir=str(docs_dir),
        middleware=SimpleNamespace(call=_call),
        cache_manager=cache_manager,
    )

    await generate_parent_module_docs(ctx, ["Parent"])

    assert any('"ChildA"' in prompt for prompt in prompts)
    assert "regenerated summary" in (docs_dir / "mod-parent.md").read_text(encoding="utf-8")
