import time
from typing import Any, Dict, List, cast

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse
from pydantic_ai.usage import UsageLimits

# ── pydantic_ai compatibility patch ─────────────────────────────────────────
# Some OpenAI-compatible providers (e.g. GLM API) send streaming chunks where
# usage is present but individual token counts are None.  pydantic_ai 1.0.x
# assumes int values and crashes with "int += NoneType" inside
# _incr_usage_tokens.  Patch it to treat None as 0.
try:
    import pydantic_ai.usage as _pai_usage

    def _safe_incr_usage_tokens(slf, incr_usage):  # type: ignore[no-untyped-def]
        slf.input_tokens += incr_usage.input_tokens or 0
        slf.cache_write_tokens += incr_usage.cache_write_tokens or 0
        slf.cache_read_tokens += incr_usage.cache_read_tokens or 0
        slf.input_audio_tokens += incr_usage.input_audio_tokens or 0
        slf.cache_audio_read_tokens += incr_usage.cache_audio_read_tokens or 0
        slf.output_tokens += incr_usage.output_tokens or 0
        for key, value in incr_usage.details.items():
            slf.details[key] = slf.details.get(key, 0) + value

    cast(Any, _pai_usage)._incr_usage_tokens = _safe_incr_usage_tokens
except Exception:
    pass  # silently skip if pydantic_ai changes its internals
# ─────────────────────────────────────────────────────────────────────────────
# import logfire
import logging
import os
import traceback

# Configure logging and monitoring

logger = logging.getLogger(__name__)


# try:
#     # Configure logfire with environment variables for Docker compatibility
#     logfire_token = os.getenv('LOGFIRE_TOKEN')
#     logfire_project = os.getenv('LOGFIRE_PROJECT_NAME', 'default')
#     logfire_service = os.getenv('LOGFIRE_SERVICE_NAME', 'default')

#     if logfire_token:
#         # Configure with explicit token (for Docker)
#         logfire.configure(
#             token=logfire_token,
#             project_name=logfire_project,
#             service_name=logfire_service,
#         )
#     else:
#         # Use default configuration (for local development with logfire auth)
#         logfire.configure(
#             project_name=logfire_project,
#             service_name=logfire_service,
#         )

#     logfire.instrument_pydantic_ai()
#     logger.debug(f"Logfire configured successfully for project: {logfire_project}")

# except Exception as e:
#     logger.warning(f"Failed to configure logfire: {e}")

# Local imports
from codewiki.src.be.agent_tools.deps import CodeWikiDeps
from codewiki.src.be.agent_tools.read_code_components import read_code_components_tool
from codewiki.src.be.agent_tools.str_replace_editor import str_replace_editor_tool
from codewiki.src.be.agent_tools.generate_sub_module_documentations import (
    generate_sub_module_documentation_tool,
)
from codewiki.src.be.llm_services import create_fallback_models, create_long_context_model
from codewiki.src.be.prompt_template import (
    format_user_prompt,
    format_system_prompt,
    format_leaf_system_prompt,
    format_overview_prompt,
)
from codewiki.src.be.generation.context_pack import build_context_pack, format_context_pack_section
from codewiki.src.be.llm_usage import LLMUsageStats, record_agent_run_usage
from codewiki.src.be.utils import is_complex_module, count_tokens, agent_progress_handler
from codewiki.src.codewiki_config import CodeWikiConfig
from codewiki.src.config import (
    MODULE_TREE_FILENAME,
)
from codewiki.src.utils import (
    content_hash,
    doc_id_for_path,
    file_manager,
    module_doc_filename,
    find_module_doc,
)
from codewiki.src.be.dependency_analyzer.models.core import Node


class AgentOrchestrator:
    """Orchestrates the AI agents for documentation generation."""

    def __init__(self, config: CodeWikiConfig, usage_stats: LLMUsageStats | None = None):
        self.config = config
        self.usage_stats = usage_stats
        self.fallback_models = create_fallback_models(config)
        self.long_context_model = (
            create_long_context_model(config) if config.long_context_model else None
        )
        self.custom_instructions = config.get_prompt_addition() if config else None
        self.output_language = config.output_language if config else "en"
        # v2: late-injected after index build + clustering
        self.index_products = None
        self.global_assets = None

    def set_generation_context(self, index_products, global_assets):
        """Late injection of index products and global assets.

        Called after index build + clustering completes, before doc generation starts.
        """
        self.index_products = index_products
        self.global_assets = global_assets

    @staticmethod
    def _assigned_doc_filename(module_tree: dict, module_path: list[str]) -> str:
        """Return the frozen filename for a module path when available."""
        if not module_path:
            return module_doc_filename([])
        try:
            node = module_tree
            for idx, part in enumerate(module_path):
                if idx == 0:
                    node = node[part]
                else:
                    node = node["children"][part]
            return node.get("_doc_filename", module_doc_filename(module_path))
        except (KeyError, TypeError):
            return module_doc_filename(module_path)

    def create_agent(
        self,
        module_name: str,
        components: Dict[str, Any],
        core_component_ids: List[str],
        estimated_tokens: int = 0,
    ) -> Agent[CodeWikiDeps, str]:
        """Create an appropriate agent based on module complexity."""
        if self.long_context_model and estimated_tokens > self.config.long_context_threshold:
            model = self.long_context_model
        else:
            model = self.fallback_models
        custom_instructions = self.custom_instructions or ""

        if is_complex_module(components, core_component_ids):
            return Agent(
                model,
                name=module_name,
                deps_type=CodeWikiDeps,
                tools=[
                    read_code_components_tool,
                    str_replace_editor_tool,
                    generate_sub_module_documentation_tool,
                ],
                system_prompt=format_system_prompt(
                    module_name, custom_instructions, self.output_language
                ),
            )
        else:
            return Agent(
                model,
                name=module_name,
                deps_type=CodeWikiDeps,
                tools=[read_code_components_tool, str_replace_editor_tool],
                system_prompt=format_leaf_system_prompt(
                    module_name, custom_instructions, self.output_language
                ),
            )

    async def process_module(
        self,
        module_name: str,
        components: Dict[str, Node],
        core_component_ids: List[str],
        module_path: List[str],
        working_dir: str,
        tree_manager=None,
        gen_state=None,
        state_mgr=None,
    ) -> tuple[Dict[str, Any], str]:
        """Process a single module and generate its documentation.

        Args:
            tree_manager: Optional ModuleTreeManager for lock-protected
                tree access during concurrent processing.

        Returns:
            A tuple of (module_tree, models_used) where *models_used* is a
            comma-separated string of model names that actually responded.
        """
        logger.info(f"Processing module: {module_name}")
        module_tree_for_state: dict[str, Any] | None = None

        # ── Cache check ──────────────────────────────────────────────────
        doc_path_parts = module_path if module_path else [module_name]
        docs_path = None if gen_state else find_module_doc(working_dir, doc_path_parts)
        task = None
        if gen_state:
            module_tree_for_state = await tree_manager.get_snapshot() if tree_manager else None
        if gen_state and module_tree_for_state:
            doc_id = doc_id_for_path(module_tree_for_state, doc_path_parts)
            task = gen_state.get_task(doc_id)
            if task and task.status == "completed":
                candidate = os.path.join(working_dir, task.output_file)
                if os.path.exists(candidate) and os.path.getsize(candidate) > 100:
                    docs_path = candidate

        if docs_path and os.path.getsize(docs_path) > 100:
            if task and task.status == "completed":
                logger.debug(f"✓ Module docs already exists at {docs_path}")
                return {}, "cached"
            if is_complex_module(components, core_component_ids) and module_path:
                children = {}
                if tree_manager:
                    snapshot = await tree_manager.get_snapshot()
                    try:
                        node = snapshot
                        for key in module_path[:-1]:
                            node = node[key]["children"]
                        children = node.get(module_path[-1], {}).get("children", {})
                    except (KeyError, TypeError):
                        pass

                if not children:
                    logger.debug(
                        f"✓ Module {module_name} has docs and no children — treating as complete"
                    )
                    if state_mgr and module_tree_for_state:
                        await state_mgr.mark_completed(
                            doc_id_for_path(module_tree_for_state, doc_path_parts),
                            content_hash=content_hash(docs_path),
                            model=self.config.main_model,
                        )
                    return {}, "cached"

                child_done = False
                if gen_state and module_tree_for_state:
                    child_done = all(
                        (
                            child_task := gen_state.get_task(
                                doc_id_for_path(module_tree_for_state, module_path + [cn])
                            )
                        )
                        and child_task.status == "completed"
                        for cn in children
                    )
                if not child_done:
                    if not gen_state:
                        child_done = all(
                            (lambda p: p is not None and os.path.getsize(p) > 100)(
                                find_module_doc(working_dir, module_path + [cn])
                            )
                            for cn in children
                        )

                if child_done:
                    logger.debug(
                        f"✓ Module {module_name} and all children have docs — treating as complete"
                    )
                    if state_mgr and module_tree_for_state:
                        await state_mgr.mark_completed(
                            doc_id_for_path(module_tree_for_state, doc_path_parts),
                            content_hash=content_hash(docs_path),
                            model=self.config.main_model,
                        )
                    return {}, "cached"

                logger.debug(
                    f"↩ Module {module_name} exists but has children without docs — re-processing"
                )
            else:
                # Leaf / simple module — .md existence is sufficient
                logger.debug(f"✓ Module docs already exists at {docs_path}")
                return {}, "cached"

        # ── Get module tree snapshot ─────────────────────────────────────
        if tree_manager:
            module_tree = await tree_manager.get_snapshot()
        else:
            module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)
            module_tree = file_manager.load_json(module_tree_path) or {}

        # Estimate prompt tokens to pre-select long-context model if needed.
        # The model receives system_prompt + tool_definitions + user_prompt.
        # Compute overhead from actual system prompt + estimated tool schemas.
        custom_instructions = self.custom_instructions or ""
        if is_complex_module(components, core_component_ids):
            _sys_prompt = format_system_prompt(
                module_name, custom_instructions, self.output_language
            )
        else:
            _sys_prompt = format_leaf_system_prompt(
                module_name, custom_instructions, self.output_language
            )
        _TOOL_SCHEMA_ESTIMATE = 3_000  # pydantic-ai tool JSON schemas
        _SYSTEM_PROMPT_OVERHEAD = count_tokens(_sys_prompt) + _TOOL_SCHEMA_ESTIMATE

        # Pre-compute context pack so we can deduct its size from the budget
        glossary = self.global_assets.get("glossary") if self.global_assets else None
        link_map = self.global_assets.get("link_map") if self.global_assets else None
        context_pack = build_context_pack(
            module_components=core_component_ids,
            components=components,
            index_products=self.index_products,
            glossary=glossary,
            link_map=link_map,
        )
        context_section = format_context_pack_section(context_pack)
        context_tokens = count_tokens(context_section) if context_section else 0

        # Budget for format_user_prompt = total limit - overhead - context pack
        _prompt_budget = max(
            self.config.max_input_tokens - _SYSTEM_PROMPT_OVERHEAD - context_tokens,
            50_000,
        )
        user_prompt = format_user_prompt(
            module_name=module_name,
            core_component_ids=core_component_ids,
            components=components,
            module_tree=module_tree,
            max_input_tokens=_prompt_budget,
        )

        if context_section:
            user_prompt += "\n\n" + context_section

        assigned_filename = self._assigned_doc_filename(module_tree, module_path)
        user_prompt += f"\n\nWrite your documentation to the file: {assigned_filename}"

        prompt_tokens = count_tokens(user_prompt)
        estimated_tokens = prompt_tokens + _SYSTEM_PROMPT_OVERHEAD

        # Decide: use long-context model if over threshold, else truncate to fit
        _use_long_context = (
            self.long_context_model and estimated_tokens > self.config.long_context_threshold
        )

        if not _use_long_context:
            # No long-context model available or not needed — hard-truncate if over budget
            _max_prompt_tokens = self.config.max_input_tokens - _SYSTEM_PROMPT_OVERHEAD
            if prompt_tokens > _max_prompt_tokens:
                from codewiki.src.be.utils import _get_encoder

                enc = _get_encoder("gpt-4")
                tokens = enc.encode(user_prompt)
                user_prompt = enc.decode(tokens[:_max_prompt_tokens])
                prompt_tokens = _max_prompt_tokens
                estimated_tokens = prompt_tokens + _SYSTEM_PROMPT_OVERHEAD
                logger.warning(
                    "⚠️ Hard-truncated prompt for '%s' to %dK tokens (no long-context model)",
                    module_name,
                    estimated_tokens // 1000,
                )
        else:
            logger.info(
                "🔀 Routing '%s' to long-context model (~%dK tokens > %dK threshold)",
                module_name,
                estimated_tokens // 1000,
                self.config.long_context_threshold // 1000,
            )

        file_count = len(
            set(
                getattr(components[c], "relative_path", "")
                for c in core_component_ids
                if c in components
            )
        )
        logger.info(
            "📝 Prompt for '%s': ~%dK tokens (prompt %dK + overhead %dK, %d components, %d files)",
            module_name,
            estimated_tokens // 1000,
            prompt_tokens // 1000,
            _SYSTEM_PROMPT_OVERHEAD // 1000,
            len(core_component_ids),
            file_count,
        )

        # Create agent
        agent = self.create_agent(module_name, components, core_component_ids, estimated_tokens)

        # Create per-agent dependencies (each agent gets its own mutable copies)
        deps = CodeWikiDeps(
            absolute_docs_path=working_dir,
            absolute_repo_path=str(os.path.abspath(self.config.repo_path)),
            registry={},
            components=components,
            path_to_current_module=list(module_path),  # copy to avoid cross-agent mutation
            current_module_name=module_name,
            module_tree=module_tree,
            max_depth=self.config.max_depth,
            current_depth=1,
            config=self.config,
            custom_instructions=self.custom_instructions,
            module_tree_manager=tree_manager,
            fallback_models=self.fallback_models,
            long_context_model=self.long_context_model,
            index_products=self.index_products,
            global_assets=self.global_assets,
            assigned_doc_filename=assigned_filename,
            gen_state=gen_state,
            state_mgr=state_mgr,
            usage_stats=self.usage_stats,
        )

        # Run agent
        try:
            t0 = time.time()
            result = await agent.run(
                user_prompt,
                deps=deps,
                usage_limits=UsageLimits(
                    request_limit=None,
                    request_tokens_limit=None
                    if _use_long_context
                    else self.config.max_input_tokens,
                ),
                event_stream_handler=agent_progress_handler,
            )
            elapsed = time.time() - t0

            # Log which model(s) actually responded (detects fallback switches)
            model_names = []
            for msg in result.all_messages():
                if isinstance(msg, ModelResponse) and msg.model_name:
                    if msg.model_name not in model_names:
                        model_names.append(msg.model_name)
            run_usage = result.usage()
            if self.usage_stats is not None and run_usage:
                record_agent_run_usage(
                    self.usage_stats,
                    model_names,
                    run_usage.input_tokens or 0,
                    run_usage.output_tokens or 0,
                    run_usage.requests or 0,
                )
            models_used = ", ".join(model_names) if model_names else "unknown"
            if len(model_names) > 1:
                logger.info(
                    f"Fallback triggered for '{module_name}': "
                    f"models used: {models_used} ({elapsed:.1f}s)"
                )
            logger.debug(
                f"Successfully processed module: {module_name} "
                f"in {elapsed:.1f}s (model: {models_used})"
            )

            # Persist tree — manager handles locking; otherwise save directly
            if tree_manager:
                await tree_manager.save()
            else:
                module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)
                file_manager.save_json(deps.module_tree, module_tree_path)

            # Mark the module as fully completed so future runs can skip it
            if state_mgr:
                doc_id = doc_id_for_path(module_tree, doc_path_parts)
                output_path = os.path.join(working_dir, assigned_filename)
                await state_mgr.mark_completed(
                    doc_id,
                    content_hash=content_hash(output_path),
                    model=models_used,
                )

            return deps.module_tree, models_used

        except Exception as e:
            logger.error(
                "Error processing module %s (~%dK input tokens): %s",
                module_name,
                estimated_tokens // 1000,
                e,
            )
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise
