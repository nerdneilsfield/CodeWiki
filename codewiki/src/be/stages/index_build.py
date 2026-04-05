from __future__ import annotations

import structlog

from codewiki.src.be.pipeline import PipelineContext

logger = structlog.get_logger(__name__)


class IndexBuildStage:
    name = "IndexBuildStage"
    failure_policy = "degraded_ok"

    async def execute(self, ctx: PipelineContext) -> None:
        # IndexBuilder has its own commit-based cache; don't skip here.
        await ctx.generator._build_index(ctx)
