from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING
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
