from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_config(tmp_path):
    from codewiki.src.codewiki_config import CodeWikiConfig

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
        fallback_model=["test/fallback"],
        long_context_model="test/long",
        long_context_threshold=100,
    )


def test_create_agent_uses_long_context_model_for_large_prompt(tmp_path):
    """Agent gets long-context model when estimated tokens exceed threshold."""
    import codewiki.src.be.agent_orchestrator as orch_mod

    with (
        patch.object(orch_mod, "Agent", return_value=MagicMock()) as mock_agent,
    ):
        middleware = SimpleNamespace(create_agent_model=lambda: "middleware-model")
        orchestrator = orch_mod.AgentOrchestrator(_make_config(tmp_path), middleware=middleware)
        # threshold is 100 in test config
        orchestrator.create_agent("module", {}, [], estimated_tokens=1000)

    assert mock_agent.call_args.args[0] == "middleware-model"


def test_create_agent_uses_complex_toolset_without_submodule_tool(tmp_path):
    import codewiki.src.be.agent_orchestrator as orch_mod

    components = {
        "a": SimpleNamespace(file_path="a.py"),
        "b": SimpleNamespace(file_path="b.py"),
    }

    with (
        patch.object(orch_mod, "Agent", return_value=MagicMock()) as mock_agent,
    ):
        middleware = SimpleNamespace(create_agent_model=lambda: "middleware-model")
        orchestrator = orch_mod.AgentOrchestrator(_make_config(tmp_path), middleware=middleware)
        orchestrator.create_agent("module", components, ["a", "b"], estimated_tokens=0)

    tools = mock_agent.call_args.kwargs["tools"]
    assert len(tools) == 2
    assert not any(
        getattr(tool, "name", "") == "generate_sub_module_documentation" for tool in tools
    )


def test_summarize_child_doc_prefers_overview_section(tmp_path):
    import codewiki.src.be.agent_orchestrator as orch_mod

    content = """# ChildA

Implementation notes that should not be preferred.

## Overview

This is the real summary paragraph for the child module.

More detail follows here.
"""

    summary = orch_mod.AgentOrchestrator._summarize_child_doc(content)

    assert "Implementation notes that should not be preferred." in summary
    assert "This is the real summary paragraph for the child module." in summary
    assert "More detail follows here." in summary


def test_summarize_child_doc_supports_cjk_summary_headings(tmp_path):
    import codewiki.src.be.agent_orchestrator as orch_mod

    content = """# 子模块

这里是一些实现细节，不应该优先。

## 概述

这是这个子模块真正的总结段落，用来给父模块做摘要。

后面还有更多说明。
"""

    summary = orch_mod.AgentOrchestrator._summarize_child_doc(content)

    assert "这里是一些实现细节，不应该优先。" in summary
    assert "这是这个子模块真正的总结段落，用来给父模块做摘要。" in summary
    assert "后面还有更多说明。" in summary


def test_summarize_child_doc_uses_structure_not_heading_dictionary(tmp_path):
    import codewiki.src.be.agent_orchestrator as orch_mod

    content = """# Module enfant

Premiere phrase d'introduction pour le module.

## Aperçu rapide

Ce bloc explique le role du module et sa place dans le systeme.

Il contient encore une phrase utile pour le parent.

## Details internes

Cette section ne devrait pas etre preferee avant la section precedente.
"""

    summary = orch_mod.AgentOrchestrator._summarize_child_doc(content)

    assert "Premiere phrase d'introduction pour le module." in summary
    assert "Ce bloc explique le role du module et sa place dans le systeme." in summary
    assert "Il contient encore une phrase utile pour le parent." in summary


def test_summarize_child_doc_also_uses_multilingual_summary_headings(tmp_path):
    import codewiki.src.be.agent_orchestrator as orch_mod

    content = """# モジュール

最初の自然段落です。親モジュールに最低限渡したい導入です。

## 実装メモ

この節は詳細ですが、要約節ではありません。

## 概要

この節全体は要約節として必ず含めたい内容です。

二つ目の段落も同じく完全に保持されるべきです。

三つ目の段落も落としてはいけません。

四つ目の段落も含めます。

五つ目の段落があるので、構造ヒューリスティック単独なら落ちやすいケースです。

## さらに詳細

ここは不要です。
"""

    summary = orch_mod.AgentOrchestrator._summarize_child_doc(content)

    assert "最初の自然段落です。親モジュールに最低限渡したい導入です。" in summary
    assert "この節全体は要約節として必ず含めたい内容です。" in summary
    assert "二つ目の段落も同じく完全に保持されるべきです。" in summary
    assert "三つ目の段落も落としてはいけません。" in summary
    assert "四つ目の段落も含めます。" in summary
    assert "五つ目の段落があるので、構造ヒューリスティック単独なら落ちやすいケースです。" in summary


def test_summarize_child_doc_heading_dictionary_matches_accented_titles_too(tmp_path):
    import codewiki.src.be.agent_orchestrator as orch_mod

    content = """# Module

Opening paragraph that must always be included.

## Setup

Implementation detail.

## Internals

More implementation detail.

### Résumé

This later, deeper section should still be included because the heading itself is a summary marker.

Its second paragraph must also remain intact.
"""

    summary = orch_mod.AgentOrchestrator._summarize_child_doc(content)

    assert "Opening paragraph that must always be included." in summary
    assert (
        "This later, deeper section should still be included because the heading itself is a summary marker."
        in summary
    )
    assert "Its second paragraph must also remain intact." in summary


@pytest.mark.asyncio
async def test_process_module_returns_cached_for_leaf_docs(tmp_path):
    import codewiki.src.be.agent_orchestrator as orch_mod

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    doc_file = docs_dir / "leaf.md"
    doc_file.write_text("x" * 200, encoding="utf-8")

    with (
        patch.object(orch_mod, "find_module_doc", return_value=str(doc_file)),
    ):
        middleware = SimpleNamespace(create_agent_model=lambda: "middleware-model")
        orchestrator = orch_mod.AgentOrchestrator(_make_config(tmp_path), middleware=middleware)
        with patch.object(orchestrator, "create_agent") as mock_create_agent:
            module_tree, models_used = await orchestrator.process_module(
                module_name="leaf",
                components={"leaf": SimpleNamespace(file_path="a.py")},
                core_component_ids=["leaf"],
                module_path=["leaf"],
                working_dir=str(docs_dir),
            )

    assert module_tree == {}
    assert models_used == "cached"
    mock_create_agent.assert_not_called()


@pytest.mark.asyncio
async def test_process_module_success_records_usage_and_marks_completed(tmp_path):
    import codewiki.src.be.agent_orchestrator as orch_mod
    from codewiki.src.be.llm_usage import LLMUsageStats

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "module_tree.json").write_text("{}", encoding="utf-8")

    fake_usage = SimpleNamespace(input_tokens=11, output_tokens=7, requests=2)
    fake_result = SimpleNamespace(
        all_messages=lambda: [SimpleNamespace(model_name="test/main")],
        usage=lambda: fake_usage,
    )
    fake_agent = MagicMock()
    fake_agent.run = AsyncMock(return_value=fake_result)
    with (
        patch.object(orch_mod, "format_user_prompt", return_value="prompt"),
        patch.object(orch_mod, "build_context_pack", return_value={}),
        patch.object(orch_mod, "format_context_pack_section", return_value=""),
        patch.object(orch_mod, "count_tokens", return_value=10),
        patch.object(orch_mod, "file_manager") as mock_file_manager,
        patch.object(orch_mod, "ModelResponse", SimpleNamespace),
    ):
        mock_file_manager.load_json.return_value = {}
        middleware = SimpleNamespace(create_agent_model=lambda: "middleware-model")
        orchestrator = orch_mod.AgentOrchestrator(
            _make_config(tmp_path), middleware=middleware, usage_stats=LLMUsageStats()
        )
        with patch.object(orchestrator, "create_agent", return_value=fake_agent):
            module_tree, models_used = await orchestrator.process_module(
                module_name="module",
                components={"leaf": SimpleNamespace(file_path="a.py")},
                core_component_ids=["leaf"],
                module_path=["module"],
                working_dir=str(docs_dir),
            )

    assert module_tree == {}
    assert models_used == "test/main"
    assert orchestrator.usage_stats.total_input_tokens == 11
    assert orchestrator.usage_stats.total_output_tokens == 7
    mock_file_manager.save_json.assert_called_once()


@pytest.mark.asyncio
async def test_process_parent_module_uses_reduced_components_and_child_doc_context(tmp_path):
    import codewiki.src.be.agent_orchestrator as orch_mod

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "child-a.md").write_text(
        "# ChildA\n\nAlpha summary.\n\nMore detail.", encoding="utf-8"
    )
    (docs_dir / "child-b.md").write_text(
        "# ChildB\n\nBeta summary.\n\nMore detail.", encoding="utf-8"
    )

    module_tree = {
        "Parent": {
            "components": [
                "a1",
                "a2",
                "a3",
                "a4",
                "a5",
                "a6",
                "b1",
                "b2",
                "b3",
                "b4",
                "b5",
                "b6",
            ],
            "_doc_filename": "parent.md",
            "children": {
                "ChildA": {
                    "components": ["a1", "a2", "a3", "a4", "a5", "a6"],
                    "_doc_filename": "child-a.md",
                    "children": {},
                },
                "ChildB": {
                    "components": ["b1", "b2", "b3", "b4", "b5", "b6"],
                    "_doc_filename": "child-b.md",
                    "children": {},
                },
            },
        }
    }
    components = {
        "a1": SimpleNamespace(file_path="a1.py", relative_path="src/a1.py", depends_on={"b1"}),
        "a2": SimpleNamespace(file_path="a2.py", relative_path="src/a2.py", depends_on=set()),
        "a3": SimpleNamespace(file_path="a3.py", relative_path="src/a3.py", depends_on=set()),
        "a4": SimpleNamespace(file_path="a4.py", relative_path="src/a4.py", depends_on=set()),
        "a5": SimpleNamespace(file_path="a5.py", relative_path="src/a5.py", depends_on=set()),
        "a6": SimpleNamespace(file_path="a6.py", relative_path="src/a6.py", depends_on=set()),
        "b1": SimpleNamespace(file_path="b1.py", relative_path="src/b1.py", depends_on={"a1"}),
        "b2": SimpleNamespace(file_path="b2.py", relative_path="src/b2.py", depends_on=set()),
        "b3": SimpleNamespace(file_path="b3.py", relative_path="src/b3.py", depends_on=set()),
        "b4": SimpleNamespace(file_path="b4.py", relative_path="src/b4.py", depends_on=set()),
        "b5": SimpleNamespace(file_path="b5.py", relative_path="src/b5.py", depends_on=set()),
        "b6": SimpleNamespace(file_path="b6.py", relative_path="src/b6.py", depends_on=set()),
    }

    fake_result = SimpleNamespace(
        all_messages=lambda: [SimpleNamespace(model_name="test/main")],
        usage=lambda: None,
    )
    fake_agent = MagicMock()
    fake_agent.run = AsyncMock(return_value=fake_result)

    with (
        patch.object(orch_mod, "find_module_doc", return_value=None),
        patch.object(orch_mod, "build_context_pack", return_value={}),
        patch.object(orch_mod, "format_context_pack_section", return_value=""),
        patch.object(orch_mod, "count_tokens", return_value=10),
        patch.object(orch_mod.file_manager, "load_json", return_value=module_tree),
        patch.object(orch_mod, "ModelResponse", SimpleNamespace),
        patch.object(orch_mod, "format_user_prompt", return_value="prompt") as mock_format,
    ):
        middleware = SimpleNamespace(create_agent_model=lambda: "middleware-model")
        orchestrator = orch_mod.AgentOrchestrator(_make_config(tmp_path), middleware=middleware)
        with patch.object(orchestrator, "create_agent", return_value=fake_agent):
            await orchestrator.process_module(
                module_name="Parent",
                components=components,
                core_component_ids=module_tree["Parent"]["components"],
                module_path=["Parent"],
                working_dir=str(docs_dir),
            )

    effective_ids = mock_format.call_args.kwargs["core_component_ids"]
    assert "a1" in effective_ids
    assert "b1" in effective_ids
    assert len(effective_ids) < len(module_tree["Parent"]["components"])

    prompt_arg = fake_agent.run.await_args.args[0]
    assert "CHILD_MODULE_DOC_SUMMARIES" in prompt_arg
    assert "Alpha summary." in prompt_arg
    assert "Beta summary." in prompt_arg
