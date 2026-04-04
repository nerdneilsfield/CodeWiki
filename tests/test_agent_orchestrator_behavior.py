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
