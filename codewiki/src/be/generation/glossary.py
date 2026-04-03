"""Global glossary and link map for cross-module consistency.

Aligned with v3.md section 3.4 L193-195.
"""

from dataclasses import dataclass
import os
import re
from typing import Any

from codewiki.src.be.index.models import Visibility, ExportStatus

_ABBREVIATIONS = {
    "e.g.": "e<DOT>g<DOT>",
    "i.e.": "i<DOT>e<DOT>",
}


@dataclass(frozen=True)
class GlossaryEntry:
    term: str
    definition: str
    symbol_id: str
    file_path: str
    kind: str


def _first_sentence(text: str) -> str:
    """Extract the first sentence while preserving common abbreviations."""
    protected = text.strip()
    for original, placeholder in _ABBREVIATIONS.items():
        protected = protected.replace(original, placeholder)
    first_sentence = re.split(r"(?<=[.!?])\s+", protected)[0].strip()
    for original, placeholder in _ABBREVIATIONS.items():
        first_sentence = first_sentence.replace(placeholder, original)
    return first_sentence


def build_glossary(
    index_products: Any | None,
) -> dict[str, GlossaryEntry]:
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

    glossary: dict[str, GlossaryEntry] = {}
    for sym in index_products.symbol_table.all_symbols():
        if sym.visibility not in _INCLUDED_VISIBILITY:
            continue
        if sym.export_status not in _INCLUDED_EXPORT:
            continue

        # First sentence of docstring — use regex to avoid splitting on "e.g." or "i.e."
        doc_summary = ""
        if sym.docstring:
            doc_summary = _first_sentence(sym.docstring)
            if not doc_summary.endswith((".", "!", "?")):
                doc_summary += "."

        definition = f"{doc_summary} ({sym.kind.value}, {sym.file_path})".strip()
        glossary[sym.name] = GlossaryEntry(
            term=sym.name,
            definition=definition,
            symbol_id=sym.symbol_id,
            file_path=sym.file_path,
            kind=sym.kind.value,
        )

    return dict(sorted(glossary.items()))


def filter_glossary(
    glossary: dict[str, GlossaryEntry],
    relevant_symbol_ids: set[str],
    module_file_paths: set[str] | None = None,
    token_limit: int = 4000,
) -> dict[str, GlossaryEntry]:
    """Filter glossary to entries relevant to the current module."""
    from codewiki.src.be.utils import count_tokens

    priority_a = {k: v for k, v in glossary.items() if v.symbol_id in relevant_symbol_ids}

    priority_b: dict[str, GlossaryEntry] = {}
    if module_file_paths:
        module_dirs = {os.path.dirname(path) for path in module_file_paths if path}
        for key, value in glossary.items():
            if key in priority_a:
                continue
            if os.path.dirname(value.file_path) in module_dirs:
                priority_b[key] = value

    result: dict[str, GlossaryEntry] = {}
    token_count = 0
    for source in (priority_a, priority_b):
        for key, value in source.items():
            entry_tokens = count_tokens(f"{value.term}: {value.definition}")
            if token_count + entry_tokens > token_limit:
                return result
            result[key] = value
            token_count += entry_tokens

    return result


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
