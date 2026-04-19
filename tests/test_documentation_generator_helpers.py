import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

from codewiki.src.be.pipeline import GenerationResult
from codewiki.src.codewiki_config import CodeWikiConfig


def _make_generator(tmp_path):
    from codewiki.src.be.documentation_generator import DocumentationGenerator

    return DocumentationGenerator(
        CodeWikiConfig(
            repo_path=str(tmp_path / "repo"),
            output_dir=str(tmp_path / "out"),
            dependency_graph_dir=str(tmp_path / "graphs"),
            docs_dir=str(tmp_path / "docs"),
            max_depth=2,
            llm_base_url="http://localhost",
            llm_api_key="x",
            main_model="test/main",
            cluster_model="test/cluster",
            output_language="en",
        ),
        commit_id="abc123",
    )


def test_create_documentation_metadata_includes_usage_and_generated_status(tmp_path):
    from codewiki.src.be.llm_usage import LLMUsageStats

    gen = _make_generator(tmp_path)
    working_dir = tmp_path / "docs"
    working_dir.mkdir()
    (working_dir / "overview.md").write_text("# Overview", encoding="utf-8")

    usage = LLMUsageStats()
    usage.record("openai/gpt-4o", 10, 5)
    result = GenerationResult(status="degraded", warnings=["guide failed"])

    metadata = gen.create_documentation_metadata(
        str(working_dir),
        {"a": {}, "b": {}},
        1,
        usage_stats=usage,
        generation_result=result,
    )

    assert metadata["generation_info"]["commit_id"] == "abc123"
    assert metadata["statistics"]["total_components"] == 2
    assert metadata["statistics"]["token_usage"]["total_input_tokens"] == 10
    assert metadata["generation_status"] == "degraded"
    assert "overview.md" in metadata["files_generated"]


def test_build_initial_context_carries_commit_and_cancel_token(tmp_path):
    from codewiki.src.be.cancellation import CancellationToken

    gen = _make_generator(tmp_path)
    gen.cancel_token = CancellationToken()

    ctx = gen._build_initial_context()

    assert ctx.commit_id == "abc123"
    assert ctx.cancel_token is gen.cancel_token
    assert ctx.generator is gen
    assert ctx.usage_stats is gen.usage_stats


def test_build_index_uses_index_builder_output(tmp_path):
    gen = _make_generator(tmp_path)
    fake_products = MagicMock()

    with patch("codewiki.src.be.index.index_builder.IndexBuilder") as builder_cls:
        builder_cls.return_value.build.return_value = fake_products
        ctx = gen._build_initial_context()
        asyncio.run(gen._build_index(ctx))

    assert ctx.index_products is fake_products


def test_generate_guides_instantiates_guide_generator_with_cancel_token(tmp_path):
    gen = _make_generator(tmp_path)
    ctx = gen._build_initial_context()
    ctx.components = {"comp": {"file_path": "a.py"}}
    ctx.module_tree = {"Root": {"children": {}}}
    ctx.working_dir = str(tmp_path / "docs")

    with patch("codewiki.src.be.documentation_generator.GuideGenerator") as guide_cls:
        guide_cls.return_value.run = AsyncMock()
        asyncio.run(gen._generate_guides(ctx))

    kwargs = guide_cls.call_args.kwargs
    assert kwargs["cancel_token"] is gen.cancel_token
    guide_cls.return_value.run.assert_awaited_once()


def test_postprocess_docs_forwards_usage_stats(tmp_path):
    gen = _make_generator(tmp_path)
    ctx = gen._build_initial_context()
    ctx.working_dir = str(tmp_path / "docs")

    with patch("codewiki.src.be.docs_fixer.fix_docs") as fix_docs:
        gen._postprocess_docs(ctx)

    fix_docs.assert_called_once_with(
        ctx.working_dir,
        gen.config,
        usage_stats=gen.usage_stats,
        middleware=gen.middleware,
        cache_manager=gen.cache_manager,
    )


def test_generate_docs_from_tree_small_repo_renames_overview(tmp_path):
    gen = _make_generator(tmp_path)
    working_dir = tmp_path / "docs"
    working_dir.mkdir()
    repo_name = os.path.basename(os.path.normpath(gen.config.repo_path))
    generated_path = working_dir / f"{repo_name}.md"
    generated_path.write_text("# Repo", encoding="utf-8")

    gen.agent_orchestrator.process_module = AsyncMock(return_value=({"Root": {}}, ["test/main"]))

    out_dir, summary = asyncio.run(
        gen._generate_docs_from_tree({"comp": {}}, ["comp"], str(working_dir), {})
    )

    assert out_dir == str(working_dir)
    assert summary.total == 0
    assert (working_dir / "overview.md").exists()
    assert not generated_path.exists()


def test_run_re_raises_pipeline_failures(tmp_path):
    gen = _make_generator(tmp_path)

    async def _boom(_ctx):
        raise RuntimeError("pipeline exploded")

    with patch("codewiki.src.be.pipeline.PipelineRunner.execute", side_effect=_boom):
        try:
            asyncio.run(gen.run())
        except RuntimeError as exc:
            assert "pipeline exploded" in str(exc)
        else:
            raise AssertionError("Expected RuntimeError")


def test_cluster_modules_uses_cached_tree_when_commit_matches(tmp_path):
    from codewiki.src.be.documentation_tree_utils import config_fingerprint
    from codewiki.src.config import FIRST_MODULE_TREE_FILENAME, MODULE_TREE_FILENAME

    gen = _make_generator(tmp_path)
    ctx = gen._build_initial_context()
    ctx.components = {"comp": {"file_path": "a.py"}}
    ctx.leaf_nodes = ["comp"]
    ctx.index_products = MagicMock()
    cached_tree = {"Root": {"components": ["comp"], "children": {}}}
    gen.cache_manager.update_metadata(
        repo_commit="abc123",
        config_fingerprint=config_fingerprint(gen.config),
    )

    def _exists(path: str) -> bool:
        return path.endswith(FIRST_MODULE_TREE_FILENAME)

    with (
        patch("codewiki.src.be.documentation_generator.file_manager.ensure_directory"),
        patch("codewiki.src.be.documentation_generator.os.path.exists", side_effect=_exists),
        patch(
            "codewiki.src.be.documentation_generator.file_manager.load_json",
            return_value=cached_tree,
        ),
        patch(
            "codewiki.src.be.documentation_generator.heal_module_tree_components",
            return_value=cached_tree,
        ) as heal,
        patch("codewiki.src.be.documentation_generator.cluster_modules") as cluster,
        patch(
            "codewiki.src.be.documentation_generator.freeze_doc_filenames"
        ) as freeze_in_clustering,
        patch("codewiki.src.be.documentation_generator.file_manager.save_json") as save_json,
        patch("codewiki.src.be.generation.glossary.build_glossary", return_value={"term": "def"}),
        patch("codewiki.src.be.generation.glossary.build_link_map", return_value={"doc": "link"}),
    ):
        asyncio.run(gen._cluster_modules(ctx))

    # Same config → cluster not called; heal called with available components
    heal.assert_called_once_with(cached_tree, ctx.components)
    cluster.assert_not_called()
    freeze_in_clustering.assert_not_called()
    assert ctx.module_tree == cached_tree
    saved_basenames = [os.path.basename(call.args[1]) for call in save_json.call_args_list]
    assert FIRST_MODULE_TREE_FILENAME in saved_basenames
    assert MODULE_TREE_FILENAME not in saved_basenames


def test_cluster_modules_reclusters_and_warns_when_context_setup_fails(tmp_path):
    from codewiki.src.config import FIRST_MODULE_TREE_FILENAME

    gen = _make_generator(tmp_path)
    ctx = gen._build_initial_context()
    ctx.components = {"comp": {"file_path": "a.py"}}
    ctx.leaf_nodes = ["comp"]
    ctx.index_products = MagicMock()
    reclustered = {"Grouped": {"components": ["comp"], "children": {}}}

    with (
        patch("codewiki.src.be.documentation_generator.file_manager.ensure_directory"),
        patch(
            "codewiki.src.be.documentation_generator.os.path.exists",
            side_effect=lambda path: path.endswith(FIRST_MODULE_TREE_FILENAME),
        ),
        patch(
            "codewiki.src.be.documentation_generator.file_manager.load_json",
            return_value={"old": {}},
        ),
        patch(
            "codewiki.src.be.documentation_generator.cluster_modules", return_value=reclustered
        ) as cluster,
        patch(
            "codewiki.src.be.documentation_generator.freeze_doc_filenames"
        ) as freeze_in_clustering,
        patch("codewiki.src.be.documentation_generator.file_manager.save_json"),
        patch("codewiki.src.be.generation.glossary.build_glossary", return_value={"term": "def"}),
        patch("codewiki.src.be.generation.glossary.build_link_map", return_value={"doc": "link"}),
        patch.object(
            gen.agent_orchestrator,
            "set_generation_context",
            side_effect=RuntimeError("context boom"),
        ),
    ):
        asyncio.run(gen._cluster_modules(ctx))

    cluster.assert_called_once()
    freeze_in_clustering.assert_not_called()
    assert ctx.module_tree == reclustered
    assert any("Generation context setup failed" in warning for warning in ctx.result.warnings)


def test_initialize_cache_from_tree_uses_frozen_doc_filename(tmp_path):
    gen = _make_generator(tmp_path)
    gen._build_initial_context()
    module_tree = {"Root": {"_doc_filename": "root.md", "children": {}}}

    with (
        patch("codewiki.src.be.documentation_generator.cleanup_legacy_internal_files"),
        patch("codewiki.src.be.documentation_generator.dedup_docs_directory"),
    ):
        asyncio.run(gen._initialize_cache_from_tree(module_tree, str(tmp_path / "docs")))

    assert gen.cache_manager.get_entry("module:root") is not None
    assert gen.cache_manager.get_entry("overview:root") is not None
    assert gen.cache_manager.get_output_file("module:root") == "root.md"


def test_initialize_cache_handles_internal_parents_as_module_artifacts(tmp_path):
    gen = _make_generator(tmp_path)
    gen._build_initial_context()
    module_tree = {
        "Root": {
            "path": "root",
            "_doc_filename": "root.md",
            "children": {
                "Child": {
                    "path": "",
                    "_doc_filename": "root-child.md",
                    "children": {},
                    "components": [],
                },
            },
            "components": [],
        }
    }

    with (
        patch("codewiki.src.be.documentation_generator.cleanup_legacy_internal_files"),
        patch("codewiki.src.be.documentation_generator.dedup_docs_directory"),
    ):
        asyncio.run(gen._initialize_cache_from_tree(module_tree, str(tmp_path / "docs")))

    assert gen.cache_manager.get_output_file("module:root") == "root.md"
    assert gen.cache_manager.get_output_file("module:root-child") == "root-child.md"


def test_initialize_cache_invalidates_same_artifact_when_output_file_changes(tmp_path):
    gen = _make_generator(tmp_path)
    gen._build_initial_context()
    gen.cache_manager.plan_task("module:modules", output_file="old_modules.md")
    gen.cache_manager.mark_done(
        "module:modules",
        input_hash="abc",
        output_path=str(tmp_path / "docs" / "old_modules.md"),
        output_file="old_modules.md",
        model="test/main",
    )

    module_tree = {
        "Modules": {
            "_doc_filename": "modules.md",
            "path": "modules",
            "children": {},
            "components": [],
        }
    }

    with (
        patch("codewiki.src.be.documentation_generator.cleanup_legacy_internal_files"),
        patch("codewiki.src.be.documentation_generator.dedup_docs_directory"),
    ):
        asyncio.run(gen._initialize_cache_from_tree(module_tree, str(tmp_path / "docs")))

    entry = gen.cache_manager.get_entry("module:modules")
    assert entry is not None
    assert entry.status == "missing"
    assert entry.output_file == "modules.md"


def test_initial_context_exposes_middleware_for_stages(tmp_path):
    gen = _make_generator(tmp_path)
    ctx = gen._build_initial_context()

    assert ctx.generator is gen
    assert getattr(ctx.generator, "middleware", None) is not None
