"""
Main CLI application for CodeWiki using Click framework.
"""

import logging
import sys
import click

from codewiki import __version__
from codewiki.src.logging_setup import configure_cli_logging

logger = logging.getLogger(__name__)


@click.group()
@click.version_option(version=__version__, prog_name="CodeWiki CLI")
@click.pass_context
def cli(ctx):
    """
    CodeWiki: Transform codebases into comprehensive documentation.

    Generate AI-powered documentation for your code repositories with support
    for Python, Java, JavaScript, TypeScript, C, C++, and C#.
    """
    # Ensure context object exists
    ctx.ensure_object(dict)


@cli.command()
def version():
    """Display version information."""
    configure_cli_logging(verbose=False)
    logger.info("CodeWiki CLI v%s", __version__)
    logger.info("Python-based documentation generator using AI analysis")


# Import commands
from codewiki.cli.commands.config import config_group
from codewiki.cli.commands.generate import generate_command
from codewiki.cli.commands.build_static import build_static_command

# Register command groups
cli.add_command(config_group)
cli.add_command(generate_command, name="generate")
cli.add_command(build_static_command)


def main():
    """Entry point for the CLI."""
    try:
        configure_cli_logging(verbose=False)
        cli(obj={})
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
