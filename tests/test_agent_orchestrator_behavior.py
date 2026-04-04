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
        fallback_model="test/fallback",
        long_context_model="test/long",
        long_context_threshold=100,
    )


def test_create_agent_uses_long_context_model_for_large_prompt(tmp_path):
    import codewiki.src.be.agent_orchestrator as orch_mod

    with (
        patch.object(orch_mod, "create_fallback_models", return_value="fallback-model"),
        patch.object(orch_mod, "create_long_context_model", return_value="long-model"),
        patch.object(orch_mod, "Agent", return_value=MagicMock()) as mock_agent,
    ):
        orchestrator = orch_mod.AgentOrchestrator(_make_config(tmp_path))
        orchestrator.create_agent("module", {}, [], estimated_tokens=1000)

    assert mock_agent.call_args.args[0] == "long-model"


def test_create_agent_uses_complex_toolset_for_multi_file_module(tmp_path):
    import codewiki.src.be.agent_orchestrator as orch_mod

    components = {
        "a": SimpleNamespace(file_path="a.py"),
        "b": SimpleNamespace(file_path="b.py"),
    }

    with (
        patch.object(orch_mod, "create_fallback_models", return_value="fallback-model"),
        patch.object(orch_mod, "create_long_context_model", return_value="long-model"),
        patch.object(orch_mod, "Agent", return_value=MagicMock()) as mock_agent,
    ):
        orchestrator = orch_mod.AgentOrchestrator(_make_config(tmp_path))
        orchestrator.create_agent("module", components, ["a", "b"], estimated_tokens=0)

    tools = mock_agent.call_args.kwargs["tools"]
    assert len(tools) == 3
    assert any(getattr(tool, "name", "") == "generate_sub_module_documentation" for tool in tools)


@pytest.mark.asyncio
async def test_process_module_returns_cached_for_leaf_docs(tmp_path):
    import codewiki.src.be.agent_orchestrator as orch_mod

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    doc_file = docs_dir / "leaf.md"
    doc_file.write_text("x" * 200, encoding="utf-8")

    with (
        patch.object(orch_mod, "create_fallback_models", return_value="fallback-model"),
        patch.object(orch_mod, "create_long_context_model", return_value="long-model"),
        patch.object(orch_mod, "find_module_doc", return_value=str(doc_file)),
    ):
        orchestrator = orch_mod.AgentOrchestrator(_make_config(tmp_path))
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
    state_mgr = AsyncMock()

    with (
        patch.object(orch_mod, "create_fallback_models", return_value="fallback-model"),
        patch.object(orch_mod, "create_long_context_model", return_value="long-model"),
        patch.object(orch_mod, "format_user_prompt", return_value="prompt"),
        patch.object(orch_mod, "build_context_pack", return_value={}),
        patch.object(orch_mod, "format_context_pack_section", return_value=""),
        patch.object(orch_mod, "count_tokens", return_value=10),
        patch.object(orch_mod, "file_manager") as mock_file_manager,
        patch.object(orch_mod, "ModelResponse", SimpleNamespace),
    ):
        mock_file_manager.load_json.return_value = {}
        orchestrator = orch_mod.AgentOrchestrator(
            _make_config(tmp_path), usage_stats=LLMUsageStats()
        )
        with patch.object(orchestrator, "create_agent", return_value=fake_agent):
            module_tree, models_used = await orchestrator.process_module(
                module_name="module",
                components={"leaf": SimpleNamespace(file_path="a.py")},
                core_component_ids=["leaf"],
                module_path=["module"],
                working_dir=str(docs_dir),
                state_mgr=state_mgr,
            )

    assert module_tree == {}
    assert models_used == "test/main"
    assert orchestrator.usage_stats.total_input_tokens == 11
    assert orchestrator.usage_stats.total_output_tokens == 7
    state_mgr.mark_completed.assert_awaited_once()
    mock_file_manager.save_json.assert_called_once()
