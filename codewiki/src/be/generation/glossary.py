"""Global glossary and link map for cross-module consistency.

Aligned with v3.md section 3.4 L193-195.
"""
import re
from typing import Any

from codewiki.src.be.index.models import Visibility, ExportStatus


def build_glossary(
    index_products: Any | None,
) -> dict[str, str]:
    """Build global glossary from public/unknown-visibility symbols.

    Includes symbols with Visibility PUBLIC or UNKNOWN and ExportStatus
    EXPORTED or UNKNOWN.  This ensures Python repos (which typically have
    ExportStatus.UNKNOWN and Visibility.UNKNOWN) still produce glossary entries.

    Terms: class/function names
    Definitions: first sentence of docstring + kind + file location

    Returns: {term: definition} sorted by term.
    Returns empty dict when index_products is None or has no symbol_table.
    """
    if not index_products or not hasattr(index_products, "symbol_table"):
        return {}

    _INCLUDED_VISIBILITY = {Visibility.PUBLIC, Visibility.UNKNOWN}
    _INCLUDED_EXPORT = {ExportStatus.EXPORTED, ExportStatus.UNKNOWN}

    glossary: dict[str, str] = {}
    for sym in index_products.symbol_table.all_symbols():
        if sym.visibility not in _INCLUDED_VISIBILITY:
            continue
        if sym.export_status not in _INCLUDED_EXPORT:
            continue

        # First sentence of docstring — use regex to avoid splitting on "e.g." or "i.e."
        doc_summary = ""
        if sym.docstring:
            first_sentence = re.split(r'(?<=[.!?])\s+', sym.docstring.strip())[0]
            doc_summary = first_sentence.strip()
            if not doc_summary.endswith((".", "!", "?")):
                doc_summary += "."

        definition = f"{doc_summary} ({sym.kind.value}, {sym.file_path})".strip()
        glossary[sym.name] = definition

    return dict(sorted(glossary.items()))


def build_link_map(
    module_tree: dict,
) -> dict[str, str]:
    """Build link map for cross-module references.

    Keys use slash-joined tree title paths for prompt reference.
    Values use frozen ``_doc_filename`` when present; otherwise they fall back
    to ``module_doc_filename()`` based on path or title path.
    """
    link_map: dict[str, str] = {}
    _walk_tree(module_tree, [], link_map)
    return dict(sorted(link_map.items()))


def _walk_tree(
    tree: dict,
    parent_path: list[str],
    link_map: dict[str, str],
) -> None:
    """Recursively walk module tree and populate link_map."""
    from codewiki.src.utils import module_doc_filename

    for title, info in tree.items():
        if not isinstance(info, dict):
            continue

        key_path = parent_path + [title]
        key_str = "/".join(key_path) if len(key_path) > 1 else title

        doc_filename = info.get("_doc_filename")
        if not doc_filename:
            path = info.get("path", "")
            doc_filename = module_doc_filename([path] if path else key_path)

        link_map[key_str] = doc_filename

        children = info.get("children", {})
        if children and isinstance(children, dict):
            _walk_tree(children, key_path, link_map)
