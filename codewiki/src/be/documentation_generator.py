import logging
import os
from typing import Dict, List, Any, Optional
import traceback

from tqdm import tqdm

# Configure logging and monitoring
logger = logging.getLogger(__name__)

# Local imports
from codewiki.src.be.dependency_analyzer import DependencyGraphBuilder
from codewiki.src.be.llm_services import call_llm
from codewiki.src.be.cluster_modules import cluster_modules, heal_module_tree_components
from codewiki.src.config import (
    Config,
    FIRST_MODULE_TREE_FILENAME,
    MODULE_TREE_FILENAME,
    OVERVIEW_FILENAME,
    GENERATION_STATE_FILENAME,
    internal_file_path,
)
from codewiki.src.utils import (
    file_manager,
    module_doc_filename,
)
from codewiki.src.be.agent_orchestrator import AgentOrchestrator
from codewiki.src.be.module_tree_manager import ModuleTreeManager
from codewiki.src.be.guide_generator import GuideGenerator
from codewiki.src.be.generation_state import GenerationState, GenerationStateManager, DocTask
from codewiki.src.be.documentation_tree_utils import (
    build_generation_tasks,
    cleanup_legacy_internal_files,
    config_fingerprint,
    dedup_docs_directory,
    freeze_doc_filenames,
    hash_mapping,
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


class DocumentationGenerator:
    """Main documentation generation orchestrator."""

    def __init__(self, config: Config, commit_id: str | None = None):
        self.config = config
        self.commit_id = commit_id
        self.graph_builder = DependencyGraphBuilder(config)
        self.usage_stats = LLMUsageStats()
        self.agent_orchestrator = AgentOrchestrator(config, usage_stats=self.usage_stats)
        self._gen_state: Optional[GenerationState] = None
        self._state_mgr: Optional[GenerationStateManager] = None

    @staticmethod
    def _freeze_doc_filenames(tree: Dict[str, Any]) -> None:
        freeze_doc_filenames(tree)

    def _build_generation_tasks(self, tree: Dict[str, Any]) -> list[DocTask]:
        return build_generation_tasks(tree, self.config)

    def _module_doc_exists(
        self,
        working_dir: str,
        module_path: List[str],
        module_tree: Optional[Dict[str, Any]] = None,
    ) -> bool:
        return module_doc_exists(working_dir, module_path, module_tree, self._gen_state)

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
    ):
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

        # Add generated markdown files to the metadata
        try:
            for file_path in os.listdir(working_dir):
                if file_path.endswith(".md") and file_path not in metadata["files_generated"]:
                    metadata["files_generated"].append(file_path)
        except Exception as e:
            logger.warning(f"Could not list generated files: {e}")

        metadata_path = os.path.join(working_dir, "metadata.json")
        file_manager.save_json(metadata, metadata_path)

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
        return build_overview_structure_impl(
            OverviewContext(
                config=self.config,
                module_tree=module_tree,
                working_dir=working_dir,
                gen_state=self._gen_state,
            ),
            module_path,
        )

    # ── Main entry point ─────────────────────────────────────────────────

    async def generate_module_documentation(
        self, components: Dict[str, Any], leaf_nodes: List[str]
    ) -> str:
        """Generate documentation for all modules using level-based concurrency."""
        # Prepare output directory
        working_dir = os.path.abspath(self.config.docs_dir)
        file_manager.ensure_directory(working_dir)
        cleanup_legacy_internal_files(working_dir)

        module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)
        first_module_tree_path = os.path.join(working_dir, FIRST_MODULE_TREE_FILENAME)
        state_path = internal_file_path(working_dir, GENERATION_STATE_FILENAME)
        module_tree = file_manager.load_json(module_tree_path) or {}
        first_module_tree = file_manager.load_json(first_module_tree_path) or {}

        if not module_tree:
            # Small repo that fits in a single context — no parallelism needed
            logger.info("Processing whole repo because repo can fit in the context window")
            repo_name = os.path.basename(os.path.normpath(self.config.repo_path))
            final_module_tree, _ = await self.agent_orchestrator.process_module(
                repo_name, components, leaf_nodes, [], working_dir
            )

            file_manager.save_json(final_module_tree, module_tree_path)

            repo_overview_path = os.path.join(working_dir, module_doc_filename([repo_name]))
            if os.path.exists(repo_overview_path):
                os.rename(repo_overview_path, os.path.join(working_dir, OVERVIEW_FILENAME))

            return working_dir

        dedup_docs_directory(working_dir)

        freeze_doc_filenames(module_tree)
        freeze_doc_filenames(first_module_tree)
        file_manager.save_json(module_tree, module_tree_path)
        file_manager.save_json(first_module_tree, first_module_tree_path)

        self._gen_state = self._gen_state or GenerationState.load(state_path)
        self._state_mgr = self._state_mgr or GenerationStateManager(self._gen_state, state_path)
        await self._state_mgr.update_metadata(self.commit_id or "", config_fingerprint(self.config))
        planned_tasks = build_generation_tasks(
            module_tree, self.config, existing_state=self._gen_state
        )
        if not self._gen_state.tasks:
            try:
                await self._state_mgr.bulk_add_tasks(planned_tasks)
            except ValueError as exc:
                logger.warning(
                    "Skipping colliding planned tasks during initial ledger load: %s", exc
                )
                for task in planned_tasks:
                    try:
                        await self._state_mgr.add_task(task)
                    except ValueError as item_exc:
                        logger.warning(
                            "Skipped task %s due to output_file collision: %s",
                            task.doc_id,
                            item_exc,
                        )
        else:
            existing_ids = set(self._gen_state.tasks)
            missing_tasks = [task for task in planned_tasks if task.doc_id not in existing_ids]
            if missing_tasks:
                try:
                    await self._state_mgr.bulk_add_tasks(missing_tasks)
                except ValueError as exc:
                    logger.warning("Skipping colliding missing tasks: %s", exc)
                    for task in missing_tasks:
                        try:
                            await self._state_mgr.add_task(task)
                        except ValueError as item_exc:
                            logger.warning(
                                "Skipped task %s due to output_file collision: %s",
                                task.doc_id,
                                item_exc,
                            )
            await self._state_mgr.mark_stale(
                {task.doc_id: task.input_hash for task in planned_tasks}
            )
        await self._state_mgr.promote_ready()

        # ── Dynamic task-queue concurrent path ────────────────────────────
        tree_manager = ModuleTreeManager(module_tree, module_tree_path)
        max_concurrent = self.config.max_concurrent
        max_retries = self.config.max_retries

        graph_tree = await tree_manager.get_snapshot()
        logger.info(
            f"📊 Running queue on {len(graph_tree)} top-level modules (concurrency={max_concurrent})"
        )
        await self._run_module_queue(
            graph_tree,
            components,
            working_dir,
            tree_manager,
            desc="Generating docs",
            include_root=False,
        )

        # ── Fill any modules whose .md was not written ────────────────────
        await self._fill_missing_module_docs(working_dir, components, tree_manager, max_retries)

        # ── Generate repo-level overview after all modules are complete ───
        logger.info("📚 Generating repository overview")
        await self.generate_parent_module_docs([], working_dir, tree_manager)

        return working_dir

    async def _run_module_queue(
        self,
        graph_tree: Dict[str, Any],
        components: Dict[str, Any],
        working_dir: str,
        tree_manager,
        desc: str = "Generating docs",
        include_root: bool = True,
    ) -> None:
        async def _generate_root_overview() -> None:
            await self.generate_parent_module_docs([], working_dir, tree_manager)

        await run_module_queue_impl(
            config=self.config,
            graph_tree=graph_tree,
            components=components,
            working_dir=working_dir,
            tree_manager=tree_manager,
            process_module=self.agent_orchestrator.process_module,
            generate_root_overview=(_generate_root_overview if include_root else None),
            desc=desc,
            include_root=include_root,
            gen_state=self._gen_state,
            state_mgr=self._state_mgr,
            progress_factory=tqdm,
        )

    async def _fill_missing_module_docs(
        self,
        working_dir: str,
        components: Dict[str, Any],
        tree_manager,
        max_retries: int,
    ) -> None:
        await fill_missing_module_docs_impl(
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
            gen_state=self._gen_state,
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
                gen_state=self._gen_state,
            ),
            module_path,
        )

    async def generate_parent_module_docs(
        self,
        module_path: List[str],
        working_dir: str,
        tree_manager: Optional[ModuleTreeManager] = None,
    ) -> Dict[str, Any]:
        return await generate_parent_module_docs_impl(
            OverviewContext(
                config=self.config,
                module_tree={},
                working_dir=working_dir,
                gen_state=self._gen_state,
                state_mgr=self._state_mgr,
                tree_manager=tree_manager,
                call_llm=call_llm,
                usage_stats=self.usage_stats,
            ),
            module_path,
        )

    async def run(self) -> None:
        """Run the complete documentation generation process using dynamic programming."""
        try:
            # Build dependency graph
            components, leaf_nodes = self.graph_builder.build_dependency_graph()

            logger.debug(f"Found {len(leaf_nodes)} leaf nodes")

            # Build v3 index (symbol table, import graph, component cards)
            try:
                from codewiki.src.be.index.index_builder import IndexBuilder

                index_builder = IndexBuilder(
                    repo_path=self.config.repo_path,
                    include_patterns=self.config.include_patterns,
                    exclude_patterns=self.config.exclude_patterns,
                    output_dir=self.config.docs_dir,
                )
                self.index_products = index_builder.build()
            except Exception:
                logger.warning(
                    "Index build failed; continuing without index products", exc_info=True
                )
                self.index_products = None

            # Cluster modules
            working_dir = os.path.abspath(self.config.docs_dir)
            file_manager.ensure_directory(working_dir)
            first_module_tree_path = os.path.join(working_dir, FIRST_MODULE_TREE_FILENAME)
            module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)
            state_path = internal_file_path(working_dir, GENERATION_STATE_FILENAME)
            existing_state = GenerationState.load(state_path)
            current_config_fp = config_fingerprint(self.config)

            cached_tree = (
                file_manager.load_json(first_module_tree_path)
                if os.path.exists(first_module_tree_path)
                else None
            )

            need_recluster = True
            if cached_tree:
                if (
                    existing_state.repo_commit == (self.commit_id or "")
                    and existing_state.config_fingerprint == current_config_fp
                ):
                    need_recluster = False
                    logger.debug("Module tree cache hit (same commit)")
                else:
                    logger.info("Module tree cache invalidated (commit/config changed)")

            if not need_recluster:
                assert cached_tree is not None
                module_tree = heal_module_tree_components(cached_tree, components)
                freeze_doc_filenames(module_tree)
                file_manager.save_json(module_tree, first_module_tree_path)
                if not os.path.exists(module_tree_path):
                    file_manager.save_json(module_tree, module_tree_path)
            else:
                logger.debug(f"Clustering modules (no valid cache at {first_module_tree_path})")
                module_tree = cluster_modules(
                    leaf_nodes,
                    components,
                    self.config,
                    index_products=self.index_products,
                    usage_stats=self.usage_stats,
                )
                if module_tree:
                    freeze_doc_filenames(module_tree)
                    file_manager.save_json(module_tree, first_module_tree_path)
                    file_manager.save_json(module_tree, module_tree_path)

            logger.debug(f"Grouped components into {len(module_tree)} modules")

            # v2: build global assets and inject into agent orchestrator
            try:
                from codewiki.src.be.generation.glossary import build_glossary, build_link_map

                glossary = build_glossary(self.index_products) if self.index_products else {}
                link_map = build_link_map(module_tree) if module_tree else {}
                self.agent_orchestrator.set_generation_context(
                    index_products=self.index_products,
                    global_assets={"glossary": glossary, "link_map": link_map},
                )
                logger.info(
                    f"Generation v2 context set: {len(glossary)} glossary terms, {len(link_map)} link map entries"
                )
            except Exception:
                logger.warning(
                    "Failed to set generation v2 context; continuing without", exc_info=True
                )

            existing_state.repo_commit = self.commit_id or ""
            existing_state.config_fingerprint = current_config_fp
            self._gen_state = existing_state

            # Generate module documentation using dynamic programming approach
            # This processes leaf modules first, then parent modules
            working_dir = await self.generate_module_documentation(components, leaf_nodes)

            # Generate guide documents (Get Started, Beginner's Guide, etc.)
            logger.info("📖 Starting guide document generation")
            guide_gen = GuideGenerator(
                config=self.config,
                components=components,
                module_tree=module_tree,
                working_dir=working_dir,
                usage_stats=self.usage_stats,
            )
            await guide_gen.run()

            # Phase: post-processing fix (markdown + math + mermaid)
            from codewiki.src.be.docs_fixer import fix_docs

            fix_docs(working_dir, self.config, usage_stats=self.usage_stats)

            # Create documentation metadata after all usage-producing steps.
            self.create_documentation_metadata(
                working_dir,
                components,
                len(leaf_nodes),
                usage_stats=self.usage_stats,
            )

            logger.debug(
                f"Documentation generation completed successfully using dynamic programming!"
            )
            logger.debug(f"Processing order: leaf modules → parent modules → repository overview")
            logger.debug(f"Documentation saved to: {working_dir}")

        except Exception as e:
            logger.error(f"Documentation generation failed: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise
