import asyncio
import os
import time
from typing import Any
from pydantic_ai import RunContext, Tool, Agent
from pydantic_ai.messages import ModelResponse
from pydantic_ai.usage import UsageLimits
import openai

from codewiki.src.be.agent_tools.deps import CodeWikiDeps
from codewiki.src.be.agent_tools.read_code_components import read_code_components_tool
from codewiki.src.be.agent_tools.str_replace_editor import str_replace_editor_tool
from codewiki.src.be.llm_usage import record_agent_run_usage
from codewiki.src.be.llm_services import select_agent_model
from codewiki.src.be.prompt_template import (
    format_system_prompt,
    format_leaf_system_prompt,
    format_user_prompt,
)
from codewiki.src.be.utils import is_complex_module, count_tokens, agent_progress_handler
from codewiki.src.be.cluster_modules import format_potential_core_components
from codewiki.src.config import MODULE_TREE_FILENAME
from codewiki.src.utils import (
    content_hash,
    doc_id_for_path,
    file_manager,
    module_doc_filename,
    find_module_doc,
)
from codewiki.src.be.generation_state import DocTask

import logging

logger = logging.getLogger(__name__)


async def generate_sub_module_documentation(
    ctx: RunContext[CodeWikiDeps], sub_module_specs: dict[str, list[str]]
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

    # ── Reuse existing children if the tree already has them ─────────────
    # This ensures the tree structure is stable across re-runs: the first
    # successful run establishes the sub-module split and subsequent runs
    # reuse it rather than letting the LLM propose a different split.
    existing_children: dict[str, Any] = {}
    try:
        node = deps.module_tree
        for key in deps.path_to_current_module:
            node = node[key]["children"]
        existing_children = node  # children dict of the current module's parent level
    except (KeyError, TypeError):
        pass

    # Check if the current module already has children in the tree
    current_node_children: dict[str, Any] = {}
    try:
        current_node = deps.module_tree
        for key in deps.path_to_current_module[:-1]:
            current_node = current_node[key]["children"]
        current_node_children = current_node[deps.path_to_current_module[-1]].get("children", {})
    except (KeyError, TypeError, IndexError):
        pass

    if current_node_children:
        # Tree already has children for this module — use them instead of the
        # agent's proposal.  This keeps the tree stable across re-runs.
        logger.info(
            f"Using cached sub-module tree for '{deps.current_module_name}' "
            f"({len(current_node_children)} children) — ignoring agent proposal"
        )
        sub_module_specs = {
            name: info.get("components", []) for name, info in current_node_children.items()
        }
    else:
        # ── Validate & filter out obviously wrong entries ────────────────
        _META_KEYS = {
            "module_name",
            "sub_modules",
            "language",
            "output_language",
            "name",
            "description",
            "specs",
            "components",
            "children",
        }
        filtered: dict[str, list[str]] = {}
        for sub_name, comp_ids in sub_module_specs.items():
            if sub_name.lower() in _META_KEYS:
                logger.warning(
                    f"Skipping invalid sub-module name '{sub_name}' (looks like a metadata key, not a module name)"
                )
                continue
            if sub_name == deps.current_module_name:
                logger.warning(f"Skipping sub-module '{sub_name}' (same as parent module name)")
                continue
            if not isinstance(comp_ids, list) or not comp_ids:
                logger.warning(
                    f"Skipping sub-module '{sub_name}' — component list is empty or invalid"
                )
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
                name: {"components": ids, "children": {}} for name, ids in sub_module_specs.items()
            }
            await deps.module_tree_manager.update_children(
                deps.path_to_current_module, new_children
            )
        else:
            module_tree_path = os.path.join(deps.absolute_docs_path, MODULE_TREE_FILENAME)
            file_manager.save_json(deps.module_tree, module_tree_path)

        if deps.state_mgr and deps.gen_state:
            discovered_parent_id = doc_id_for_path(deps.module_tree, deps.path_to_current_module)
            for sub_module_name in sub_module_specs:
                sub_path = deps.path_to_current_module + [sub_module_name]
                sub_doc_id = doc_id_for_path(deps.module_tree, sub_path)
                if deps.gen_state.get_task(sub_doc_id):
                    continue
                try:
                    nav = deps.module_tree
                    for key in deps.path_to_current_module:
                        nav = nav[key]["children"]
                    sub_node = nav.get(sub_module_name, {})
                    output_file = sub_node.get(
                        "_doc_filename",
                        module_doc_filename(sub_path),
                    )
                except (KeyError, TypeError):
                    output_file = module_doc_filename(sub_path)
                await deps.state_mgr.register_discovered_task(
                    DocTask(
                        doc_id=sub_doc_id,
                        kind="module",
                        module_path=sub_path,
                        output_file=output_file,
                        source="discovered",
                        parent_doc_id=discovered_parent_id,
                        language=deps.config.output_language,
                        prompt_version="v7",
                    )
                )
                await deps.state_mgr.flush()

    for sub_module_name, core_component_ids in sub_module_specs.items():
        # Create visual indentation for nested modules
        indent = "  " * deps.current_depth
        arrow = "└─" if deps.current_depth > 0 else "→"

        # ── Skip sub-modules already dispatched in this agent run ─────
        if sub_module_name in deps._dispatched_sub_modules:
            logger.debug(
                f"{indent}{arrow} ✓ Sub-module {sub_module_name} already dispatched in this run, skipping"
            )
            continue
        deps._dispatched_sub_modules.add(sub_module_name)

        # ── Skip sub-modules whose docs already exist ─────────────────
        sub_module_path = deps.path_to_current_module + [sub_module_name]
        sub_doc_id = doc_id_for_path(deps.module_tree, sub_module_path)
        docs_path = None
        if deps.gen_state:
            task = deps.gen_state.get_task(sub_doc_id)
            if task and task.status == "completed":
                candidate = os.path.join(deps.absolute_docs_path, task.output_file)
                if os.path.exists(candidate) and os.path.getsize(candidate) > 100:
                    docs_path = candidate
        if docs_path is None and not deps.gen_state:
            docs_path = find_module_doc(deps.absolute_docs_path, sub_module_path)
        if docs_path and os.path.getsize(docs_path) > 100:
            logger.debug(
                f"{indent}{arrow} ✓ Sub-module {sub_module_name} already has docs, skipping"
            )
            continue

        logger.info(f"{indent}{arrow} Generating documentation for sub-module: {sub_module_name}")

        # Look up the assigned filename before mutating the current path.
        try:
            nav = deps.module_tree
            for key in deps.path_to_current_module:
                nav = nav[key]["children"]
            sub_node = nav.get(sub_module_name, {})
            assigned_filename = sub_node.get(
                "_doc_filename",
                module_doc_filename(deps.path_to_current_module + [sub_module_name]),
            )
        except (KeyError, TypeError):
            assigned_filename = module_doc_filename(deps.path_to_current_module + [sub_module_name])

        num_tokens = count_tokens(
            format_potential_core_components(core_component_ids, ctx.deps.components)[-1]
        )
        if ctx.deps.long_context_model and num_tokens > ctx.deps.config.long_context_threshold:
            model = ctx.deps.long_context_model
        else:
            model = ctx.deps.fallback_models or select_agent_model(ctx.deps.config, num_tokens)

        custom_instructions = ctx.deps.custom_instructions or ""

        if (
            is_complex_module(ctx.deps.components, core_component_ids)
            and ctx.deps.current_depth < ctx.deps.max_depth
            and num_tokens >= ctx.deps.config.max_token_per_leaf_module
        ):
            sub_agent = Agent(
                model=model,
                name=sub_module_name,
                deps_type=CodeWikiDeps,
                system_prompt=format_system_prompt(
                    sub_module_name, custom_instructions, ctx.deps.config.output_language
                ),
                tools=[
                    read_code_components_tool,
                    str_replace_editor_tool,
                    generate_sub_module_documentation_tool,
                ],
            )
        else:
            sub_agent = Agent(
                model=model,
                name=sub_module_name,
                deps_type=CodeWikiDeps,
                system_prompt=format_leaf_system_prompt(
                    sub_module_name, custom_instructions, ctx.deps.config.output_language
                ),
                tools=[read_code_components_tool, str_replace_editor_tool],
            )

        deps.current_module_name = sub_module_name
        deps.path_to_current_module.append(sub_module_name)
        deps.current_depth += 1
        deps.assigned_doc_filename = assigned_filename

        _sub_retry_delays = [5, 15]
        _sub_last_exc = None
        _sub_models_str = "unknown"
        for _sub_attempt in range(len(_sub_retry_delays) + 1):
            if _sub_attempt > 0:
                _delay = _sub_retry_delays[_sub_attempt - 1]
                logger.warning(
                    f"{indent}{arrow} Retrying sub-module '{sub_module_name}' "
                    f"in {_delay}s (attempt {_sub_attempt}/{len(_sub_retry_delays)}) "
                    f"after: {_sub_last_exc}"
                )
                await asyncio.sleep(_delay)
            try:
                _sub_t0 = time.time()
                _sub_result = await sub_agent.run(
                    format_user_prompt(
                        module_name=deps.current_module_name,
                        core_component_ids=core_component_ids,
                        components=ctx.deps.components,
                        module_tree=ctx.deps.module_tree,
                    )
                    + f"\n\nWrite your documentation to the file: {assigned_filename}",
                    deps=ctx.deps,
                    usage_limits=UsageLimits(request_limit=None),
                    event_stream_handler=agent_progress_handler,
                )
                _sub_elapsed = time.time() - _sub_t0
                # Log which model(s) responded
                _sub_models = []
                for _msg in _sub_result.all_messages():
                    if isinstance(_msg, ModelResponse) and _msg.model_name:
                        if _msg.model_name not in _sub_models:
                            _sub_models.append(_msg.model_name)
                _sub_usage = _sub_result.usage()
                if ctx.deps.usage_stats is not None and _sub_usage:
                    record_agent_run_usage(
                        ctx.deps.usage_stats,
                        _sub_models,
                        _sub_usage.input_tokens or 0,
                        _sub_usage.output_tokens or 0,
                        _sub_usage.requests or 0,
                    )
                _sub_models_str = ", ".join(_sub_models) if _sub_models else "unknown"
                if len(_sub_models) > 1:
                    logger.info(
                        f"{indent}{arrow} Fallback triggered for sub-module '{sub_module_name}': "
                        f"models used: {_sub_models_str} ({_sub_elapsed:.1f}s)"
                    )
                logger.debug(
                    f"{indent}{arrow} Sub-module '{sub_module_name}' completed "
                    f"in {_sub_elapsed:.1f}s (model: {_sub_models_str})"
                )
                _sub_last_exc = None
                break
            except Exception as _exc:
                _sub_last_exc = _exc

        if _sub_last_exc is not None:
            logger.error(
                f"{indent}{arrow} Sub-module '{sub_module_name}' failed after all retries: "
                f"{_sub_last_exc} — skipping (fill pass will retry)"
            )
            deps.path_to_current_module.pop()
            deps.current_depth -= 1
            continue

        # Mark this sub-module as completed so re-runs can skip it
        if deps.state_mgr and deps.gen_state:
            await deps.state_mgr.mark_completed(
                sub_doc_id,
                content_hash=content_hash(os.path.join(deps.absolute_docs_path, assigned_filename)),
                model=_sub_models_str,
            )

        # remove the sub-module name from the path to current module and the module tree
        deps.path_to_current_module.pop()
        deps.current_depth -= 1
        deps.assigned_doc_filename = ""

    # restore the previous module name
    deps.current_module_name = previous_module_name

    doc_files = [
        module_doc_filename(deps.path_to_current_module + [name])
        for name in sub_module_specs.keys()
    ]
    return f"Generate successfully. Documentations: {', '.join(doc_files)} are saved in the working directory."


generate_sub_module_documentation_tool = Tool(
    function=generate_sub_module_documentation,
    name="generate_sub_module_documentation",
    description="Generate detailed description of a given sub-module specs to the sub-agents",
    takes_ctx=True,
    max_retries=3,
)
