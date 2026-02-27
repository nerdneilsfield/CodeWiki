import os
from pydantic_ai import RunContext, Tool, Agent
from pydantic_ai.usage import UsageLimits

from codewiki.src.be.agent_tools.deps import CodeWikiDeps
from codewiki.src.be.agent_tools.read_code_components import read_code_components_tool
from codewiki.src.be.agent_tools.str_replace_editor import str_replace_editor_tool
from codewiki.src.be.llm_services import create_fallback_models
from codewiki.src.be.prompt_template import format_system_prompt, format_leaf_system_prompt, format_user_prompt
from codewiki.src.be.utils import is_complex_module, count_tokens, agent_progress_handler
from codewiki.src.be.cluster_modules import format_potential_core_components
from codewiki.src.config import MODULE_TREE_FILENAME
from codewiki.src.utils import file_manager

import logging
logger = logging.getLogger(__name__)



async def generate_sub_module_documentation(
    ctx: RunContext[CodeWikiDeps],
    sub_module_specs: dict[str, list[str]]
) -> str:
    """Delegate documentation generation for sub-modules to sub-agents.

    Each key in *sub_module_specs* is a human-readable sub-module name
    (e.g. ``"authentication_layer"``).  Its value is a **list of component
    IDs** taken verbatim from the core_components list you were given.

    Args:
        sub_module_specs: A mapping of sub-module names to their component IDs.
            Keys   – descriptive sub-module names (snake_case, NOT metadata
                     keys like ``module_name`` / ``language``).
            Values – lists of **exact component IDs** from the core_components
                     list (NOT module names, language codes, or descriptions).
            Example: {"auth_layer": ["src/auth.py::AuthManager", "src/auth.py::Token"],
                      "data_store": ["src/db.py::Database"]}
    """

    deps = ctx.deps
    previous_module_name = deps.current_module_name

    # ── Validate & filter out obviously wrong entries ────────────────────
    _META_KEYS = {
        'module_name', 'sub_modules', 'language', 'output_language',
        'name', 'description', 'specs', 'components', 'children',
    }
    filtered: dict[str, list[str]] = {}
    for sub_name, comp_ids in sub_module_specs.items():
        if sub_name.lower() in _META_KEYS:
            logger.warning(f"Skipping invalid sub-module name '{sub_name}' (looks like a metadata key, not a module name)")
            continue
        if sub_name == deps.current_module_name:
            logger.warning(f"Skipping sub-module '{sub_name}' (same as parent module name)")
            continue
        if not isinstance(comp_ids, list) or not comp_ids:
            logger.warning(f"Skipping sub-module '{sub_name}' — component list is empty or invalid")
            continue
        filtered[sub_name] = comp_ids

    if not filtered:
        return (
            "ERROR: All sub-module entries were invalid. Please call this tool again "
            "with correct sub_module_specs: keys must be descriptive sub-module names "
            "(NOT 'module_name', 'language', etc.) and values must be lists of "
            "component IDs from the core_components list."
        )
    sub_module_specs = filtered

    # Create fallback models from config
    fallback_models = create_fallback_models(deps.config)

    # add the sub-module to the module tree (preserve existing entries)
    value = deps.module_tree
    for key in deps.path_to_current_module:
        value = value[key]["children"]
    for sub_module_name, core_component_ids in sub_module_specs.items():
        if sub_module_name not in value:
            value[sub_module_name] = {"components": core_component_ids, "children": {}}
        else:
            # Only refresh components; keep existing _completed / children
            value[sub_module_name]["components"] = core_component_ids

    # Persist the updated tree immediately so the sidebar stays accurate even if
    # the agent fails later (after sub-module .md files have already been created).
    if deps.module_tree_manager:
        new_children = {
            name: {"components": ids, "children": {}}
            for name, ids in sub_module_specs.items()
        }
        await deps.module_tree_manager.update_children(
            deps.path_to_current_module, new_children
        )
    else:
        module_tree_path = os.path.join(deps.absolute_docs_path, MODULE_TREE_FILENAME)
        file_manager.save_json(deps.module_tree, module_tree_path)
    
    for sub_module_name, core_component_ids in sub_module_specs.items():

        # Create visual indentation for nested modules
        indent = "  " * deps.current_depth
        arrow = "└─" if deps.current_depth > 0 else "→"

        # ── Skip sub-modules already dispatched in this agent run ─────
        if sub_module_name in deps._dispatched_sub_modules:
            logger.info(f"{indent}{arrow} ✓ Sub-module {sub_module_name} already dispatched in this run, skipping")
            continue
        deps._dispatched_sub_modules.add(sub_module_name)

        # ── Skip sub-modules whose docs already exist ─────────────────
        docs_path = os.path.join(deps.absolute_docs_path, f"{sub_module_name}.md")
        if os.path.exists(docs_path) and os.path.getsize(docs_path) > 100:
            logger.info(f"{indent}{arrow} ✓ Sub-module {sub_module_name} already has docs, skipping")
            continue

        logger.info(f"{indent}{arrow} Generating documentation for sub-module: {sub_module_name}")

        num_tokens = count_tokens(format_potential_core_components(core_component_ids, ctx.deps.components)[-1])

        if is_complex_module(ctx.deps.components, core_component_ids) and ctx.deps.current_depth < ctx.deps.max_depth and num_tokens >= ctx.deps.config.max_token_per_leaf_module:
            sub_agent = Agent(
                model=fallback_models,
                name=sub_module_name,
                deps_type=CodeWikiDeps,
                system_prompt=format_system_prompt(sub_module_name, ctx.deps.custom_instructions, ctx.deps.config.output_language),
                tools=[read_code_components_tool, str_replace_editor_tool, generate_sub_module_documentation_tool],
            )
        else:
            sub_agent = Agent(
                model=fallback_models,
                name=sub_module_name,
                deps_type=CodeWikiDeps,
                system_prompt=format_leaf_system_prompt(sub_module_name, ctx.deps.custom_instructions, ctx.deps.config.output_language),
                tools=[read_code_components_tool, str_replace_editor_tool],
            )

        deps.current_module_name = sub_module_name
        deps.path_to_current_module.append(sub_module_name)
        deps.current_depth += 1

        result = await sub_agent.run(
            format_user_prompt(
                module_name=deps.current_module_name,
                core_component_ids=core_component_ids,
                components=ctx.deps.components,
                module_tree=ctx.deps.module_tree,
            ),
            deps=ctx.deps,
            usage_limits=UsageLimits(request_limit=None),
            event_stream_handler=agent_progress_handler,
        )

        # Mark this sub-module as completed so re-runs can skip it
        if deps.module_tree_manager:
            await deps.module_tree_manager.mark_completed(list(deps.path_to_current_module))

        # remove the sub-module name from the path to current module and the module tree
        deps.path_to_current_module.pop()
        deps.current_depth -= 1

    # restore the previous module name
    deps.current_module_name = previous_module_name

    return f"Generate successfully. Documentations: {', '.join([key + '.md' for key in sub_module_specs.keys()])} are saved in the working directory."


generate_sub_module_documentation_tool = Tool(function=generate_sub_module_documentation, name="generate_sub_module_documentation", description="Generate detailed description of a given sub-module specs to the sub-agents", takes_ctx=True, max_retries=3)