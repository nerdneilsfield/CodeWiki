from __future__ import annotations

from codewiki.src.be.pipeline import PipelineContext


class GuideStage:
    name = "GuideStage"
    failure_policy = "degraded_ok"

    async def execute(self, ctx: PipelineContext) -> None:
        await ctx.generator._generate_guides(ctx)
