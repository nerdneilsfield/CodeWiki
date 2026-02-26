import asyncio
import logging
import os
import json
from collections import defaultdict
from typing import Dict, List, Any, Optional
from copy import deepcopy
import traceback

# Configure logging and monitoring
logger = logging.getLogger(__name__)

# Local imports
from codewiki.src.be.dependency_analyzer import DependencyGraphBuilder
from codewiki.src.be.llm_services import call_llm
from codewiki.src.be.prompt_template import (
    format_overview_prompt,
)
from codewiki.src.be.cluster_modules import cluster_modules, heal_module_tree_components
from codewiki.src.config import (
    Config,
    FIRST_MODULE_TREE_FILENAME,
    MODULE_TREE_FILENAME,
    OVERVIEW_FILENAME
)
from codewiki.src.utils import file_manager
from codewiki.src.be.agent_orchestrator import AgentOrchestrator
from codewiki.src.be.module_tree_manager import ModuleTreeManager


class DocumentationGenerator:
    """Main documentation generation orchestrator."""

    def __init__(self, config: Config, commit_id: str = None):
        self.config = config
        self.commit_id = commit_id
        self.graph_builder = DependencyGraphBuilder(config)
        self.agent_orchestrator = AgentOrchestrator(config)

    @staticmethod
    def _detect_repo_url(repo_path: str) -> Optional[str]:
        """Try to detect the GitHub/remote URL from git config."""
        try:
            import subprocess
            result = subprocess.run(
                ['git', 'remote', 'get-url', 'origin'],
                cwd=repo_path, capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                url = result.stdout.strip()
                if url.startswith('git@github.com:'):
                    url = url.replace('git@github.com:', 'https://github.com/')
                url = url.rstrip('/').removesuffix('.git')
                return url
        except Exception:
            pass
        return None

    def create_documentation_metadata(self, working_dir: str, components: Dict[str, Any], num_leaf_nodes: int):
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
                "commit_id": self.commit_id
            },
            "statistics": {
                "total_components": len(components),
                "leaf_nodes": num_leaf_nodes,
                "max_depth": self.config.max_depth
            },
            "files_generated": [
                "overview.md",
                "module_tree.json",
                "first_module_tree.json"
            ]
        }

        # Add generated markdown files to the metadata
        try:
            for file_path in os.listdir(working_dir):
                if file_path.endswith('.md') and file_path not in metadata["files_generated"]:
                    metadata["files_generated"].append(file_path)
        except Exception as e:
            logger.warning(f"Could not list generated files: {e}")

        metadata_path = os.path.join(working_dir, "metadata.json")
        file_manager.save_json(metadata, metadata_path)

    # ── Level-based scheduling ────────────────────────────────────────────

    def get_processing_levels(
        self, module_tree: Dict[str, Any], parent_path: Optional[List[str]] = None
    ) -> List[List[tuple]]:
        """Group modules into levels for parallel processing.

        Returns a list of levels, where level 0 contains the deepest leaf
        modules and the highest level contains top-level parent modules.
        Modules within the same level are independent and can be processed
        concurrently.
        """
        if parent_path is None:
            parent_path = []

        # node_key -> (level, module_path, module_name, module_info)
        node_levels: Dict[str, tuple] = {}

        def assign_levels(tree: Dict[str, Any], path: List[str]):
            for name, info in tree.items():
                current_path = path + [name]
                key = "/".join(current_path)
                children = info.get("children") or {}
                if not children or not isinstance(children, dict):
                    # Leaf node — level 0
                    node_levels[key] = (0, current_path, name, info)
                else:
                    # Recurse into children first
                    assign_levels(children, current_path)
                    # Parent level = max child level + 1
                    child_max = max(
                        node_levels["/".join(current_path + [cn])][0]
                        for cn in children
                        if "/".join(current_path + [cn]) in node_levels
                    )
                    node_levels[key] = (child_max + 1, current_path, name, info)

        assign_levels(module_tree, parent_path)

        by_level: Dict[int, List[tuple]] = defaultdict(list)
        for _key, (level, path, name, info) in node_levels.items():
            by_level[level].append((path, name, info))

        return [by_level[i] for i in sorted(by_level.keys())]

    # ── Legacy helper (kept for backward compat) ─────────────────────────

    def get_processing_order(self, module_tree: Dict[str, Any], parent_path: List[str] = []) -> List[tuple[List[str], str]]:
        """Get the processing order using topological sort (leaf modules first)."""
        processing_order = []

        def collect_modules(tree: Dict[str, Any], path: List[str]):
            for module_name, module_info in tree.items():
                current_path = path + [module_name]

                # If this module has children, process them first
                if module_info.get("children") and isinstance(module_info["children"], dict) and module_info["children"]:
                    collect_modules(module_info["children"], current_path)
                    # Add this parent module after its children
                    processing_order.append((current_path, module_name))
                else:
                    # This is a leaf module, add it immediately
                    processing_order.append((current_path, module_name))

        collect_modules(module_tree, parent_path)
        return processing_order

    def is_leaf_module(self, module_info: Dict[str, Any]) -> bool:
        """Check if a module is a leaf module (has no children or empty children)."""
        children = module_info.get("children", {})
        return not children or (isinstance(children, dict) and len(children) == 0)

    def build_overview_structure(self, module_tree: Dict[str, Any], module_path: List[str],
                                 working_dir: str) -> Dict[str, Any]:
        """Build structure for overview generation with 1-depth children docs and target indicator."""

        processed_module_tree = deepcopy(module_tree)
        module_info = processed_module_tree
        for path_part in module_path:
            module_info = module_info[path_part]
            if path_part != module_path[-1]:
                module_info = module_info.get("children", {})
            else:
                module_info["is_target_for_overview_generation"] = True

        if "children" in module_info:
            module_info = module_info["children"]

        for child_name, child_info in module_info.items():
            if os.path.exists(os.path.join(working_dir, f"{child_name}.md")):
                child_info["docs"] = file_manager.load_text(os.path.join(working_dir, f"{child_name}.md"))
            else:
                logger.warning(f"Module docs not found at {os.path.join(working_dir, f"{child_name}.md")}")
                child_info["docs"] = ""

        return processed_module_tree

    # ── Main entry point ─────────────────────────────────────────────────

    @staticmethod
    def _module_doc_exists(working_dir: str, module_name: str) -> bool:
        """Return True if a non-trivial .md file already exists for *module_name*."""
        docs_path = os.path.join(working_dir, f"{module_name}.md")
        return os.path.exists(docs_path) and os.path.getsize(docs_path) > 100

    async def generate_module_documentation(self, components: Dict[str, Any], leaf_nodes: List[str]) -> str:
        """Generate documentation for all modules using level-based concurrency."""
        # Prepare output directory
        working_dir = os.path.abspath(self.config.docs_dir)
        file_manager.ensure_directory(working_dir)

        module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)
        first_module_tree_path = os.path.join(working_dir, FIRST_MODULE_TREE_FILENAME)
        module_tree = file_manager.load_json(module_tree_path) or {}
        first_module_tree = file_manager.load_json(first_module_tree_path) or {}

        if not module_tree:
            # Small repo that fits in a single context — no parallelism needed
            logger.info("Processing whole repo because repo can fit in the context window")
            repo_name = os.path.basename(os.path.normpath(self.config.repo_path))
            final_module_tree = await self.agent_orchestrator.process_module(
                repo_name, components, leaf_nodes, [], working_dir
            )

            file_manager.save_json(final_module_tree, module_tree_path)

            repo_overview_path = os.path.join(working_dir, f"{repo_name}.md")
            if os.path.exists(repo_overview_path):
                os.rename(repo_overview_path, os.path.join(working_dir, OVERVIEW_FILENAME))

            return working_dir

        # ── Concurrent path ──────────────────────────────────────────────

        # Create lock-protected tree manager
        tree_manager = ModuleTreeManager(module_tree, module_tree_path)

        # Compute levels (level 0 = deepest leaves, higher = parents)
        levels = self.get_processing_levels(first_module_tree)
        max_concurrent = getattr(self.config, 'max_concurrent', 3)
        max_retries = getattr(self.config, 'max_retries', 2)
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _process_one(module_path, module_name, module_info):
            """Process a single module; returns True on success, False on failure."""
            module_key = "/".join(module_path)
            try:
                if self.is_leaf_module(module_info):
                    await self.agent_orchestrator.process_module(
                        module_name, components,
                        module_info.get("components", []),
                        module_path, working_dir, tree_manager
                    )
                else:
                    await self.generate_parent_module_docs(
                        module_path, working_dir, tree_manager
                    )
                return True
            except Exception as e:
                logger.error(f"Failed to process {module_key}: {e}")
                logger.error(traceback.format_exc())
                return False

        async def _process(module_path, module_name, module_info):
            async with semaphore:
                return await _process_one(module_path, module_name, module_info)

        for level_idx, level_modules in enumerate(levels):
            level_names = [name for _, name, _ in level_modules]
            logger.info(
                f"📊 Level {level_idx}: {len(level_modules)} module(s) "
                f"(concurrency={max_concurrent}) — {', '.join(level_names[:5])}"
                f"{'...' if len(level_names) > 5 else ''}"
            )

            tasks = [_process(mp, mn, mi) for mp, mn, mi in level_modules]
            await asyncio.gather(*tasks)

            # ── Retry failed modules sequentially ────────────────────────
            for retry in range(max_retries):
                failed = [
                    (mp, mn, mi)
                    for mp, mn, mi in level_modules
                    if not self._module_doc_exists(working_dir, mn)
                ]
                if not failed:
                    break
                logger.warning(
                    f"↩ Retry {retry + 1}/{max_retries} for {len(failed)} failed module(s): "
                    f"{', '.join(mn for _, mn, _ in failed)}"
                )
                for mp, mn, mi in failed:
                    await _process_one(mp, mn, mi)

        # ── Fill any sub-modules whose .md was not written ───────────────
        await self._fill_missing_module_docs(working_dir, components, tree_manager, max_retries)

        # ── Root overview (after all modules) ────────────────────────────
        logger.info("📚 Generating repository overview")
        await self.generate_parent_module_docs([], working_dir, tree_manager)

        return working_dir

    async def _fill_missing_module_docs(
        self,
        working_dir: str,
        components: Dict[str, Any],
        tree_manager,
        max_retries: int,
    ) -> None:
        """Walk the full module_tree and retry every module whose .md file is absent.

        Sub-modules are added to module_tree.json dynamically by the recursive
        agents, but if a sub-agent crashes the parent agent may still succeed and
        write its own .md.  This pass finds the gaps and fills them.
        """
        def collect_missing(tree: Dict[str, Any], path: List[str]) -> List[tuple]:
            result = []
            for name, info in tree.items():
                current_path = path + [name]
                if not self._module_doc_exists(working_dir, name):
                    result.append((current_path, name, info))
                children = info.get("children") or {}
                if children:
                    result.extend(collect_missing(children, current_path))
            return result

        for attempt in range(max_retries):
            module_tree = await tree_manager.get_snapshot()
            missing = collect_missing(module_tree, [])
            if not missing:
                return
            logger.warning(
                f"↩ Fill pass {attempt + 1}/{max_retries}: "
                f"{len(missing)} module(s) without docs — "
                f"{', '.join(mn for _, mn, _ in missing[:5])}"
                f"{'...' if len(missing) > 5 else ''}"
            )
            for module_path, module_name, module_info in missing:
                try:
                    await self.agent_orchestrator.process_module(
                        module_name, components,
                        module_info.get("components", []),
                        module_path, working_dir, tree_manager
                    )
                except Exception as e:
                    logger.error(f"Fill retry failed for {module_name}: {e}")
                    logger.error(traceback.format_exc())

    # ── Parent / overview generation ─────────────────────────────────────

    async def generate_parent_module_docs(self, module_path: List[str],
                                        working_dir: str,
                                        tree_manager: Optional[ModuleTreeManager] = None) -> Dict[str, Any]:
        """Generate documentation for a parent module based on its children's documentation."""
        module_name = module_path[-1] if len(module_path) >= 1 else os.path.basename(os.path.normpath(self.config.repo_path))

        logger.info(f"Generating parent documentation for: {module_name}")

        # Get module tree
        if tree_manager:
            module_tree = await tree_manager.get_snapshot()
        else:
            module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)
            module_tree = file_manager.load_json(module_tree_path)

        # Determine output path and skip if already exists
        if len(module_path) == 0:
            output_path = os.path.join(working_dir, OVERVIEW_FILENAME)
        else:
            output_path = os.path.join(working_dir, f"{module_name}.md")
        if os.path.exists(output_path) and os.path.getsize(output_path) > 100:
            logger.info(f"✓ Docs already exists at {output_path}")
            return module_tree

        # Create repo structure with 1-depth children docs and target indicator
        repo_structure = self.build_overview_structure(module_tree, module_path, working_dir)

        prompt = format_overview_prompt(
            name=module_name,
            repo_structure=json.dumps(repo_structure, indent=4),
            is_repo=(len(module_path) == 0),
            output_language=self.config.output_language,
        )

        try:
            # Run LLM call in a thread so it doesn't block the event loop
            parent_docs = await asyncio.to_thread(call_llm, prompt, self.config)

            # Parse and save parent documentation
            parent_content = parent_docs.split("<OVERVIEW>")[1].split("</OVERVIEW>")[0].strip()
            file_manager.save_text(parent_content, output_path)

            logger.debug(f"Successfully generated parent documentation for: {module_name}")
            return module_tree

        except Exception as e:
            logger.error(f"Error generating parent documentation for {module_name}: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise

    async def run(self) -> None:
        """Run the complete documentation generation process using dynamic programming."""
        try:
            # Build dependency graph
            components, leaf_nodes = self.graph_builder.build_dependency_graph()

            logger.debug(f"Found {len(leaf_nodes)} leaf nodes")

            # Cluster modules
            working_dir = os.path.abspath(self.config.docs_dir)
            file_manager.ensure_directory(working_dir)
            first_module_tree_path = os.path.join(working_dir, FIRST_MODULE_TREE_FILENAME)
            module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)

            # Load cached module tree; re-cluster if missing or empty (stale from a small-repo run)
            cached_tree = file_manager.load_json(first_module_tree_path) if os.path.exists(first_module_tree_path) else None
            if cached_tree:
                logger.debug(f"Module tree found at {first_module_tree_path}")
                module_tree = heal_module_tree_components(cached_tree, components)
                file_manager.save_json(module_tree, first_module_tree_path)
                # Do NOT overwrite module_tree.json here — it may already contain
                # sub-module entries added dynamically during a previous run.
                # Only initialise it when it doesn't exist yet.
                if not os.path.exists(module_tree_path):
                    file_manager.save_json(module_tree, module_tree_path)
            else:
                logger.debug(f"Module tree not found or empty at {first_module_tree_path}, clustering modules")
                module_tree = cluster_modules(leaf_nodes, components, self.config)
                if module_tree:
                    file_manager.save_json(module_tree, first_module_tree_path)
                    file_manager.save_json(module_tree, module_tree_path)

            logger.debug(f"Grouped components into {len(module_tree)} modules")

            # Generate module documentation using dynamic programming approach
            # This processes leaf modules first, then parent modules
            working_dir = await self.generate_module_documentation(components, leaf_nodes)

            # Create documentation metadata
            self.create_documentation_metadata(working_dir, components, len(leaf_nodes))

            logger.debug(f"Documentation generation completed successfully using dynamic programming!")
            logger.debug(f"Processing order: leaf modules → parent modules → repository overview")
            logger.debug(f"Documentation saved to: {working_dir}")

        except Exception as e:
            logger.error(f"Documentation generation failed: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise
