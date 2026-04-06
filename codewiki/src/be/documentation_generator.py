import logging
import os
from typing import Dict, List, Any, Optional
import traceback

from tqdm import tqdm

# Configure logging and monitoring
logger = logging.getLogger(__name__)

# Local imports
from codewiki.src.be.dependency_analyzer import DependencyGraphBuilder
from codewiki.src.be.cache_manager import CacheManager, module_artifact_id, overview_artifact_id
from codewiki.src.be.cluster_modules import cluster_modules, heal_module_tree_components
from codewiki.src.be.llm_middleware import LLMMiddleware
from codewiki.src.codewiki_config import CodeWikiConfig
from codewiki.src.config import (
    FIRST_MODULE_TREE_FILENAME,
    MODULE_TREE_FILENAME,
    OVERVIEW_FILENAME,
)
from codewiki.src.utils import (
    file_manager,
    module_doc_filename,
)
from codewiki.src.be.agent_orchestrator import AgentOrchestrator
from codewiki.src.be.module_tree_manager import ModuleTreeManager
from codewiki.src.be.guide_generator import GuideGenerator
from codewiki.src.be.documentation_tree_utils import (
    build_generation_tasks,
    cleanup_legacy_internal_files,
    config_fingerprint,
    dedup_docs_directory,
    freeze_doc_filenames,
    module_doc_exists,
)
from codewiki.src.be.documentation_overview import (
    OverviewContext,
    build_overview_structure as build_overview_structure_impl,
    collect_child_doc_hashes as collect_child_doc_hashes_impl,
    generate_parent_module_docs as generate_parent_module_docs_impl,
)
from codewiki.src.be.documentation_scheduler import (
    fill_missing_module_docs as fill_missing_module_docs_impl,
    get_processing_levels as get_processing_levels_impl,
    get_processing_order as get_processing_order_impl,
    is_leaf_module as is_leaf_module_impl,
    run_module_queue as run_module_queue_impl,
)
from codewiki.src.be.llm_usage import LLMUsageStats
from codewiki.src.be.cancellation import CancellationToken
from codewiki.src.be.pipeline import (
    GenerationResult,
    ModuleSummary,
    PipelineContext,
    PipelineRunner,
)


class DocumentationGenerator:
    """Main documentation generation orchestrator."""

    def __init__(
        self,
        config: CodeWikiConfig,
        commit_id: str | None = None,
        cancel_token: CancellationToken | None = None,
    ):
        self.config = config
        self.commit_id = commit_id
        self.cancel_token = cancel_token
        self.graph_builder = DependencyGraphBuilder(config, commit_id=commit_id or "")
        self.usage_stats = LLMUsageStats()
        self.middleware = LLMMiddleware(config, usage_stats=self.usage_stats)
        self.cache_manager: CacheManager | None = None
        self.agent_orchestrator = AgentOrchestrator(
            config,
            middleware=self.middleware,
            usage_stats=self.usage_stats,
        )

    @staticmethod
    def _freeze_doc_filenames(tree: Dict[str, Any]) -> None:
        freeze_doc_filenames(tree)

    def _module_doc_exists(
        self,
        working_dir: str,
        module_path: List[str],
        module_tree: Optional[Dict[str, Any]] = None,
    ) -> bool:
        return module_doc_exists(working_dir, module_path, module_tree)

    @staticmethod
    def _detect_repo_url(repo_path: str) -> Optional[str]:
        """Try to detect the GitHub/remote URL from git config."""
        try:
            import subprocess

            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                url = result.stdout.strip()
                if url.startswith("git@github.com:"):
                    url = url.replace("git@github.com:", "https://github.com/")
                url = url.rstrip("/").removesuffix(".git")
                return url
        except Exception:
            pass
        return None

    def create_documentation_metadata(
        self,
        working_dir: str,
        components: Dict[str, Any],
        num_leaf_nodes: int,
        usage_stats: LLMUsageStats | None = None,
        generation_result: GenerationResult | None = None,
    ) -> dict[str, Any]:
        """Create a metadata file with documentation generation information."""
        from datetime import datetime

        repo_url = self._detect_repo_url(self.config.repo_path)
        metadata = {
            "generation_info": {
                "timestamp": datetime.now().isoformat(),
                "main_model": self.config.main_model,
                "generator_version": "1.0.1",
                "repo_path": self.config.repo_path,
                "repo_url": repo_url,
                "commit_id": self.commit_id,
            },
            "statistics": {
                "total_components": len(components),
                "leaf_nodes": num_leaf_nodes,
                "max_depth": self.config.max_depth,
            },
            "files_generated": ["overview.md", "module_tree.json", "first_module_tree.json"],
        }
        if usage_stats is not None:
            metadata["statistics"]["token_usage"] = usage_stats.to_dict()
        if generation_result is not None:
            metadata.update(generation_result.to_metadata_dict())

        # Add generated markdown files to the metadata
        try:
            for file_path in os.listdir(working_dir):
                if file_path.endswith(".md") and file_path not in metadata["files_generated"]:
                    metadata["files_generated"].append(file_path)
        except Exception as e:
            logger.warning(f"Could not list generated files: {e}")

        metadata_path = os.path.join(working_dir, "metadata.json")
        file_manager.save_json(metadata, metadata_path)
        logger.info("💾 Metadata written to %s", metadata_path)
        return metadata

    def get_processing_levels(
        self, module_tree: Dict[str, Any], parent_path: Optional[List[str]] = None
    ) -> List[List[tuple]]:
        return get_processing_levels_impl(module_tree, parent_path)

    def get_processing_order(
        self, module_tree: Dict[str, Any], parent_path: Optional[List[str]] = None
    ) -> List[tuple[List[str], str]]:
        return get_processing_order_impl(module_tree, parent_path)

    def is_leaf_module(self, module_info: Dict[str, Any]) -> bool:
        return is_leaf_module_impl(module_info)

    def build_overview_structure(
        self, module_tree: Dict[str, Any], module_path: List[str], working_dir: str
    ) -> Dict[str, Any]:
        cache_manager = getattr(self, "cache_manager", None)
        return build_overview_structure_impl(
            OverviewContext(
                config=self.config,
                module_tree=module_tree,
                working_dir=working_dir,
                middleware=self.middleware,
                cache_manager=cache_manager,
            ),
            module_path,
        )

    # ── Main entry point ─────────────────────────────────────────────────

    async def generate_module_documentation(
        self, components: Dict[str, Any], leaf_nodes: List[str]
    ) -> tuple[str, ModuleSummary]:
        """Generate documentation for all modules using level-based concurrency."""
        working_dir = os.path.abspath(self.config.docs_dir)
        # Prepare output directory
        file_manager.ensure_directory(working_dir)
        cleanup_legacy_internal_files(working_dir)

        module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)
        first_module_tree_path = os.path.join(working_dir, FIRST_MODULE_TREE_FILENAME)
        module_tree = file_manager.load_json(module_tree_path) or {}
        first_module_tree = file_manager.load_json(first_module_tree_path) or {}

        if not module_tree:
            # Small repo that fits in a single context — no parallelism needed
            logger.info("Processing whole repo because repo can fit in the context window")
            repo_name = os.path.basename(os.path.normpath(self.config.repo_path))
            final_module_tree, _ = await self.agent_orchestrator.process_module(
                repo_name,
                components,
                leaf_nodes,
                [],
                working_dir,
                cache_manager=self.cache_manager,
            )

            file_manager.save_json(final_module_tree, module_tree_path)

            repo_overview_path = os.path.join(working_dir, module_doc_filename([repo_name]))
            if os.path.exists(repo_overview_path):
                os.rename(repo_overview_path, os.path.join(working_dir, OVERVIEW_FILENAME))

            return working_dir, ModuleSummary()

        dedup_docs_directory(working_dir)

        freeze_doc_filenames(module_tree)
        freeze_doc_filenames(first_module_tree)
        file_manager.save_json(module_tree, module_tree_path)
        file_manager.save_json(first_module_tree, first_module_tree_path)

        await self._initialize_cache_from_tree(module_tree, working_dir)

        # ── Dynamic task-queue concurrent path ────────────────────────────
        tree_manager = ModuleTreeManager(module_tree, module_tree_path)
        max_concurrent = self.config.max_concurrent
        max_retries = self.config.max_retries

        graph_tree = await tree_manager.get_snapshot()
        logger.info(
            f"📊 Running queue on {len(graph_tree)} top-level modules (concurrency={max_concurrent})"
        )
        summary = await self._run_module_queue(
            graph_tree,
            components,
            working_dir,
            tree_manager,
            desc="Generating docs",
            include_root=False,
        )

        # ── Fill any modules whose .md was not written ────────────────────
        fill_summary = await self._fill_missing_module_docs(
            working_dir, components, tree_manager, max_retries
        )
        summary.extend(fill_summary)

        # ── Generate repo-level overview after all modules are complete ───
        logger.info("📚 Generating repository overview")
        await self.generate_parent_module_docs([], working_dir, tree_manager)

        return working_dir, summary

    async def _run_module_queue(
        self,
        graph_tree: Dict[str, Any],
        components: Dict[str, Any],
        working_dir: str,
        tree_manager,
        desc: str = "Generating docs",
        include_root: bool = True,
    ) -> ModuleSummary:
        cache_manager = getattr(self, "cache_manager", None)

        async def _generate_root_overview() -> None:
            await self.generate_parent_module_docs([], working_dir, tree_manager)

        return await run_module_queue_impl(
            config=self.config,
            graph_tree=graph_tree,
            components=components,
            working_dir=working_dir,
            tree_manager=tree_manager,
            process_module=self.agent_orchestrator.process_module,
            generate_root_overview=(_generate_root_overview if include_root else None),
            desc=desc,
            include_root=include_root,
            cache_manager=cache_manager,
            progress_factory=tqdm,
            cancel_token=getattr(self, "cancel_token", None),
        )

    async def _fill_missing_module_docs(
        self,
        working_dir: str,
        components: Dict[str, Any],
        tree_manager,
        max_retries: int,
    ) -> ModuleSummary:
        cache_manager = getattr(self, "cache_manager", None)
        return await fill_missing_module_docs_impl(
            config=self.config,
            working_dir=working_dir,
            components=components,
            tree_manager=tree_manager,
            run_module_queue=lambda **kwargs: self._run_module_queue(
                kwargs["graph_tree"],
                kwargs["components"],
                kwargs["working_dir"],
                kwargs["tree_manager"],
                desc=kwargs["desc"],
                include_root=kwargs["include_root"],
            ),
            module_doc_exists=module_doc_exists,
            cache_manager=cache_manager,
            cancel_token=getattr(self, "cancel_token", None),
        )

    # ── Parent / overview generation ─────────────────────────────────────

    def _collect_child_doc_hashes(
        self,
        module_tree: Dict[str, Any],
        module_path: List[str],
        working_dir: str,
    ) -> Dict[str, str]:
        return collect_child_doc_hashes_impl(
            OverviewContext(
                config=self.config,
                module_tree=module_tree,
                working_dir=working_dir,
                middleware=self.middleware,
            ),
            module_path,
        )

    async def generate_parent_module_docs(
        self,
        module_path: List[str],
        working_dir: str,
        tree_manager: Optional[ModuleTreeManager] = None,
    ) -> Dict[str, Any]:
        cache_manager = getattr(self, "cache_manager", None)
        return await generate_parent_module_docs_impl(
            OverviewContext(
                config=self.config,
                module_tree={},
                working_dir=working_dir,
                tree_manager=tree_manager,
                middleware=self.middleware,
                usage_stats=self.usage_stats,
                cache_manager=cache_manager,
            ),
            module_path,
        )

    def _build_initial_context(self) -> PipelineContext:
        if self.cache_manager is None:
            cache_dir = os.path.join(self.config.docs_dir, ".codewiki")
            os.makedirs(cache_dir, exist_ok=True)
            self.cache_manager = CacheManager(cache_dir)
            self.cache_manager.start()
        return PipelineContext(
            config=self.config,
            working_dir=os.path.abspath(self.config.docs_dir),
            usage_stats=self.usage_stats,
            graph_builder=self.graph_builder,
            agent_orchestrator=self.agent_orchestrator,
            generator=self,
            cancel_token=getattr(self, "cancel_token", None),
            commit_id=self.commit_id or "",
            cache_manager=self.cache_manager,
        )

    async def _build_index(self, ctx: PipelineContext) -> None:
        from codewiki.src.be.index.index_builder import IndexBuilder

        index_builder = IndexBuilder(
            repo_path=self.config.repo_path,
            include_patterns=self.config.include_patterns,
            exclude_patterns=self.config.exclude_patterns,
            output_dir=self.config.docs_dir,
        )
        ctx.index_products = index_builder.build()

    async def _cluster_modules(self, ctx: PipelineContext) -> None:
        from codewiki.src.be.generation.glossary import build_glossary, build_link_map

        working_dir = os.path.abspath(self.config.docs_dir)
        file_manager.ensure_directory(working_dir)
        first_module_tree_path = os.path.join(working_dir, FIRST_MODULE_TREE_FILENAME)
        module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)
        current_config_fp = config_fingerprint(self.config)
        cache_meta = self.cache_manager.get_metadata() if self.cache_manager else {}

        cached_tree = (
            file_manager.load_json(first_module_tree_path)
            if os.path.exists(first_module_tree_path)
            else None
        )

        need_recluster = True
        commit_changed = False
        if cached_tree:
            if cache_meta.get("config_fingerprint") == current_config_fp:
                need_recluster = False
                commit_changed = cache_meta.get("repo_commit", "") != (self.commit_id or "")
                if commit_changed:
                    logger.info("Commit changed — reusing cluster layout, updating components")
                else:
                    logger.debug("Module tree cache hit (same commit + config)")
            else:
                logger.info("Module tree cache invalidated (config changed: language or max_depth)")

        if not need_recluster:
            assert cached_tree is not None
            if ctx.components:
                # Heal tree with current components (GraphBuild always runs)
                module_tree = heal_module_tree_components(cached_tree, ctx.components)
            else:
                module_tree = cached_tree
            freeze_doc_filenames(module_tree)
            file_manager.save_json(module_tree, first_module_tree_path)
            if not os.path.exists(module_tree_path):
                file_manager.save_json(module_tree, module_tree_path)
        else:
            logger.debug("Clustering modules (no valid cache at %s)", first_module_tree_path)
            module_tree = cluster_modules(
                ctx.leaf_nodes,
                ctx.components,
                self.config,
                index_products=ctx.index_products,
                usage_stats=self.usage_stats,
                middleware=self.middleware,
            )
            if module_tree:
                freeze_doc_filenames(module_tree)
                file_manager.save_json(module_tree, first_module_tree_path)
                file_manager.save_json(module_tree, module_tree_path)

        if self.cache_manager:
            self.cache_manager.update_metadata(
                repo_commit=self.commit_id or "",
                config_fingerprint=current_config_fp,
            )
            self.cache_manager.flush()
            logger.debug("💾 Clustering cache metadata persisted")

        try:
            glossary = build_glossary(ctx.index_products) if ctx.index_products else {}
            link_map = build_link_map(module_tree) if module_tree else {}
            self.agent_orchestrator.set_generation_context(
                index_products=ctx.index_products,
                global_assets={"glossary": glossary, "link_map": link_map},
            )
            logger.info(
                "Generation v2 context set: %s glossary terms, %s link map entries",
                len(glossary),
                len(link_map),
            )
        except Exception as exc:
            logger.warning("Failed to set generation v2 context; continuing without", exc_info=True)
            ctx.result.add_warning(f"Generation context setup failed: {exc}")

        ctx.working_dir = working_dir
        ctx.module_tree = module_tree

    async def _initialize_cache_from_tree(
        self,
        module_tree: Dict[str, Any],
        working_dir: str,
    ) -> None:
        cleanup_legacy_internal_files(working_dir)

        module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)
        first_module_tree_path = os.path.join(working_dir, FIRST_MODULE_TREE_FILENAME)
        dedup_docs_directory(working_dir)
        freeze_doc_filenames(module_tree)
        file_manager.save_json(module_tree, module_tree_path)
        file_manager.save_json(module_tree, first_module_tree_path)
        if not self.cache_manager:
            return

        for task in build_generation_tasks(module_tree, self.config):
            if task.doc_id == "overview:root":
                artifact_id = "overview:root"
            elif task.kind == "overview":
                artifact_id = overview_artifact_id(task.doc_id)
            else:
                artifact_id = module_artifact_id(task.doc_id)
            self.cache_manager.plan_task(artifact_id, output_file=task.output_file)

        self.cache_manager.update_metadata(
            repo_commit=self.commit_id or "",
            config_fingerprint=config_fingerprint(self.config),
        )
        self.cache_manager.flush()

    async def _generate_docs_from_tree(
        self,
        components: Dict[str, Any],
        leaf_nodes: List[str],
        working_dir: str,
        module_tree: Dict[str, Any],
    ) -> tuple[str, ModuleSummary]:
        if not module_tree:
            logger.info("Processing whole repo because repo can fit in the context window")
            repo_name = os.path.basename(os.path.normpath(self.config.repo_path))
            final_module_tree, _ = await self.agent_orchestrator.process_module(
                repo_name, components, leaf_nodes, [], working_dir, cache_manager=self.cache_manager
            )

            module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)
            file_manager.save_json(final_module_tree, module_tree_path)

            repo_overview_path = os.path.join(working_dir, module_doc_filename([repo_name]))
            if os.path.exists(repo_overview_path):
                os.rename(repo_overview_path, os.path.join(working_dir, OVERVIEW_FILENAME))

            return working_dir, ModuleSummary()

        tree_manager = ModuleTreeManager(
            module_tree, os.path.join(working_dir, MODULE_TREE_FILENAME)
        )
        self._tree_manager = tree_manager  # expose for pipeline flush
        graph_tree = await tree_manager.get_snapshot()
        logger.info(
            "📊 Running queue on %s top-level modules (concurrency=%s)",
            len(graph_tree),
            self.config.max_concurrent,
        )
        summary = await self._run_module_queue(
            graph_tree,
            components,
            working_dir,
            tree_manager,
            desc="Generating docs",
            include_root=False,
        )
        if not (self.cancel_token and self.cancel_token.is_cancelled):
            fill_summary = await self._fill_missing_module_docs(
                working_dir, components, tree_manager, self.config.max_retries
            )
            summary.extend(fill_summary)

        logger.info("📚 Generating repository overview")
        await self.generate_parent_module_docs([], working_dir, tree_manager)
        return working_dir, summary

    async def _generate_guides(self, ctx: PipelineContext) -> None:
        logger.info("📖 Starting guide document generation")
        guide_gen = GuideGenerator(
            config=self.config,
            components=ctx.components,
            module_tree=ctx.module_tree,
            working_dir=ctx.working_dir,
            usage_stats=self.usage_stats,
            cancel_token=ctx.cancel_token,
            middleware=self.middleware,
            cache_manager=self.cache_manager,
        )
        await guide_gen.run()

    def _postprocess_docs(self, ctx: PipelineContext) -> None:
        from codewiki.src.be.docs_fixer import fix_docs

        fix_docs(
            ctx.working_dir,
            self.config,
            usage_stats=self.usage_stats,
            middleware=self.middleware,
            cache_manager=self.cache_manager,
        )

    def _write_metadata(self, ctx: PipelineContext) -> dict[str, Any]:
        return self.create_documentation_metadata(
            ctx.working_dir,
            ctx.components,
            len(ctx.leaf_nodes),
            usage_stats=self.usage_stats,
            generation_result=ctx.result,
        )

    async def run(self) -> GenerationResult:
        """Run documentation generation via the staged pipeline."""
        from codewiki.src.be.stages import DEFAULT_STAGES

        try:
            runner = PipelineRunner(list(DEFAULT_STAGES))
            result = await runner.execute(self._build_initial_context())
            logger.debug("Documentation generation completed with status=%s", result.status)
            return result
        except Exception as e:
            logger.error(f"Documentation generation failed: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise
