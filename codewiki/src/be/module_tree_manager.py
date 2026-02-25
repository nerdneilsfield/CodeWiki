"""
Thread-safe manager for the shared module tree with disk persistence.

When multiple agents process modules concurrently, they all need to read/write
the same ``module_tree.json``.  This manager serialises those operations behind
an ``asyncio.Lock`` so concurrent saves never clobber each other.
"""

import asyncio
from copy import deepcopy
from typing import Dict, Any, List

from codewiki.src.utils import file_manager


class ModuleTreeManager:
    """Lock-protected in-memory module tree with automatic disk persistence."""

    def __init__(self, tree: Dict[str, Any], persist_path: str):
        self._tree = tree
        self._persist_path = persist_path
        self._lock = asyncio.Lock()

    async def get_snapshot(self) -> Dict[str, Any]:
        """Return a deep copy of the entire tree (safe for the caller to mutate)."""
        async with self._lock:
            return deepcopy(self._tree)

    async def update_children(self, path: List[str], new_children: Dict[str, Any]):
        """Merge *new_children* into the children dict at *path* and persist.

        ``path`` is the list of module keys leading to the parent node whose
        ``children`` dict should be updated.  For a top-level module named
        ``"API Server"``, ``path`` would be ``["API Server"]``.
        """
        async with self._lock:
            node = self._tree
            for key in path:
                node = node[key]["children"]
            node.update(new_children)
            file_manager.save_json(self._tree, self._persist_path)

    async def save(self):
        """Force-persist the current in-memory tree to disk."""
        async with self._lock:
            file_manager.save_json(self._tree, self._persist_path)
