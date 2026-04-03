"""
CLI adapter for documentation generator backend.

This adapter wraps the existing backend documentation_generator.py
and provides CLI-specific functionality like progress reporting.
"""

from pathlib import Path
import time
import asyncio
import os
import logging


from codewiki.cli.utils.progress import ProgressTracker
from codewiki.cli.models.job import DocumentationJob, LLMConfig
from codewiki.cli.utils.errors import APIError

# Import backend modules
from codewiki.src.be.documentation_generator import DocumentationGenerator
from codewiki.src.codewiki_config import CodeWikiConfig

logger = logging.getLogger(__name__)


class CLIDocumentationGenerator:
    """
    CLI adapter for documentation generation with progress reporting.

    This class wraps the backend documentation generator and adds
    CLI-specific features like progress tracking and error handling.
    """

    def __init__(
        self,
        repo_path: Path,
        output_dir: Path,
        config: CodeWikiConfig,
        verbose: bool = False,
        generate_html: bool = False,
        generate_static: bool = False,
        no_cache: bool = False,
        hide_repo_links: bool = False,
    ):
        """
        Initialize the CLI documentation generator.

        Args:
            repo_path: Repository path
            output_dir: Output directory
            config: Runtime configuration
            verbose: Enable verbose output
            generate_html: Whether to generate HTML viewer
            no_cache: Clear existing docs before generation
        """
        self.repo_path = repo_path
        self.output_dir = output_dir
        self.config = config
        self.verbose = verbose
        self.generate_html = generate_html
        self.generate_static = generate_static
        self.no_cache = no_cache
        self.hide_repo_links = hide_repo_links
        self.progress_tracker = ProgressTracker(total_stages=5, verbose=verbose)
        self.job = DocumentationJob()

        # Setup job metadata
        self.job.repository_path = str(repo_path)
        self.job.repository_name = repo_path.name
        self.job.output_directory = str(output_dir)
        self.job.llm_config = LLMConfig(
            main_model=config.main_model,
            cluster_model=config.cluster_model,
            base_url=config.llm_base_url or "multi-provider",
        )

        # Configure backend logging
        self._configure_backend_logging()

    def _configure_backend_logging(self):
        """Configure backend logger for CLI use with colored output."""
        from codewiki.src.logging_setup import configure_cli_logging

        configure_cli_logging(verbose=self.verbose)

    def generate(self) -> DocumentationJob:
        """
        Generate documentation with progress tracking.

        Returns:
            Completed DocumentationJob

        Raises:
            APIError: If LLM API call fails
        """
        self.job.start()
        start_time = time.time()

        try:
            # Run backend documentation generation
            asyncio.run(self._run_backend_generation(self.config))

            # Stage 4: HTML Generation (optional — pick one or both)
            if self.generate_html:
                self._run_html_generation()
            if self.generate_static:
                self._run_static_generation()

            # Stage 5: Finalization (metadata already created by backend)
            self._finalize_job()

            # Complete job
            generation_time = time.time() - start_time
            self.job.complete()

            return self.job

        except APIError as e:
            self.job.fail(str(e))
            raise
        except Exception as e:
            self.job.fail(str(e))
            raise

    async def _run_backend_generation(self, backend_config: CodeWikiConfig):
        """Run the backend documentation generation with progress tracking."""

        # --no-cache: wipe generated markdown files and internal state so every
        # module is regenerated from scratch (e.g. to switch main_model mid-run).
        if self.no_cache:
            working_dir = str(self.output_dir.absolute())
            import glob as _glob

            removed = 0
            for md_file in _glob.glob(os.path.join(working_dir, "*.md")):
                os.remove(md_file)
                removed += 1
            if removed:
                logging.getLogger(__name__).info(
                    f"--no-cache: removed {removed} existing .md file(s) from {working_dir}"
                )
            internal_dir = os.path.join(working_dir, ".codewiki")
            if os.path.isdir(internal_dir):
                for fname in os.listdir(internal_dir):
                    try:
                        os.remove(os.path.join(internal_dir, fname))
                    except OSError:
                        pass

        self.progress_tracker.start_stage(1, "Documentation Generation")
        if self.verbose:
            self.progress_tracker.update_stage(0.1, "Running documentation pipeline...")

        doc_generator = DocumentationGenerator(backend_config)

        try:
            result = await doc_generator.run()
        except Exception as e:
            raise APIError(f"Documentation generation failed: {e}")

        if result.status == "failed":
            raise APIError("; ".join(result.warnings) or "Documentation generation failed")

        if result.status == "degraded":
            logger.warning("Generation completed with issues:")
            for warning in result.warnings:
                logger.warning("  - %s", warning)
            for failure in result.module_summary.failed:
                logger.warning("  - Module %s failed: %s", failure.doc_id, failure.error)

        from codewiki.src.utils import file_manager
        from codewiki.src.config import MODULE_TREE_FILENAME

        working_dir = str(self.output_dir.absolute())
        metadata = result.metadata or {}
        stats = metadata.get("statistics", {})
        token_usage = stats.get("token_usage", {})

        self.job.statistics.total_files_analyzed = int(stats.get("total_components", 0) or 0)
        self.job.statistics.leaf_nodes = int(stats.get("leaf_nodes", 0) or 0)
        self.job.statistics.total_tokens_used = int(
            (token_usage.get("total_input", 0) or 0) + (token_usage.get("total_output", 0) or 0)
        )

        module_tree = file_manager.load_json(os.path.join(working_dir, MODULE_TREE_FILENAME)) or {}
        self.job.module_count = len(module_tree)

        for file_path in os.listdir(working_dir):
            if file_path.endswith(".md") or file_path.endswith(".json"):
                if file_path not in self.job.files_generated:
                    self.job.files_generated.append(file_path)

        if self.verbose:
            self.progress_tracker.update_stage(
                1.0, f"Pipeline finished with status {result.status}"
            )

        self.progress_tracker.complete_stage()

    def _run_html_generation(self):
        """Run HTML generation stage."""
        self.progress_tracker.start_stage(4, "HTML Generation")

        from codewiki.cli.html_generator import HTMLGenerator

        # Generate HTML
        html_generator = HTMLGenerator()

        if self.verbose:
            self.progress_tracker.update_stage(0.3, "Loading module tree and metadata...")

        repo_info = html_generator.detect_repository_info(self.repo_path)

        # Generate HTML with auto-loading of module_tree and metadata from docs_dir
        output_path = self.output_dir / "index.html"
        html_generator.generate(
            output_path=output_path,
            title=repo_info["name"] or self.repo_path.name,
            repository_url=repo_info["url"],
            github_pages_url=repo_info["github_pages_url"],
            docs_dir=self.output_dir,  # Auto-load module_tree and metadata from here
            hide_repo_links=self.hide_repo_links,
        )

        self.job.files_generated.append("index.html")

        if self.verbose:
            self.progress_tracker.update_stage(1.0, "Generated index.html")

        self.progress_tracker.complete_stage()

    def _run_static_generation(self):
        """Pre-render every .md file to a standalone .html page."""
        self.progress_tracker.start_stage(4, "Static HTML Generation")

        from codewiki.cli.static_generator import StaticHTMLGenerator

        if self.verbose:
            self.progress_tracker.update_stage(0.1, "Pre-rendering markdown files...")

        generator = StaticHTMLGenerator()
        written = generator.generate(self.output_dir, hide_repo_links=self.hide_repo_links)

        for fname in written:
            if fname not in self.job.files_generated:
                self.job.files_generated.append(fname)

        if self.verbose:
            self.progress_tracker.update_stage(1.0, f"Generated {len(written)} HTML files")

        self.progress_tracker.complete_stage()

    def _finalize_job(self):
        """Finalize the job (metadata already created by backend)."""
        # Just verify metadata exists
        metadata_path = self.output_dir / "metadata.json"
        if not metadata_path.exists():
            # Create our own if backend didn't
            with open(metadata_path, "w") as f:
                f.write(self.job.to_json())
