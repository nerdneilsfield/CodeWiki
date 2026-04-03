from __future__ import annotations

from codewiki.src.be.pipeline import PipelineContext


class GraphBuildStage:
    name = "GraphBuildStage"
    failure_policy = "fail_fast"

    async def execute(self, ctx: PipelineContext) -> None:
        components, leaf_nodes = ctx.graph_builder.build_dependency_graph()
        ctx.components = components
        ctx.leaf_nodes = leaf_nodes
