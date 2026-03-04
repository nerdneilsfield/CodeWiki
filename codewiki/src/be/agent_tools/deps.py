from dataclasses import dataclass, field
from typing import Any, Optional, Set, TYPE_CHECKING
from codewiki.src.be.dependency_analyzer.models.core import Node
from codewiki.src.config import Config

if TYPE_CHECKING:
    from codewiki.src.be.module_tree_manager import ModuleTreeManager

@dataclass
class CodeWikiDeps:
    absolute_docs_path: str
    absolute_repo_path: str
    registry: dict
    components: dict[str, Node]
    path_to_current_module: list[str]
    current_module_name: str
    module_tree: dict[str, any]
    max_depth: int
    current_depth: int
    config: Config  # LLM configuration
    custom_instructions: str = None
    module_tree_manager: Optional['ModuleTreeManager'] = None
    fallback_models: Any = None        # pre-built FallbackModel from AgentOrchestrator
    long_context_model: Any = None     # pre-built OpenAIModel (long context) or None
    # Tracks sub-module names already dispatched in this agent run to prevent
    # the LLM from processing the same sub-module twice via repeated tool calls.
    _dispatched_sub_modules: Set[str] = field(default_factory=set)
