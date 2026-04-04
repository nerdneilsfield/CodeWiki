from __future__ import annotations

import structlog

from codewiki.src.be.pipeline import PipelineContext

logger = structlog.get_logger(__name__)


class GraphBuildStage:
    name = "GraphBuildStage"
    failure_policy = "fail_fast"

    async def execute(self, ctx: PipelineContext) -> None:
        if ctx.cluster_cache_hit:
            logger.info("Skipping graph build (cluster cache hit, same commit)")
            return
        components, leaf_nodes = ctx.graph_builder.build_dependency_graph()
        ctx.components = components
        ctx.leaf_nodes = leaf_nodes
