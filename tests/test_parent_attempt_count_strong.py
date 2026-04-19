from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from codewiki.src.be.documentation_generator import DocumentationGenerator
from codewiki.src.be.llm_usage import LLMCallResult
from codewiki.src.codewiki_config import CodeWikiConfig, RefinementConfig


def test_parent_attempt_count_is_exactly_one(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    gen = DocumentationGenerator(
        CodeWikiConfig(
            repo_path=str(tmp_path / "repo"),
            docs_dir=str(docs_dir),
            llm_base_url="http://localhost",
            llm_api_key="x",
            main_model="m",
            cluster_model="c",
            output_language="en",
            refinement=RefinementConfig(
                max_depth=2,
                min_components_for_split=2,
                min_distinct_files_for_split=2,
            ),
        ),
        commit_id="testcommit",
    )

    components = {
        "a.py::A": MagicMock(
            file_path="a.py", relative_path="a.py", source_code="x", depends_on=set()
        ),
        "b.py::B": MagicMock(
            file_path="b.py", relative_path="b.py", source_code="y", depends_on=set()
        ),
        "c.py::C": MagicMock(
            file_path="c.py", relative_path="c.py", source_code="z", depends_on=set()
        ),
        "d.py::D": MagicMock(
            file_path="d.py", relative_path="d.py", source_code="w", depends_on=set()
        ),
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

    refinement_response = LLMCallResult(
        content='{"should_split": true, "children": {"Left": {"module_id": "left", "title": "Left", "path": "left", "description": ".", "components": ["a.py::A", "b.py::B"]}, "Right": {"module_id": "right", "title": "Right", "path": "right", "description": ".", "components": ["c.py::C", "d.py::D"]}}}',
        usage=None,
        model="fake",
    )
    no_split_response = LLMCallResult(
        content='{"should_split": false, "children": {}}',
        usage=None,
        model="fake",
    )

    def fake_call(prompt, model=None, temperature=0.0, **_kwargs):
        if "You are refining a software module" in prompt:
            if "Parent module: Top" in prompt:
                return refinement_response
            return no_split_response
        return LLMCallResult(content="GENERATED", usage=None, model=model or "fake")

    async def fake_process_module(
        module_name,
        components,
        core_component_ids,
        module_path,
        working_dir,
        tree_manager,
        **_kwargs,
    ):
        doc_name = "left.md" if module_name == "Left" else "right.md"
        (docs_dir / doc_name).write_text(f"# {module_name}\n\nGenerated.\n", encoding="utf-8")
        return {}, "leaf-model"

    with (
        patch.object(
            gen.graph_builder,
            "build_dependency_graph",
            return_value=(components, list(components.keys())),
        ),
        patch("codewiki.src.be.documentation_generator.cluster_modules", return_value=cluster_tree),
        patch(
            "codewiki.src.be.documentation_generator.heal_module_tree_components",
            return_value=cluster_tree,
        ),
        patch.object(gen.middleware, "call", new=fake_call),
        patch("codewiki.src.be.stages.index_build.IndexBuildStage.execute", new=AsyncMock()),
        patch("codewiki.src.be.stages.guide.GuideStage.execute", new=AsyncMock()),
        patch("codewiki.src.be.stages.postprocess.PostprocessStage.execute", new=AsyncMock()),
        patch.object(
            gen.agent_orchestrator, "process_module", new=AsyncMock(side_effect=fake_process_module)
        ),
    ):
        asyncio.run(gen.run())

    top_entry = gen.cache_manager.get_entry("module:top")
    assert top_entry is not None
    assert top_entry.attempt_count == 1

    asyncio.run(gen.run())

    top_entry_after = gen.cache_manager.get_entry("module:top")
    assert top_entry_after is not None
    assert top_entry_after.attempt_count == 1
