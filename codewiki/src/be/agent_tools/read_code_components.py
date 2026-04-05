import logging

from pydantic_ai import RunContext, Tool

from codewiki.src.be.agent_tools.deps import CodeWikiDeps

logger = logging.getLogger(__name__)

# Per-call token budget for tool return value (leaves room for
# system prompt + prior messages in the context window).
_MAX_RETURN_TOKENS = 100_000


def _truncate_source(code: str, max_lines: int = 200) -> str:
    """Truncate source code keeping head 60% + tail 40%."""
    lines = code.split("\n")
    if len(lines) <= max_lines:
        return code
    head_n = int(max_lines * 0.6)
    tail_n = max_lines - head_n
    omitted = len(lines) - head_n - tail_n
    return (
        "\n".join(lines[:head_n])
        + f"\n// ... ({omitted} lines truncated) ...\n"
        + "\n".join(lines[-tail_n:])
    )


async def read_code_components(ctx: RunContext[CodeWikiDeps], component_ids: list[str]) -> str:
    """Read the code of a given component id

    Args:
        component_ids: The ids of the components to read, e.g. ["sweagent.types.AgentRunResult", "sweagent.types.AgentRunResult"] where sweagent.types part is the path to the component and AgentRunResult is the name of the component
    """
    from codewiki.src.be.utils import count_tokens

    budget = getattr(ctx.deps.config, "max_input_tokens", _MAX_RETURN_TOKENS)
    # Tool return should use at most 1/3 of total budget (leaves room for
    # existing context + system prompt + response)
    tool_budget = min(budget // 3, _MAX_RETURN_TOKENS)

    results = []
    total_tokens = 0

    for component_id in component_ids:
        if component_id not in ctx.deps.components:
            results.append(f"# Component {component_id} not found")
            continue

        source = (ctx.deps.components[component_id].source_code or "").strip()
        if not source:
            results.append(f"# Component {component_id}: (no source code)")
            continue

        # Truncate individual large components
        source = _truncate_source(source)

        entry = f"# Component {component_id}:\n{source}\n"
        entry_tokens = count_tokens(entry)

        if total_tokens + entry_tokens > tool_budget:
            remaining = len(component_ids) - len(results)
            results.append(
                f"# ... ({remaining} more components omitted — tool return budget exceeded)"
            )
            logger.warning(
                "read_code_components: truncated at %d/%d components (~%dK tokens, budget %dK)",
                len(results),
                len(component_ids),
                total_tokens // 1000,
                tool_budget // 1000,
            )
            break

        results.append(entry)
        total_tokens += entry_tokens

    return "\n".join(results)


read_code_components_tool = Tool(
    function=read_code_components,
    name="read_code_components",
    description="Read the code of a given list of component ids",
    takes_ctx=True,
)
