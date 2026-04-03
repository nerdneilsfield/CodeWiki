from __future__ import annotations

from codewiki.src.be.pipeline import PipelineContext


class MetadataStage:
    name = "MetadataStage"
    failure_policy = "degraded_ok"

    async def execute(self, ctx: PipelineContext) -> None:
        ctx.result.metadata = ctx.generator._write_metadata(ctx)
