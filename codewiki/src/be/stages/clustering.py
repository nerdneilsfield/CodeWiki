from __future__ import annotations

from codewiki.src.be.pipeline import PipelineContext


class ClusteringStage:
    name = "ClusteringStage"
    failure_policy = "fail_fast"

    async def execute(self, ctx: PipelineContext) -> None:
        await ctx.generator._cluster_modules(ctx)
