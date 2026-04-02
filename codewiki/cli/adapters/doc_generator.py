"""
CLI adapter for documentation generator backend.

This adapter wraps the existing backend documentation_generator.py
and provides CLI-specific functionality like progress reporting.
"""

from pathlib import Path
from typing import Dict, Any
import time
import asyncio
import os
import logging
import sys


from codewiki.cli.utils.progress import ProgressTracker
from codewiki.cli.models.job import DocumentationJob, LLMConfig
from codewiki.cli.utils.errors import APIError

# Import backend modules
from codewiki.src.be.documentation_generator import DocumentationGenerator
from codewiki.src.config import Config as BackendConfig, set_cli_context
from codewiki.src.config_loader import RuntimeOverrides


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
        config: Dict[str, Any],
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
            config: LLM configuration
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
        app_config = config.get('app_config')
        if app_config is not None:
            self.job.llm_config = LLMConfig(
                main_model=app_config.generation.main_model,
                cluster_model=app_config.generation.cluster_model,
                base_url='multi-provider'
            )
        else:
            self.job.llm_config = LLMConfig(
                main_model=config.get('main_model', ''),
                cluster_model=config.get('cluster_model', ''),
                base_url=config.get('base_url', '')
            )
        
        # Configure backend logging
        self._configure_backend_logging()
    

    def _build_backend_config(self) -> BackendConfig:
        """Build backend runtime config from either legacy dict config or new AppConfig path."""
        app_config = self.config.get("app_config")
        if app_config is not None:
            overrides = self.config.get("runtime_overrides") or RuntimeOverrides()
            if overrides.output_dir is None:
                overrides.output_dir = str(self.output_dir)
            return app_config.to_runtime_config(
                repo_path=str(self.repo_path),
                overrides=overrides,
            )

        return BackendConfig.from_cli(
            repo_path=str(self.repo_path),
            output_dir=str(self.output_dir),
            llm_base_url=self.config.get('base_url'),
            llm_api_key=self.config.get('api_key'),
            main_model=self.config.get('main_model'),
            cluster_model=self.config.get('cluster_model'),
            fallback_model=self.config.get('fallback_model'),
            long_context_model=self.config.get('long_context_model') or None,
            long_context_threshold=self.config.get('long_context_threshold', 200000),
            max_tokens=self.config.get('max_tokens', 32768),
            max_token_per_module=self.config.get('max_token_per_module', 36369),
            max_token_per_leaf_module=self.config.get('max_token_per_leaf_module', 16000),
            max_depth=self.config.get('max_depth', 2),
            max_concurrent=self.config.get('max_concurrent', 3),
            max_retries=self.config.get('max_retries', 2),
            output_language=self.config.get('output_language', 'en'),
            agent_instructions=self.config.get('agent_instructions')
        )

    def _configure_backend_logging(self):
        """Configure backend logger for CLI use with colored output."""
        from codewiki.src.be.dependency_analyzer.utils.logging_config import ColoredFormatter

        # Get project-wide logger (covers both backend and CLI modules)
        backend_logger = logging.getLogger('codewiki')

        # Remove existing handlers to avoid duplicates
        backend_logger.handlers.clear()

        if self.verbose:
            # In verbose mode, show DEBUG and above for our own code
            backend_logger.setLevel(logging.DEBUG)

            # Create console handler with formatting
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(logging.DEBUG)

            # Use colored formatter for better readability
            colored_formatter = ColoredFormatter()
            console_handler.setFormatter(colored_formatter)

            # Add handler to logger
            backend_logger.addHandler(console_handler)
        else:
            # In non-verbose mode, suppress backend logs (use WARNING level to hide INFO/DEBUG)
            backend_logger.setLevel(logging.WARNING)

            # Create console handler for warnings and errors only
            console_handler = logging.StreamHandler(sys.stderr)
            console_handler.setLevel(logging.WARNING)

            # Use colored formatter even for warnings/errors
            colored_formatter = ColoredFormatter()
            console_handler.setFormatter(colored_formatter)

            backend_logger.addHandler(console_handler)

        # Prevent propagation to root logger to avoid duplicate messages
        backend_logger.propagate = False

        # Suppress noisy third-party loggers regardless of mode
        for lib_logger_name in ('httpx', 'openai', 'urllib3', 'httpcore', 'pydantic_ai'):
            logging.getLogger(lib_logger_name).setLevel(logging.WARNING)
    
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
            # Set CLI context for backend
            set_cli_context(True)
            
            # Create backend config with CLI settings
            backend_config = self._build_backend_config()
            
            # Run backend documentation generation
            asyncio.run(self._run_backend_generation(backend_config))
            
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
    
    async def _run_backend_generation(self, backend_config: BackendConfig):
        """Run the backend documentation generation with progress tracking."""

        # --no-cache: wipe generated markdown files and internal state so every
        # module is regenerated from scratch (e.g. to switch main_model mid-run).
        if self.no_cache:
            working_dir = str(self.output_dir.absolute())
            import glob as _glob
            removed = 0
            for md_file in _glob.glob(os.path.join(working_dir, '*.md')):
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

        # Stage 1: Dependency Analysis
        self.progress_tracker.start_stage(1, "Dependency Analysis")
        if self.verbose:
            self.progress_tracker.update_stage(0.2, "Initializing dependency analyzer...")
        
        # Create documentation generator
        doc_generator = DocumentationGenerator(backend_config)
        
        if self.verbose:
            self.progress_tracker.update_stage(0.5, "Parsing source files...")
        
        # Build dependency graph
        try:
            components, leaf_nodes = doc_generator.graph_builder.build_dependency_graph()
            self.job.statistics.total_files_analyzed = len(components)
            self.job.statistics.leaf_nodes = len(leaf_nodes)
            
            if self.verbose:
                self.progress_tracker.update_stage(1.0, f"Found {len(leaf_nodes)} leaf nodes")
        except Exception as e:
            raise APIError(f"Dependency analysis failed: {e}")
        
        self.progress_tracker.complete_stage()
        
        # Stage 2: Module Clustering
        self.progress_tracker.start_stage(2, "Module Clustering")
        if self.verbose:
            self.progress_tracker.update_stage(0.5, "Clustering modules with LLM...")
        
        # Import clustering function
        from codewiki.src.be.cluster_modules import cluster_modules
        from codewiki.src.utils import file_manager
        from codewiki.src.config import FIRST_MODULE_TREE_FILENAME, MODULE_TREE_FILENAME
        
        working_dir = str(self.output_dir.absolute())
        file_manager.ensure_directory(working_dir)
        first_module_tree_path = os.path.join(working_dir, FIRST_MODULE_TREE_FILENAME)
        module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)
        
        try:
            cached = file_manager.load_json(first_module_tree_path) if os.path.exists(first_module_tree_path) else None
            if cached:
                # Non-empty cache: skip LLM clustering
                if self.verbose:
                    self.progress_tracker.update_stage(0.5, "Loading cached module tree...")
                module_tree = cached
            else:
                # No cache (or empty cache from a previous failed/small run): re-cluster
                if self.verbose:
                    self.progress_tracker.update_stage(0.5, "Clustering modules with LLM...")
                module_tree = cluster_modules(leaf_nodes, components, backend_config)
                if module_tree:
                    file_manager.save_json(module_tree, first_module_tree_path)

            # Only initialise module_tree.json when it doesn't yet exist.
            # Overwriting it would destroy _completed flags written by a
            # previous run, causing every complex module to be re-processed.
            if not os.path.exists(module_tree_path):
                file_manager.save_json(module_tree, module_tree_path)
            self.job.module_count = len(module_tree)

            if self.verbose:
                if module_tree:
                    self.progress_tracker.update_stage(1.0, f"Created {len(module_tree)} top-level modules")
                else:
                    self.progress_tracker.update_stage(1.0, "Repository fits in a single context window — no clustering needed")
        except Exception as e:
            raise APIError(f"Module clustering failed: {e}")
        
        self.progress_tracker.complete_stage()
        
        # Stage 3: Documentation Generation
        self.progress_tracker.start_stage(3, "Documentation Generation")
        if self.verbose:
            self.progress_tracker.update_stage(0.1, "Generating module documentation...")
        
        try:
            # Run the actual documentation generation
            await doc_generator.generate_module_documentation(components, leaf_nodes)

            if self.verbose:
                self.progress_tracker.update_stage(0.9, "Creating repository overview...")

            # Create metadata
            doc_generator.create_documentation_metadata(working_dir, components, len(leaf_nodes))

            # Generate guide documents (Get Started, Beginner's Guide, etc.)
            # Reload module_tree from disk — the whole-repo path writes the real tree there
            from codewiki.src.be.guide_generator import GuideGenerator
            from codewiki.src.config import MODULE_TREE_FILENAME
            saved_tree = file_manager.load_json(
                os.path.join(working_dir, MODULE_TREE_FILENAME)
            ) or module_tree
            guide_gen = GuideGenerator(
                config=backend_config,
                components=components,
                module_tree=saved_tree,
                working_dir=working_dir,
            )
            await guide_gen.run()

            # Collect generated files
            for file_path in os.listdir(working_dir):
                if file_path.endswith('.md') or file_path.endswith('.json'):
                    self.job.files_generated.append(file_path)

        except Exception as e:
            raise APIError(f"Documentation generation failed: {e}")
        
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
            title=repo_info['name'],
            repository_url=repo_info['url'],
            github_pages_url=repo_info['github_pages_url'],
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
            with open(metadata_path, 'w') as f:
                f.write(self.job.to_json())
