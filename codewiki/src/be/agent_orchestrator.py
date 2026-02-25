from pydantic_ai import Agent
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
from codewiki.src.be.utils import is_complex_module
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

        # skip if this module's doc already exists and has real content
        docs_path = os.path.join(working_dir, f"{module_name}.md")
        if os.path.exists(docs_path) and os.path.getsize(docs_path) > 100:
            logger.info(f"✓ Module docs already exists at {docs_path}")
            return {}

        # Get module tree snapshot (from manager or disk)
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
            result = await agent.run(
                format_user_prompt(
                    module_name=module_name,
                    core_component_ids=core_component_ids,
                    components=components,
                    module_tree=deps.module_tree
                ),
                deps=deps
            )

            # Persist tree — manager handles locking; otherwise save directly
            if tree_manager:
                await tree_manager.save()
            else:
                module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)
                file_manager.save_json(deps.module_tree, module_tree_path)

            logger.debug(f"Successfully processed module: {module_name}")
            return deps.module_tree

        except Exception as e:
            logger.error(f"Error processing module {module_name}: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise