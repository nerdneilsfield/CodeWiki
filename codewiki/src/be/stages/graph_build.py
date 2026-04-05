from __future__ import annotations

from codewiki.src.be.pipeline import PipelineContext


class GraphBuildStage:
    name = "GraphBuildStage"
    failure_policy = "fail_fast"

    async def execute(self, ctx: PipelineContext) -> None:
        # Always run: components/leaf_nodes are needed by ModuleGenerationStage
        # even when cluster cache is valid (is_complex_module requires components).
        components, leaf_nodes = ctx.graph_builder.build_dependency_graph()
        ctx.components = components
        ctx.leaf_nodes = leaf_nodes
