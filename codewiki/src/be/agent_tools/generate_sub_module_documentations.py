from __future__ import annotations

from pydantic_ai import RunContext, Tool

from codewiki.src.be.agent_tools.deps import CodeWikiDeps


async def generate_sub_module_documentation(
    ctx: RunContext[CodeWikiDeps], sub_module_specs: dict[str, list[str]]
) -> str:
    """No-op compatibility tool.

    Tree refinement now happens before documentation generation begins, so the
    runtime agent may not create or mutate sub-modules anymore.
    """
    _ = (ctx, sub_module_specs)
    return (
        "Sub-module generation is disabled during documentation writing. "
        "The module tree is frozen before generation starts; describe and "
        "cross-reference existing child modules instead of creating new ones."
    )


generate_sub_module_documentation_tool = Tool(
    function=generate_sub_module_documentation,
    name="generate_sub_module_documentation",
    description="Frozen-tree compatibility no-op; runtime sub-module creation is disabled.",
    takes_ctx=True,
)
