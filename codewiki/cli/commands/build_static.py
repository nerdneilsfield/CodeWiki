"""
build-static command: render existing markdown docs to standalone HTML pages.
"""

import sys
import click
from pathlib import Path


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
    from codewiki.cli.static_generator import StaticHTMLGenerator

    path = Path(docs_dir).resolve()
    if not path.is_dir():
        click.secho(f"✗ Directory not found: {path}", fg="red", err=True)
        sys.exit(1)

    click.echo(f"Building static HTML from {path} …")
    generator = StaticHTMLGenerator()
    written = generator.generate(path, hide_repo_links=hide_repo_links)

    if not written:
        click.secho("⚠  No .md files found — nothing generated.", fg="yellow")
        sys.exit(0)

    for name in written:
        click.echo(f"  ✓ {name}")

    click.secho(
        f"\n✓ Generated {len(written)} HTML file(s) in {path}",
        fg="green",
        bold=True,
    )
