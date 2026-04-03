"""
build-static command: render existing markdown docs to standalone HTML pages.
"""

import sys
from pathlib import Path

import click
import structlog

from codewiki.src.logging_setup import configure_cli_logging

_logger = structlog.get_logger("codewiki.cli.build_static")


@click.command(name="build-static")
@click.argument(
    "docs_dir",
    default="docs",
    type=click.Path(file_okay=False),
    metavar="DOCS_DIR",
)
@click.option(
    "--no-repo-links",
    "hide_repo_links",
    is_flag=True,
    help="Omit Repository and DeepWiki links from the generated HTML",
)
def build_static_command(docs_dir: str, hide_repo_links: bool):
    """Render markdown files in DOCS_DIR to standalone HTML pages.

    Converts every .md file found in DOCS_DIR into a self-contained .html
    file using the same template and pipeline as `codewiki generate --static`.
    Existing HTML files are overwritten; no LLM calls are made.

    DOCS_DIR defaults to ./docs.

    \b
    Examples:
      codewiki build-static
      codewiki build-static ./public/wechat-decrypt
      codewiki build-static /abs/path/to/my-docs
      codewiki build-static --no-repo-links
    """
    configure_cli_logging(verbose=False)
    from codewiki.cli.static_generator import StaticHTMLGenerator

    path = Path(docs_dir).resolve()
    if not path.is_dir():
        _logger.error("Directory not found", path=str(path))
        sys.exit(1)

    _logger.info("Building static HTML", path=str(path))
    generator = StaticHTMLGenerator()
    written = generator.generate(path, hide_repo_links=hide_repo_links)

    if not written:
        _logger.warning("No markdown files found; nothing generated", path=str(path))
        sys.exit(0)

    for name in written:
        _logger.info("Generated HTML file", filename=name)

    _logger.info("Static HTML generation complete", count=len(written), path=str(path))
