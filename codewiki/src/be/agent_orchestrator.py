import time
import re
import unicodedata
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
from codewiki.src.be.cache_manager import module_artifact_id, overview_artifact_id
from codewiki.src.be.documentation_tree_utils import (
    compute_module_input_hash,
    select_effective_component_ids,
)
from codewiki.src.be.llm_middleware import LLMMiddleware
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
    doc_id_for_path,
    file_manager,
    module_doc_filename,
    find_module_doc,
)
from codewiki.src.be.dependency_analyzer.models.core import Node


class AgentOrchestrator:
    """Orchestrates the AI agents for documentation generation."""

    _SUMMARY_SECTION_TITLES = {
        "abstract",
        "apercu",
        "aperçu",
        "description generale",
        "description générale",
        "einfuhrung",
        "einführung",
        "introduction",
        "objective",
        "objectifs",
        "overview",
        "presentation",
        "présentation",
        "purpose",
        "resume",
        "resumen",
        "résumé",
        "summary",
        "synopsis",
        "uberblick",
        "überblick",
        "vision general",
        "vision générale",
        "visao geral",
        "visão geral",
        "介绍",
        "摘要",
        "概况",
        "概括",
        "概览",
        "概觀",
        "概述",
        "概要",
        "简介",
        "簡介",
        "總覽",
        "總結",
        "总览",
        "总结",
        "說明",
        "说明",
        "要約",
        "概要説明",
        "紹介",
        "概観",
        "개요",
        "요약",
        "소개",
        "설명",
        "обзор",
        "резюме",
        "введение",
    }

    def __init__(
        self,
        config: CodeWikiConfig,
        middleware: LLMMiddleware,
        usage_stats: LLMUsageStats | None = None,
    ):
        self.config = config
        self.usage_stats = usage_stats
        self._middleware = middleware
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

    @classmethod
    def _heading_title(cls, line: str) -> str | None:
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        if not match:
            return None
        return match.group(1).strip().strip(":：").lower()

    @classmethod
    def _normalize_heading_key(cls, title: str) -> str:
        normalized = unicodedata.normalize("NFKD", title.casefold())
        ascii_like = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        ascii_like = re.sub(r"\s+", " ", ascii_like)
        return ascii_like.strip()

    @classmethod
    def _extract_prose_blocks(cls, lines: list[str]) -> list[str]:
        blocks: list[str] = []
        current: list[str] = []
        in_code = False

        def _flush() -> None:
            if current:
                blocks.append("\n".join(current).strip())
                current.clear()

        for raw in lines:
            stripped = raw.strip()
            if stripped.startswith("```"):
                _flush()
                in_code = not in_code
                continue
            if in_code:
                continue
            if not stripped:
                _flush()
                continue
            if cls._heading_title(raw) is not None:
                _flush()
                continue
            if stripped.startswith(("- ", "* ", "+ ", "> ", "|")) or re.match(
                r"^\d+\.\s", stripped
            ):
                _flush()
                continue
            current.append(stripped)

        _flush()
        return blocks

    @classmethod
    def _extract_first_paragraph(cls, lines: list[str]) -> str:
        body = list(lines)
        while body and (not body[0].strip() or cls._heading_title(body[0]) is not None):
            body.pop(0)
        blocks = cls._extract_prose_blocks(body)
        return blocks[0] if blocks else ""

    @classmethod
    def _extract_summary_sections(cls, lines: list[str]) -> list[str]:
        sections: list[tuple[int, int, str]] = []
        i = 0
        heading_index = -1
        while i < len(lines):
            title = cls._heading_title(lines[i])
            if title is None:
                i += 1
                continue

            heading_index += 1
            level = len(lines[i].lstrip()) - len(lines[i].lstrip("#"))
            i += 1
            section_lines: list[str] = []
            while i < len(lines) and cls._heading_title(lines[i]) is None:
                section_lines.append(lines[i].rstrip())
                i += 1

            section_text = "\n".join(section_lines).strip()
            prose_blocks = cls._extract_prose_blocks(section_lines)
            if not section_text or not prose_blocks:
                continue

            paragraph_count = len(prose_blocks)
            normalized_title = cls._normalize_heading_key(title)
            if normalized_title in cls._SUMMARY_SECTION_TITLES:
                sections.append((heading_index, level, section_text))
                continue
            # Structure-first heuristic:
            # - prefer sections near the top
            # - prefer H1/H2
            # - prefer prose-dominant, compact explanatory sections
            if level > 2:
                continue
            if heading_index > 3:
                continue
            if paragraph_count > 4:
                continue

            sections.append((heading_index, level, section_text))

        sections.sort(key=lambda item: (item[0], item[1]))
        return [section for _, _, section in sections]

    @classmethod
    def _summarize_child_doc(cls, content: str) -> str:
        lines = [line.rstrip() for line in content.splitlines()]
        parts: list[str] = []
        seen: set[str] = set()

        first_paragraph = cls._extract_first_paragraph(lines)
        if first_paragraph:
            normalized = first_paragraph.strip()
            if normalized not in seen:
                parts.append(normalized)
                seen.add(normalized)

        for section in cls._extract_summary_sections(lines):
            normalized = section.strip()
            if normalized not in seen:
                parts.append(normalized)
                seen.add(normalized)

        return "\n\n".join(parts)

    def _build_child_doc_summaries(
        self,
        module_path: list[str],
        current_node: dict[str, Any] | None,
        working_dir: str,
    ) -> str:
        children = (current_node or {}).get("children") or {}
        if not isinstance(children, dict) or not children:
            return ""

        sections: list[str] = []
        for child_name in sorted(children):
            child_info = children.get(child_name) or {}
            child_filename = child_info.get(
                "_doc_filename",
                module_doc_filename(module_path + [child_name]),
            )
            child_path = os.path.join(working_dir, child_filename)
            if not os.path.exists(child_path):
                continue
            try:
                child_content = file_manager.load_text(child_path)
            except OSError:
                continue
            summary = self._summarize_child_doc(child_content)
            if not summary:
                continue
            sections.append(f"### {child_name}\nFile: {child_filename}\n{summary}")

        if not sections:
            return ""
        return (
            "<CHILD_MODULE_DOC_SUMMARIES>\n"
            + "\n\n".join(sections)
            + "\n</CHILD_MODULE_DOC_SUMMARIES>"
        )

    def create_agent(
        self,
        module_name: str,
        components: Dict[str, Any],
        core_component_ids: List[str],
        estimated_tokens: int = 0,
    ) -> Agent[CodeWikiDeps, str]:
        """Create an appropriate agent based on module complexity."""
        model = self._middleware.create_agent_model()
        custom_instructions = self.custom_instructions or ""

        if is_complex_module(components, core_component_ids):
            return Agent(
                model,
                name=module_name,
                deps_type=CodeWikiDeps,
                tools=[
                    read_code_components_tool,
                    str_replace_editor_tool,
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
        cache_manager=None,
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

        # ── Cache check ──────────────────────────────────────────────────
        doc_path_parts = module_path if module_path else [module_name]
        docs_path = find_module_doc(working_dir, doc_path_parts)

        if docs_path and os.path.getsize(docs_path) > 100:
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
                    return {}, "cached"

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

        current_node = None
        try:
            current_node = module_tree
            for part in module_path:
                current_node = current_node[part]
        except Exception:
            current_node = None

        assigned_filename = self._assigned_doc_filename(module_tree, module_path)
        effective_component_ids = (
            select_effective_component_ids(current_node, components)
            if current_node is not None
            else list(core_component_ids)
        )
        if cache_manager and current_node is not None:
            current_doc_id = doc_id_for_path(module_tree, doc_path_parts)
            artifact_id = (
                overview_artifact_id(current_doc_id)
                if current_node.get("children")
                else module_artifact_id(current_doc_id)
            )
            input_hash = compute_module_input_hash(
                module_name,
                module_path,
                current_node,
                components,
                self.config,
                assigned_file=assigned_filename,
            )
            if cache_manager.is_valid(artifact_id, input_hash):
                logger.debug("✓ Cache hit for '%s'", module_name)
                return {}, "cached"

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
            module_components=effective_component_ids,
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
            core_component_ids=effective_component_ids,
            components=components,
            module_tree=module_tree,
            max_input_tokens=_prompt_budget,
        )

        if context_section:
            user_prompt += "\n\n" + context_section
        child_docs_section = self._build_child_doc_summaries(module_path, current_node, working_dir)
        if child_docs_section:
            user_prompt += "\n\n" + child_docs_section

        user_prompt += f"\n\nWrite your documentation to the file: {assigned_filename}"

        prompt_tokens = count_tokens(user_prompt)
        estimated_tokens = prompt_tokens + _SYSTEM_PROMPT_OVERHEAD

        # Hard-truncate if over the absolute max.
        # Model selection (normal vs long-context) happens in create_agent
        # based on estimated_tokens computed here.
        _absolute_max = (
            self.config.long_context_max_input_tokens
            if self.config.long_context_model
            else self.config.max_input_tokens
        )
        _max_prompt_tokens = _absolute_max - _SYSTEM_PROMPT_OVERHEAD
        if prompt_tokens > _max_prompt_tokens:
            from codewiki.src.be.utils import _get_encoder

            enc = _get_encoder("gpt-4")
            tokens = enc.encode(user_prompt)
            user_prompt = enc.decode(tokens[:_max_prompt_tokens])
            prompt_tokens = _max_prompt_tokens
            estimated_tokens = prompt_tokens + _SYSTEM_PROMPT_OVERHEAD
            logger.warning(
                "⚠️ Hard-truncated prompt for '%s' to %dK tokens",
                module_name,
                estimated_tokens // 1000,
            )

        file_count = len(
            set(
                getattr(components[c], "relative_path", "")
                for c in effective_component_ids
                if c in components
            )
        )
        logger.info(
            "📝 Prompt for '%s': ~%dK tokens (prompt %dK + overhead %dK, %d components, %d files)",
            module_name,
            estimated_tokens // 1000,
            prompt_tokens // 1000,
            _SYSTEM_PROMPT_OVERHEAD // 1000,
            len(effective_component_ids),
            file_count,
        )

        # Create agent
        agent = self.create_agent(
            module_name, components, effective_component_ids, estimated_tokens
        )

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
            middleware=self._middleware,
            index_products=self.index_products,
            global_assets=self.global_assets,
            assigned_doc_filename=assigned_filename,
            usage_stats=self.usage_stats,
        )

        try:
            t0 = time.time()
            result = await agent.run(
                user_prompt,
                deps=deps,
                usage_limits=UsageLimits(request_limit=None),
                event_stream_handler=agent_progress_handler,
            )
            elapsed = time.time() - t0

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
                    "Fallback triggered for '%s': models used: %s (%.1fs)",
                    module_name,
                    models_used,
                    elapsed,
                )
            logger.debug(
                "Successfully processed module: %s in %.1fs (model: %s)",
                module_name,
                elapsed,
                models_used,
            )

            if tree_manager:
                await tree_manager.save()
            else:
                module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)
                file_manager.save_json(deps.module_tree, module_tree_path)

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
