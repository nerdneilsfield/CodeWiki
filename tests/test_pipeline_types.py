from codewiki.src.be.pipeline import (
    GenerationResult,
    ModuleFailure,
    ModuleSummary,
    PipelineContext,
)


class TestModuleSummary:
    def test_empty_summary(self):
        summary = ModuleSummary(
            completed=[],
            failed=[],
            skipped=[],
            retried_then_succeeded=[],
            total=0,
        )
        assert summary.total == 0

    def test_with_failures(self):
        summary = ModuleSummary(
            completed=["module:a"],
            failed=[ModuleFailure(doc_id="module:b", error="timeout", retried=True)],
            skipped=[],
            retried_then_succeeded=[],
            total=2,
        )
        assert len(summary.failed) == 1
        assert summary.failed[0].retried is True


class TestGenerationResult:
    def test_complete_result(self):
        result = GenerationResult(
            status="complete",
            warnings=[],
            module_summary=ModuleSummary(
                completed=["a"],
                failed=[],
                skipped=[],
                retried_then_succeeded=[],
                total=1,
            ),
            metadata={},
        )
        assert result.status == "complete"

    def test_degraded_result(self):
        result = GenerationResult(
            status="degraded",
            warnings=["IndexBuildStage failed: timeout"],
            module_summary=ModuleSummary(
                completed=["a"],
                failed=[ModuleFailure("b", "err", False)],
                skipped=[],
                retried_then_succeeded=[],
                total=2,
            ),
            metadata={},
        )
        assert result.status == "degraded"
        assert len(result.warnings) == 1

    def test_to_metadata_dict(self):
        result = GenerationResult(
            status="complete",
            warnings=[],
            module_summary=ModuleSummary(
                completed=["a"],
                failed=[],
                skipped=[],
                retried_then_succeeded=[],
                total=1,
            ),
            metadata={"existing": "data"},
        )
        metadata = result.to_metadata_dict()
        assert metadata["generation_status"] == "complete"
        assert "module_summary" in metadata


class TestPipelineContext:
    def test_context_creation(self):
        ctx = PipelineContext(config=None, working_dir="/tmp")
        assert ctx.working_dir == "/tmp"
        assert ctx.result.status == "complete"
