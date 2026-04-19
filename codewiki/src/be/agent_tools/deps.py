from dataclasses import dataclass
from typing import Any, Optional
from codewiki.src.be.dependency_analyzer.models.core import Node
from codewiki.src.be.llm_middleware import LLMMiddleware
from codewiki.src.be.llm_usage import LLMUsageStats
from codewiki.src.codewiki_config import CodeWikiConfig


@dataclass
class CodeWikiDeps:
    absolute_docs_path: str
    absolute_repo_path: str
    registry: dict
    components: dict[str, Node]
    path_to_current_module: list[str]
    current_module_name: str
    module_tree: dict[str, Any]
    max_depth: int
    current_depth: int
    config: CodeWikiConfig  # LLM configuration
    custom_instructions: Optional[str] = None
    middleware: LLMMiddleware | None = None
    # v2: Index products and global assets for evidence-driven generation
    index_products: Any = None  # IndexProducts or None
    global_assets: Optional[dict] = None  # {"glossary": dict, "link_map": dict}
    assigned_doc_filename: str = ""
    usage_stats: Optional[LLMUsageStats] = None
