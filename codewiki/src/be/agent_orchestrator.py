import time

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse
from pydantic_ai.usage import UsageLimits
# import logfire
import logging
import os
import traceback
from typing import Dict, List, Any

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
from codewiki.src.be.agent_tools.generate_sub_module_documentations import generate_sub_module_documentation_tool
from codewiki.src.be.llm_services import create_fallback_models
from codewiki.src.be.prompt_template import (
    format_user_prompt,
    format_system_prompt,
    format_leaf_system_prompt,
    format_overview_prompt,
)
from codewiki.src.be.utils import is_complex_module, agent_progress_handler
from codewiki.src.config import (
    Config,
    MODULE_TREE_FILENAME,
)
from codewiki.src.utils import file_manager
from codewiki.src.be.dependency_analyzer.models.core import Node


class AgentOrchestrator:
    """Orchestrates the AI agents for documentation generation."""
    
    def __init__(self, config: Config):
        self.config = config
        self.fallback_models = create_fallback_models(config)
        self.custom_instructions = config.get_prompt_addition() if config else None
        self.output_language = config.output_language if config else "en"
    
    def create_agent(self, module_name: str, components: Dict[str, Any], 
                    core_component_ids: List[str]) -> Agent:
        """Create an appropriate agent based on module complexity."""
        
        if is_complex_module(components, core_component_ids):
            return Agent(
                self.fallback_models,
                name=module_name,
                deps_type=CodeWikiDeps,
                tools=[
                    read_code_components_tool, 
                    str_replace_editor_tool, 
                    generate_sub_module_documentation_tool
                ],
                system_prompt=format_system_prompt(module_name, self.custom_instructions, self.output_language),
            )
        else:
            return Agent(
                self.fallback_models,
                name=module_name,
                deps_type=CodeWikiDeps,
                tools=[read_code_components_tool, str_replace_editor_tool],
                system_prompt=format_leaf_system_prompt(module_name, self.custom_instructions, self.output_language),
            )
    
    async def process_module(self, module_name: str, components: Dict[str, Node],
                           core_component_ids: List[str], module_path: List[str],
                           working_dir: str, tree_manager=None) -> Dict[str, Any]:
        """Process a single module and generate its documentation.

        Args:
            tree_manager: Optional ModuleTreeManager for lock-protected
                tree access during concurrent processing.
        """
        logger.info(f"Processing module: {module_name}")

        # ── Cache check ──────────────────────────────────────────────────
        docs_path = os.path.join(working_dir, f"{module_name}.md")
        if os.path.exists(docs_path) and os.path.getsize(docs_path) > 100:
            # For complex modules, only skip when the tree node is marked
            # _completed (meaning this module AND all its sub-modules finished
            # successfully in a previous run).  Without the flag the tree may
            # have been corrupted/overwritten and sub-modules lost.
            if is_complex_module(components, core_component_ids) and module_path:
                completed = False
                if tree_manager:
                    snapshot = await tree_manager.get_snapshot()
                    try:
                        node = snapshot
                        for key in module_path[:-1]:
                            node = node[key]["children"]
                        completed = node.get(module_path[-1], {}).get("_completed", False)
                    except (KeyError, TypeError):
                        pass
                if not completed and tree_manager:
                    # Auto-infer: if all child modules also have docs, mark
                    # completed and skip (handles modules from before the
                    # _completed flag was introduced).
                    children = {}
                    try:
                        info_node = snapshot
                        for key in module_path[:-1]:
                            info_node = info_node[key]["children"]
                        children = info_node.get(module_path[-1], {}).get("children", {})
                    except (KeyError, TypeError):
                        pass
                    if children and all(
                        os.path.exists(os.path.join(working_dir, f"{cn}.md"))
                        and os.path.getsize(os.path.join(working_dir, f"{cn}.md")) > 100
                        for cn in children
                    ):
                        logger.debug(
                            f"✓ Module {module_name} and all children have docs — auto-marking complete"
                        )
                        await tree_manager.mark_completed(module_path)
                        return {}
                if not completed:
                    logger.debug(
                        f"↩ Module {module_name} exists but is complex and not marked complete — re-processing"
                    )
                else:
                    logger.debug(f"✓ Module docs already exists at {docs_path}")
                    return {}
            else:
                # Leaf / simple module — .md existence is sufficient
                logger.debug(f"✓ Module docs already exists at {docs_path}")
                return {}

        # ── Get module tree snapshot ─────────────────────────────────────
        if tree_manager:
            module_tree = await tree_manager.get_snapshot()
        else:
            module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)
            module_tree = file_manager.load_json(module_tree_path)

        # Create agent
        agent = self.create_agent(module_name, components, core_component_ids)

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
        )

        # Run agent
        try:
            t0 = time.time()
            result = await agent.run(
                format_user_prompt(
                    module_name=module_name,
                    core_component_ids=core_component_ids,
                    components=components,
                    module_tree=deps.module_tree
                ),
                deps=deps,
                usage_limits=UsageLimits(request_limit=None),
                event_stream_handler=agent_progress_handler,
            )
            elapsed = time.time() - t0

            # Log which model(s) actually responded (detects fallback switches)
            model_names = []
            for msg in result.all_messages():
                if isinstance(msg, ModelResponse) and msg.model_name:
                    if msg.model_name not in model_names:
                        model_names.append(msg.model_name)
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
            if tree_manager and module_path:
                await tree_manager.mark_completed(module_path)

            return deps.module_tree

        except Exception as e:
            logger.error(f"Error processing module {module_name}: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise