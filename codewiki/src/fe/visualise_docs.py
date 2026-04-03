#!/usr/bin/env python3
"""
Simple documentation server for hosting documentation folders.

This server serves documentation folders with the following structure:
- overview.md: The main overview document
- module_tree.json: Hierarchical structure of modules
- Various .md files for different modules

Usage:
    python docs_server.py --docs-folder path/to/docs --port 8080
"""

import argparse
import asyncio
import html as html_mod
import logging
import os
import re
import sys
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from markdown_it import MarkdownIt
import nh3

from .template_utils import render_template
from .templates import DOCS_VIEW_TEMPLATE, prepare_docs_content
from codewiki.src.utils import file_manager, module_doc_filename, find_module_doc
from codewiki.src.be.postprocess.anchor import heading_to_slug
from codewiki.src.logging_setup import configure_cli_logging

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Documentation Server",
    description="Simple documentation server for hosting markdown documentation folders",
)

# Global variables to store configuration
DOCS_FOLDER = None
MODULE_TREE = None


def initialize_globals():
    """Initialize global variables from environment or command line args if not already set."""
    global DOCS_FOLDER, MODULE_TREE

    if DOCS_FOLDER is None:
        # Try to get from environment variable or use a default
        import os

        docs_folder_path = os.environ.get("DOCS_FOLDER")
        if docs_folder_path and Path(docs_folder_path).exists():
            DOCS_FOLDER = docs_folder_path
            MODULE_TREE = load_module_tree(Path(docs_folder_path))
        else:
            # If no environment variable, we need to handle this gracefully
            # The FastAPI endpoints will need to check if DOCS_FOLDER is None
            pass


# Markdown parser — enable table and strikethrough plugins
md = MarkdownIt().enable("table").enable("strikethrough")


def load_module_tree(docs_folder: Path) -> Optional[Dict]:
    """Load the module tree structure from module_tree.json."""
    tree_file = docs_folder / "module_tree.json"
    if not tree_file.exists():
        logger.warning("module_tree.json not found in %s", docs_folder)
        return None

    try:
        tree = file_manager.load_json(str(tree_file))
        _attach_doc_filenames(tree, str(docs_folder))
        return tree
    except Exception as e:
        logger.error("Error loading module_tree.json: %s", e)
        return None


def _attach_doc_filenames(
    tree: Optional[Dict], docs_dir: str, path: Optional[list[str]] = None
) -> None:
    """Annotate module tree nodes with doc filenames based on module path."""
    if not tree:
        return
    base = path or []
    for name, info in tree.items():
        module_path = base + [name]
        doc_filename = info.get("_doc_filename")
        if doc_filename:
            found_path = Path(docs_dir) / doc_filename
            info["doc_filename"] = doc_filename
            info["doc_exists"] = found_path.exists()
        else:
            found = find_module_doc(docs_dir, module_path)
            if found:
                info["doc_filename"] = os.path.basename(found)
                info["doc_exists"] = True
            else:
                info["doc_filename"] = module_doc_filename(module_path)
                info["doc_exists"] = False
        children = info.get("children")
        if isinstance(children, dict) and children:
            _attach_doc_filenames(children, docs_dir, module_path)


def _fix_markdown_links(content: str, base_url: Optional[str] = None) -> str:
    """
    Pre-process markdown link URLs:
    - Percent-encode spaces so markdown-it can parse them.
    - When *base_url* is given, rewrite relative .md links (e.g. ``./auth.md``,
      ``auth.md``) to absolute URLs (e.g. ``/static-docs/{job_id}/auth.md``).
      External links and anchor-only links are left untouched.
    """
    import re

    def _fix_url(m):
        text, url = m.group(1), m.group(2)
        # Percent-encode spaces
        if " " in url:
            url = url.replace(" ", "%20")
        # Rewrite relative .md links to absolute URLs
        if (
            base_url
            and url.endswith(".md")
            and not url.startswith("http")
            and not url.startswith("/")
            and not url.startswith("#")
        ):
            clean = re.sub(r"^\.{1,2}/", "", url)
            url = base_url + clean
        return f"[{text}]({url})"

    return re.sub(r"\[([^\]]*)\]\(([^)]*)\)", _fix_url, content)


def _inject_heading_ids(html: str) -> str:
    """Add id attributes to heading tags using stable slug function.

    Duplicate slugs get a numeric suffix (-1, -2, ...) to ensure uniqueness,
    matching the convention used by GitHub and most Markdown renderers.
    """
    import re

    seen_slugs: dict[str, int] = {}

    def replacer(match):
        tag = match.group(1)
        inner = match.group(2)
        visible = re.sub(r"<[^>]+>", "", inner)
        slug = heading_to_slug(visible)
        if not slug:
            return match.group(0)
        # Deduplicate: first occurrence gets bare slug, subsequent get -1, -2, ...
        if slug in seen_slugs:
            seen_slugs[slug] += 1
            unique_slug = f"{slug}-{seen_slugs[slug]}"
        else:
            seen_slugs[slug] = 0
            unique_slug = slug
        return f'<{tag} id="{unique_slug}">{inner}</{tag}>'

    return re.sub(r"<(h[1-6])>(.*?)</\1>", replacer, html, flags=re.DOTALL)


def markdown_to_html(content: str, base_url: Optional[str] = None) -> str:
    """Convert markdown content to HTML, with special handling for mermaid diagrams."""
    # Pre-process: fix link URLs (spaces + optional absolute-URL rewriting)
    content = _fix_markdown_links(content, base_url)

    # First, convert markdown to HTML
    html = md.render(content)

    # Post-process to ensure mermaid code blocks are properly formatted
    # Look for code blocks with language-mermaid class and convert them to mermaid divs
    # Pattern to match mermaid code blocks
    pattern = r'<pre><code class="language-mermaid">(.*?)</code></pre>'

    def replace_mermaid(match):
        mermaid_code = match.group(1)
        mermaid_code = html_mod.unescape(mermaid_code)
        mermaid_code = nh3.clean(mermaid_code, tags=set(), attributes={})
        return f'<div class="mermaid">{mermaid_code}</div>'

    # Replace mermaid code blocks with proper mermaid divs
    html_output = re.sub(pattern, replace_mermaid, html, flags=re.DOTALL)

    # Inject stable heading IDs (replaces sequential h-0/h-1 JS assignment)
    html_output = _inject_heading_ids(html_output)

    return prepare_docs_content(html_output)


def get_file_title(file_path: Path) -> str:
    """Extract title from markdown file, fallback to filename."""
    try:
        content = file_manager.load_text(str(file_path))
        first_line = content.split("\n")[0].strip()
        if first_line.startswith("# "):
            return first_line[2:].strip()
    except Exception:
        pass

    # Fallback to filename without extension
    return file_path.stem.replace("_", " ").title()


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the overview page as the main page."""
    initialize_globals()

    if DOCS_FOLDER is None:
        raise HTTPException(
            status_code=500,
            detail="Documentation folder not configured. Please set DOCS_FOLDER environment variable or run with --docs-folder argument.",
        )

    overview_file = Path(DOCS_FOLDER) / "overview.md"

    if not overview_file.exists():
        raise HTTPException(
            status_code=404, detail="overview.md not found in the documentation folder"
        )

    try:
        html = await asyncio.to_thread(_render_overview_sync, overview_file)
        return HTMLResponse(content=html)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading overview.md: {e}")


@app.get("/{filename:path}", response_class=HTMLResponse)
async def serve_doc(filename: str):
    """Serve individual documentation files."""
    initialize_globals()

    if DOCS_FOLDER is None:
        raise HTTPException(
            status_code=500,
            detail="Documentation folder not configured. Please set DOCS_FOLDER environment variable or run with --docs-folder argument.",
        )

    # Security check: ensure we're only serving .md files and they exist in the docs folder
    if not filename.endswith(".md"):
        raise HTTPException(status_code=404, detail="Only markdown files are supported")

    file_path = Path(DOCS_FOLDER) / filename

    # Ensure the file is within the docs folder (prevent directory traversal)
    try:
        file_path = file_path.resolve()
        docs_folder_resolved = Path(DOCS_FOLDER).resolve()
        if not file_path.is_relative_to(docs_folder_resolved):
            raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        raise HTTPException(status_code=403, detail="Invalid file path")

    if not file_path.exists():
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        found = find_module_doc(DOCS_FOLDER, stem.split("-"))
        if found:
            file_path = Path(found)
        else:
            raise HTTPException(status_code=404, detail=f"File {filename} not found")

    try:
        html = await asyncio.to_thread(_render_doc_sync, file_path, filename)
        return HTMLResponse(content=html)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading {filename}: {e}")


# Mount static files
app.mount("/static", StaticFiles(directory="."), name="static")


def _render_overview_sync(overview_file: Path) -> str:
    content = file_manager.load_text(str(overview_file))
    html_content = markdown_to_html(content)
    title = get_file_title(overview_file)
    context = {
        "title": title,
        "content": html_content,
        "navigation": MODULE_TREE,
        "current_page": "overview.md",
    }
    return render_template(DOCS_VIEW_TEMPLATE, context)


def _render_doc_sync(file_path: Path, filename: str) -> str:
    content = file_manager.load_text(str(file_path))
    html_content = markdown_to_html(content)
    title = get_file_title(file_path)
    context = {
        "title": title,
        "content": html_content,
        "navigation": MODULE_TREE,
        "current_page": filename,
    }
    return render_template(DOCS_VIEW_TEMPLATE, context)


def main():
    """Main function to run the documentation server."""
    parser = argparse.ArgumentParser(
        description="Simple documentation server for hosting markdown documentation folders"
    )
    parser.add_argument(
        "--docs-folder",
        type=str,
        required=True,
        help="Path to the documentation folder containing markdown files and module_tree.json",
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port to run the server on (default: 8000)"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind the server to (default: 127.0.0.1)",
    )
    parser.add_argument("--debug", action="store_true", help="Run the server in debug mode")

    args = parser.parse_args()
    configure_cli_logging(verbose=args.debug)

    # Validate docs folder
    docs_folder = Path(args.docs_folder)
    if not docs_folder.exists():
        logger.error("Documentation folder '%s' does not exist", docs_folder)
        sys.exit(1)

    if not docs_folder.is_dir():
        logger.error("'%s' is not a directory", docs_folder)
        sys.exit(1)

    # Check for overview.md
    overview_file = docs_folder / "overview.md"
    if not overview_file.exists():
        logger.warning("overview.md not found in '%s'", docs_folder)

    # Set global variables and environment variable for uvicorn reload
    global DOCS_FOLDER, MODULE_TREE
    DOCS_FOLDER = str(docs_folder.resolve())
    MODULE_TREE = load_module_tree(docs_folder)

    # Set environment variable so uvicorn reload can pick it up
    import os

    os.environ["DOCS_FOLDER"] = DOCS_FOLDER

    logger.info("Starting documentation server")
    logger.info("Documentation folder: %s", DOCS_FOLDER)
    logger.info("Server running at: http://%s:%s", args.host, args.port)
    logger.info("Main page: overview.md")

    if MODULE_TREE:
        modules_count = len(MODULE_TREE)
        logger.info("Found %d main modules in module_tree.json", modules_count)

    logger.info("Press Ctrl+C to stop the server")

    try:
        import uvicorn

        uvicorn.run(
            "visualise_docs:app",
            host=args.host,
            port=args.port,
            reload=args.debug,
            log_level="debug" if args.debug else "info",
        )
    except KeyboardInterrupt:
        logger.info("Server stopped")


if __name__ == "__main__":
    main()
