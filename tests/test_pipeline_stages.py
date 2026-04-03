import asyncio

import pytest

from codewiki.src.be.pipeline import GenerationResult, PipelineContext, PipelineRunner
from codewiki.src.be.stages import DEFAULT_STAGES


class TestPipelineRunner:
    @pytest.mark.asyncio
    async def test_all_stages_succeed_gives_complete(self):
        class OkStage:
            name = "ok"
            failure_policy = "degraded_ok"

            async def execute(self, ctx):
                return None

        runner = PipelineRunner([OkStage(), OkStage()])
        result = await runner.execute(PipelineContext(config=None))
        assert result.status == "complete"
        assert result.warnings == []

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

            async def execute(self, ctx):
                return None

        runner = PipelineRunner([FailStage(), OkStage()])
        result = await runner.execute(PipelineContext(config=None))
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
        result = await runner.execute(PipelineContext(config=None))
        assert result.status == "failed"
        assert "cluster" not in executed


def test_default_stages_put_metadata_last():
    assert DEFAULT_STAGES[-1].name == "MetadataStage"


def test_documentation_generator_run_returns_generation_result(tmp_path, monkeypatch):
    from codewiki.src.be.documentation_generator import DocumentationGenerator
    from codewiki.src.codewiki_config import CodeWikiConfig

    config = CodeWikiConfig(
        repo_path=str(tmp_path / "repo"),
        output_dir=str(tmp_path / "out"),
        dependency_graph_dir=str(tmp_path / "graphs"),
        docs_dir=str(tmp_path / "docs"),
        max_depth=2,
        llm_base_url="http://localhost",
        llm_api_key="x",
        main_model="test/main",
        cluster_model="test/cluster",
    )
    generator = DocumentationGenerator(config)

    async def _fake_execute(self, ctx):
        ctx.result.metadata = {"ok": True}
        return ctx.result

    monkeypatch.setattr("codewiki.src.be.pipeline.PipelineRunner.execute", _fake_execute)

    result = asyncio.run(generator.run())
    assert isinstance(result, GenerationResult)
    assert result.metadata == {"ok": True}
