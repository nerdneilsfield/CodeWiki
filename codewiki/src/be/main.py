"""
CodeWiki - A tool for generating comprehensive documentation from Python codebases.

This module orchestrates the documentation generation process by:
1. Analyzing code dependencies
2. Clustering related modules
3. Generating documentation using AI agents
4. Creating overview documentation
"""

import logging
import argparse
import asyncio
import traceback
from pathlib import Path

# Configure logging and monitoring
from codewiki.src.be.dependency_analyzer.utils.logging_config import setup_logging

# Initialize colored logging
setup_logging(level=logging.INFO)

logger = logging.getLogger(__name__)

# Local imports
from codewiki.src.be.documentation_generator import DocumentationGenerator
from codewiki.src.config import (
    Config,
)
from codewiki.src.config_loader import load_app_config


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate comprehensive documentation for Python components in dependency order."
    )
    parser.add_argument("--repo-path", type=str, required=True, help="Path to the repository")
    parser.add_argument("--config", type=str, required=True, help="Path to TOML config file")

    return parser.parse_args()


def _build_runtime_config_from_args(args: argparse.Namespace):
    try:
        app_config = load_app_config(Path(args.config))
    except (FileNotFoundError, ValueError) as exc:
        raise RuntimeError(f"Failed to load config '{args.config}': {exc}") from exc
    return app_config.to_runtime_config(args.repo_path)


async def main() -> None:
    """Main entry point for the documentation generation process."""
    try:
        # Parse arguments and create configuration
        args = parse_arguments()
        config = _build_runtime_config_from_args(args)

        # Create and run documentation generator
        doc_generator = DocumentationGenerator(config)
        await doc_generator.run()

    except KeyboardInterrupt:
        logger.debug("Documentation generation interrupted by user")
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
