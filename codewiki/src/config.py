"""Shared runtime constants for CodeWiki."""

from __future__ import annotations

import os

# Constants
OUTPUT_BASE_DIR = "output"
DEPENDENCY_GRAPHS_DIR = "dependency_graphs"
DOCS_DIR = "docs"
FIRST_MODULE_TREE_FILENAME = "first_module_tree.json"
MODULE_TREE_FILENAME = "module_tree.json"
OVERVIEW_FILENAME = "overview.md"
GENERATION_STATE_FILENAME = "generation_state.json"
INTERNAL_SUBDIR = ".codewiki"
MAX_DEPTH = 2
DEFAULT_MAX_TOKENS = 32_768
DEFAULT_MAX_TOKEN_PER_MODULE = 36_369
DEFAULT_MAX_TOKEN_PER_LEAF_MODULE = 16_000
DEFAULT_MAX_CONCURRENT = 3
DEFAULT_MAX_RETRIES = 2
DEFAULT_LONG_CONTEXT_THRESHOLD = 200_000


def internal_file_path(working_dir: str, filename: str) -> str:
    """Return a path for CodeWiki internal files under .codewiki/."""
    internal_dir = os.path.join(working_dir, INTERNAL_SUBDIR)
    os.makedirs(internal_dir, exist_ok=True)
    return os.path.join(internal_dir, filename)
