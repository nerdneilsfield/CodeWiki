"""
Generate command for documentation generation.
"""

import sys
import traceback
from pathlib import Path
from typing import Optional, List
import click
import time
import structlog

from codewiki.cli.utils.errors import (
    ConfigurationError,
    RepositoryError,
    APIError,
    handle_error,
    EXIT_SUCCESS,
)
from codewiki.cli.utils.repo_validator import (
    validate_repository,
    check_writable_output,
    is_git_repository,
    get_git_commit_hash,
    get_git_branch,
)
from codewiki.cli.utils.logging import create_logger
from codewiki.cli.adapters.doc_generator import CLIDocumentationGenerator
from codewiki.cli.utils.instructions import display_post_generation_instructions
from codewiki.cli.utils.errors import EXIT_CONFIG_ERROR
from codewiki.src.be.llm_services import validate_llm_credentials
from codewiki.src.codewiki_config import CodeWikiConfig
from codewiki.src.logging_setup import configure_cli_logging
from codewiki.src.config_loader import (
    RuntimeOverrides,
    load_config,
)

_logger = structlog.get_logger("codewiki.cli.generate")


def log_effective_config(config) -> None:
    """Log the effective runtime configuration at INFO level."""
    if not structlog.is_configured():
        configure_cli_logging(verbose=False)
    _logger.info(
        "effective_config",
        main_model=config.main_model,
        cluster_model=config.cluster_model,
        fallback_model=config.fallback_model,
        max_tokens=config.max_tokens,
        max_concurrent=config.max_concurrent,
        output_language=config.output_language,
        providers=len(config.providers) if config.providers else 0,
    )


def _resolve_generation_config_path(config_path: str | None) -> Path:
    candidate = Path(config_path) if config_path else Path("config.toml")
    if candidate.exists():
        return candidate
    raise ConfigurationError(
        "No TOML config found.\n\n"
        "Run `codewiki config init` to create config.toml, or pass --config /path/to/config.toml."
    )


def _normalize_model_override(config: CodeWikiConfig, model_ref: str | None) -> str | None:
    if not model_ref:
        return None
    if "/" in model_ref:
        return model_ref
    if len(config.providers) == 1:
        return f"{config.providers[0].name}/{model_ref}"
    provider_names = ", ".join(provider.name for provider in config.providers)
    raise ValueError(
        f"Ambiguous model ref '{model_ref}': multiple providers configured. "
        f"Use 'provider/model' format. Available providers: {provider_names}"
    )


def _build_runtime_overrides(
    output_dir: Path,
    runtime_instructions: dict[str, object] | None,
    max_tokens: int | None,
    max_token_per_module: int | None,
    max_token_per_leaf_module: int | None,
    max_depth: int | None,
    max_concurrent: int | None,
    max_retries: int | None,
    language: str | None,
    main_model: str | None,
    cluster_model: str | None,
    long_context_model: str | None,
    long_context_threshold: int | None,
    *,
    base_config: CodeWikiConfig,
) -> RuntimeOverrides:
    persistent = dict(base_config.agent_instructions or {})
    merged_agent = {**persistent, **(runtime_instructions or {})}

    return RuntimeOverrides(
        output_dir=str(output_dir),
        max_depth=max_depth,
        max_tokens=max_tokens,
        max_token_per_module=max_token_per_module,
        max_token_per_leaf_module=max_token_per_leaf_module,
        max_concurrent=max_concurrent,
        max_retries=max_retries,
        output_language=language.strip().lower() if language else None,
        main_model=_normalize_model_override(base_config, main_model),
        cluster_model=_normalize_model_override(base_config, cluster_model),
        long_context_model=_normalize_model_override(base_config, long_context_model),
        long_context_threshold=long_context_threshold,
        agent_instructions=merged_agent if merged_agent is not None else None,
    )


def parse_patterns(patterns_str: str) -> List[str]:
    """Parse comma-separated patterns into a list."""
    if not patterns_str:
        return []
    return [p.strip() for p in patterns_str.split(",") if p.strip()]


@click.command(name="generate")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    default=None,
    help="Path to TOML config file. Defaults to ./config.toml when omitted.",
)
@click.option(
    "-C",
    "repo_dir",
    type=click.Path(exists=True, file_okay=False, path_type=str),
    default=None,
    help="Repository directory to document (default: current directory)",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default="docs",
    help="Output directory for generated documentation (default: ./docs)",
)
@click.option(
    "--create-branch",
    is_flag=True,
    help="Create a new git branch for documentation changes",
)
@click.option(
    "--github-pages",
    is_flag=True,
    help="Generate index.html for GitHub Pages deployment",
)
@click.option(
    "--static",
    "generate_static",
    is_flag=True,
    help="Pre-render all markdown files to standalone HTML pages (no runtime JS rendering)",
)
@click.option(
    "--no-cache",
    is_flag=True,
    help="Force full regeneration, ignoring cache",
)
@click.option(
    "--no-repo-links",
    "hide_repo_links",
    is_flag=True,
    help="Omit Repository and DeepWiki links from the generated HTML viewer",
)
@click.option(
    "--include",
    "-i",
    type=str,
    default=None,
    help="Comma-separated file patterns to include (e.g., '*.cs,*.py'). Overrides defaults.",
)
@click.option(
    "--exclude",
    "-e",
    type=str,
    default=None,
    help="Comma-separated patterns to exclude (e.g., '*Tests*,*Specs*,test_*')",
)
@click.option(
    "--focus",
    "-f",
    type=str,
    default=None,
    help="Comma-separated modules/paths to focus on (e.g., 'src/core,src/api')",
)
@click.option(
    "--doc-type",
    "-t",
    type=click.Choice(["api", "architecture", "user-guide", "developer"], case_sensitive=False),
    default=None,
    help="Type of documentation to generate",
)
@click.option(
    "--instructions",
    type=str,
    default=None,
    help="Custom instructions for the documentation agent",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show detailed progress and debug information",
)
@click.option(
    "--max-tokens",
    type=int,
    default=None,
    help="Maximum tokens for LLM response (overrides config)",
)
@click.option(
    "--max-token-per-module",
    type=int,
    default=None,
    help="Maximum tokens per module for clustering (overrides config)",
)
@click.option(
    "--max-token-per-leaf-module",
    type=int,
    default=None,
    help="Maximum tokens per leaf module (overrides config)",
)
@click.option(
    "--max-depth",
    type=int,
    default=None,
    help="Maximum depth for hierarchical decomposition (overrides config)",
)
@click.option(
    "--max-concurrent",
    type=int,
    default=None,
    help="Maximum number of modules to process in parallel (overrides config, default: 3)",
)
@click.option(
    "--max-retries",
    type=int,
    default=None,
    help="Number of fill-pass retries for missing module docs (overrides config, default: 2)",
)
@click.option(
    "--language",
    type=str,
    default=None,
    help="Language for generated documentation (e.g. en, zh, ja). Overrides config.",
)
@click.option(
    "--main-model",
    type=str,
    default=None,
    help="Override the main model for this generation (e.g. gpt-4o, claude-3-5-sonnet). Overrides config.",
)
@click.option(
    "--cluster-model",
    type=str,
    default=None,
    help="Override the clustering model for this generation. Overrides config.",
)
@click.option(
    "--long-context-model",
    type=str,
    default=None,
    help="Override the long-context model for this generation. Overrides config.",
)
@click.option(
    "--long-context-threshold",
    type=int,
    default=None,
    help="Override the token threshold for switching to long-context model. Overrides config.",
)
@click.pass_context
def generate_command(
    ctx,
    config_path: Optional[str],
    repo_dir: Optional[str],
    output: str,
    create_branch: bool,
    github_pages: bool,
    generate_static: bool,
    no_cache: bool,
    hide_repo_links: bool,
    include: Optional[str],
    exclude: Optional[str],
    focus: Optional[str],
    doc_type: Optional[str],
    instructions: Optional[str],
    verbose: bool,
    max_tokens: Optional[int],
    max_token_per_module: Optional[int],
    max_token_per_leaf_module: Optional[int],
    max_depth: Optional[int],
    max_concurrent: Optional[int],
    max_retries: Optional[int],
    language: Optional[str],
    main_model: Optional[str],
    cluster_model: Optional[str],
    long_context_model: Optional[str],
    long_context_threshold: Optional[int],
):
    """
    Generate comprehensive documentation for a code repository.

    Analyzes the current repository and generates documentation using LLM-powered
    analysis. Documentation is output to ./docs/ by default.

    Examples:

    \b
    # Basic generation
    $ codewiki generate

    \b
    # With git branch creation and GitHub Pages
    $ codewiki generate --create-branch --github-pages

    \b
    # Force full regeneration
    $ codewiki generate --no-cache

    \b
    # C# project: only .cs files, exclude tests
    $ codewiki generate --include "*.cs" --exclude "*Tests*,*Specs*"

    \b
    # Focus on specific modules with architecture docs
    $ codewiki generate --focus "src/core,src/api" --doc-type architecture

    \b
    # Custom instructions
    $ codewiki generate --instructions "Focus on public APIs and include usage examples"

    \b
    # Override max tokens for this generation
    $ codewiki generate --max-tokens 16384

    \b
    # Set all max token limits
    $ codewiki generate --max-tokens 32768 --max-token-per-module 40000 --max-token-per-leaf-module 20000

    \b
    # Override max depth for hierarchical decomposition
    $ codewiki generate --max-depth 3

    \b
    # Generate pre-rendered static HTML pages (no runtime JS markdown rendering)
    $ codewiki generate --static

    \b
    # Specify repository directory
    $ codewiki generate -C /path/to/repo
    $ codewiki generate -C ../other-project -o ../other-project/docs

    \b
    # Override model for this run only
    $ codewiki generate --main-model gpt-4o
    $ codewiki generate --main-model gpt-4o --long-context-model gpt-4o-128k --long-context-threshold 100000
    """
    configure_cli_logging(verbose=verbose)
    logger = create_logger(verbose=verbose, name="codewiki.cli.generate")
    start_time = time.time()

    try:
        # Pre-generation checks
        logger.step("Validating configuration...", 1, 4)

        # Validate repository
        logger.step("Validating repository...", 2, 4)

        repo_path = Path(repo_dir).resolve() if repo_dir else Path.cwd()
        repo_path, languages = validate_repository(repo_path)

        config_file = _resolve_generation_config_path(config_path)
        base_config = load_config(
            config_file,
            repo_path=str(repo_path),
            context="cli",
            resolve_secrets=True,
        )
        logger.success("Configuration valid")

        logger.success(f"Repository valid: {repo_path.name}")
        if verbose:
            logger.debug(
                f"Detected languages: {', '.join(f'{lang} ({count} files)' for lang, count in languages)}"
            )

        # Check git repository
        if not is_git_repository(repo_path):
            if create_branch:
                raise RepositoryError(
                    "Not a git repository.\n\n"
                    "The --create-branch flag requires a git repository.\n\n"
                    "To initialize a git repository: git init"
                )
            else:
                logger.warning("Not a git repository. Git features unavailable.")

        # Validate output directory
        output_dir = Path(output).expanduser().resolve()
        check_writable_output(output_dir)

        logger.success(f"Output directory: {output_dir}")

        # Check for existing documentation
        if output_dir.exists() and list(output_dir.glob("*.md")):
            if no_cache:
                logger.info(
                    "--no-cache specified: existing docs will be cleared before generation."
                )
            elif not click.confirm(
                f"\n{output_dir} already contains documentation. Overwrite?", default=True
            ):
                logger.info("Generation cancelled by user.")
                sys.exit(EXIT_SUCCESS)

        # Git branch creation (if requested)
        branch_name = None
        if create_branch:
            logger.step("Creating git branch...", 3, 4)

            from codewiki.cli.git_manager import GitManager

            git_manager = GitManager(repo_path)

            # Check clean working directory
            is_clean, status_msg = git_manager.check_clean_working_directory()
            if not is_clean:
                raise RepositoryError(
                    "Working directory has uncommitted changes.\n\n"
                    f"{status_msg}\n\n"
                    "Cannot create documentation branch with uncommitted changes.\n"
                    "Please commit or stash your changes first:\n"
                    '  git add -A && git commit -m "Your message"\n'
                    "  # or\n"
                    "  git stash"
                )

            # Create branch
            branch_name = git_manager.create_documentation_branch()
            logger.success(f"Created branch: {branch_name}")

        # Generate documentation
        logger.step("Generating documentation...", 4, 4)

        # Create runtime agent instructions from CLI options
        runtime_instructions: dict[str, object] | None = None
        if any([include, exclude, focus, doc_type, instructions]):
            runtime_instructions = {
                key: value
                for key, value in {
                    "include_patterns": parse_patterns(include) if include else None,
                    "exclude_patterns": parse_patterns(exclude) if exclude else None,
                    "focus_modules": parse_patterns(focus) if focus else None,
                    "doc_type": doc_type,
                    "custom_instructions": instructions,
                }.items()
                if value not in (None, [], "")
            }

            if verbose:
                if include:
                    logger.debug(f"Include patterns: {parse_patterns(include)}")
                if exclude:
                    logger.debug(f"Exclude patterns: {parse_patterns(exclude)}")
                if focus:
                    logger.debug(f"Focus modules: {parse_patterns(focus)}")
                if doc_type:
                    logger.debug(f"Doc type: {doc_type}")
                if instructions:
                    logger.debug(f"Custom instructions: {instructions}")

        runtime_overrides = _build_runtime_overrides(
            output_dir=output_dir,
            runtime_instructions=runtime_instructions,
            max_tokens=max_tokens,
            max_token_per_module=max_token_per_module,
            max_token_per_leaf_module=max_token_per_leaf_module,
            max_depth=max_depth,
            max_concurrent=max_concurrent,
            max_retries=max_retries,
            language=language,
            main_model=main_model,
            cluster_model=cluster_model,
            long_context_model=long_context_model,
            long_context_threshold=long_context_threshold,
            base_config=base_config,
        )
        effective_config = load_config(
            config_file,
            repo_path=str(repo_path),
            overrides=runtime_overrides,
            context="cli",
            resolve_secrets=True,
        )
        validate_llm_credentials(effective_config)
        log_effective_config(effective_config)

        # Log max token settings if verbose
        if verbose:
            logger.debug(f"Main model: {effective_config.main_model}")
            logger.debug(f"Max tokens: {effective_config.max_tokens}")
            logger.debug(f"Max token/module: {effective_config.max_token_per_module}")
            logger.debug(f"Max token/leaf module: {effective_config.max_token_per_leaf_module}")
            logger.debug(f"Max depth: {effective_config.max_depth}")
            logger.debug(f"Max concurrent: {effective_config.max_concurrent}")

        # Create generator
        generator = CLIDocumentationGenerator(
            repo_path=repo_path,
            output_dir=output_dir,
            config=effective_config,
            verbose=verbose,
            generate_html=github_pages,
            generate_static=generate_static,
            no_cache=no_cache,
            hide_repo_links=hide_repo_links,
        )

        # Run generation
        job = generator.generate()

        # Post-generation
        generation_time = time.time() - start_time

        # Get repository info
        repo_url = None
        commit_hash = get_git_commit_hash(repo_path)
        current_branch = get_git_branch(repo_path)

        if is_git_repository(repo_path):
            try:
                import git

                repo = git.Repo(repo_path)
                if repo.remotes:
                    repo_url = repo.remotes.origin.url
            except Exception:
                pass

        # Display instructions
        display_post_generation_instructions(
            output_dir=output_dir,
            repo_name=repo_path.name,
            repo_url=repo_url,
            branch_name=branch_name,
            github_pages=github_pages,
            files_generated=job.files_generated,
            statistics={
                "module_count": job.module_count,
                "total_files_analyzed": job.statistics.total_files_analyzed,
                "generation_time": generation_time,
                "total_tokens_used": job.statistics.total_tokens_used,
            },
        )

    except ValueError as e:
        logger.error(str(e))
        sys.exit(EXIT_CONFIG_ERROR)
    except ConfigurationError as e:
        logger.error(e.message)
        logger.error(f"Traceback: {traceback.format_exc()}")
        sys.exit(e.exit_code)
    except RepositoryError as e:
        logger.error(e.message)
        logger.error(f"Traceback: {traceback.format_exc()}")
        sys.exit(e.exit_code)
    except APIError as e:
        logger.error(e.message)
        logger.error(f"Traceback: {traceback.format_exc()}")
        sys.exit(e.exit_code)
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        sys.exit(handle_error(e, verbose=verbose))
