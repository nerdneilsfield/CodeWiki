from __future__ import annotations

from codewiki.src.be.pipeline import PipelineContext


class PostprocessStage:
    name = "PostprocessStage"
    failure_policy = "degraded_ok"

    async def execute(self, ctx: PipelineContext) -> None:
        ctx.generator._postprocess_docs(ctx)
