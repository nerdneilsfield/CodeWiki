from __future__ import annotations

import hashlib
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from codewiki.src.be.prompt_template import PROMPT_VERSION
from codewiki.src.codewiki_config import CodeWikiConfig
from codewiki.src.config import OVERVIEW_FILENAME
from codewiki.src.utils import (
    _normalize_for_match,
    doc_id_for_path,
    find_module_doc,
    module_doc_filename,
)

logger = logging.getLogger(__name__)


def iter_tree_nodes(tree: Dict[str, Any], parent_path: Optional[List[str]] = None):
    """Yield (module_path, key, info) for every node in the tree."""
    base = parent_path or []
    for key, info in tree.items():
        path = base + [key]
        yield path, key, info
        children = info.get("children") or {}
        if isinstance(children, dict) and children:
            yield from iter_tree_nodes(children, path)


def collect_path_counts(tree: Dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for _module_path, _key, info in iter_tree_nodes(tree):
        path = info.get("path", "")
        counts[path] += 1
    return counts


def stable_hash(parts: list[str]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def hash_mapping(mapping: Dict[str, str], extra: list[str] | None = None) -> str:
    items = [f"{key}:{mapping[key]}" for key in sorted(mapping)]
    if extra:
        items.extend(extra)
    return stable_hash(items)


def content_similarity(text_a: str, text_b: str) -> float:
    lines_a = set(text_a.strip().splitlines())
    lines_b = set(text_b.strip().splitlines())
    if not lines_a and not lines_b:
        return 1.0
    union = lines_a | lines_b
    if not union:
        return 1.0
    return len(lines_a & lines_b) / len(union)


def dedup_docs_directory(working_dir: str) -> dict[str, list]:
    """Resolve duplicate markdown files that normalize to the same name."""
    groups: dict[str, list[str]] = {}
    for fname in os.listdir(working_dir):
        if not fname.endswith(".md") or fname.startswith("_"):
            continue
        groups.setdefault(_normalize_for_match(fname), []).append(fname)

    removed: list[str] = []
    skipped_conflicts: list[list[str]] = []
    for files in groups.values():
        if len(files) <= 1:
            continue
        contents: dict[str, str] = {}
        for fname in files:
            try:
                with open(os.path.join(working_dir, fname), "r", encoding="utf-8") as f:
                    contents[fname] = f.read()
            except OSError:
                contents[fname] = ""
        files.sort(key=lambda name: len(contents.get(name, "")), reverse=True)
        winner = files[0]
        all_similar = all(
            content_similarity(contents[winner], contents[other]) > 0.8 for other in files[1:]
        )
        if not all_similar:
            logger.warning(
                "Dedup conflict: %s normalize to the same name but diverge in content",
                files,
            )
            skipped_conflicts.append(files)
            continue
        for loser in files[1:]:
            os.remove(os.path.join(working_dir, loser))
            removed.append(loser)
    return {"removed": removed, "skipped_conflicts": skipped_conflicts}


def cleanup_legacy_internal_files(working_dir: str) -> list[str]:
    """Remove legacy cache files that used to live in the docs root."""
    removed: list[str] = []
    for filename in ("_parent_doc_hashes.json", "_tree_cache_meta.json", "_guide_cache.json"):
        path = os.path.join(working_dir, filename)
        if not os.path.exists(path):
            continue
        try:
            os.remove(path)
            removed.append(filename)
        except OSError:
            logger.warning("Failed to remove legacy internal file %s", path)
    return removed


def config_fingerprint(config: CodeWikiConfig) -> str:
    """Fingerprint for clustering cache — only structural params, no models.

    Clustering depends on code structure (covered by commit hash) and
    structural settings.  Model changes should NOT invalidate the cache:
    cluster_model only affects naming, main_model only affects doc generation.
    """
    return stable_hash(
        [
            config.output_language,
            str(config.max_depth),
            # include/exclude patterns affect which files are scanned
            *sorted(config.include_patterns or []),
            *sorted(config.exclude_patterns or []),
            "clustering-v8-tfidf",
        ]
    )


def freeze_doc_filenames(tree: Dict[str, Any]) -> None:
    """Populate ``_doc_filename`` on each tree node using collision-aware rules."""
    path_counts = collect_path_counts(tree)

    def _walk(children: Dict[str, Any], parent_stem: str = ""):
        for key, info in children.items():
            if "_doc_filename" not in info:
                path = info.get("path", "")
                if path and path_counts.get(path, 0) == 1:
                    filename = module_doc_filename([path])
                elif parent_stem:
                    filename = module_doc_filename([parent_stem, key])
                elif path:
                    filename = module_doc_filename([path, key])
                else:
                    filename = module_doc_filename([key])
                info["_doc_filename"] = filename
            child_stem = os.path.splitext(info["_doc_filename"])[0]
            nested = info.get("children") or {}
            if isinstance(nested, dict) and nested:
                _walk(nested, child_stem)

    _walk(tree)


def compute_module_input_hash(
    module_name: str,
    module_path: list[str],
    module_info: dict[str, Any],
    components: dict[str, Any],
    config: CodeWikiConfig,
    assigned_file: str = "",
) -> str:
    """Compute the cache input hash for a module artifact."""
    comp_ids = sorted(module_info.get("components", []))
    source_hashes: list[str] = []
    for component_id in comp_ids:
        node = components.get(component_id)
        source_code = getattr(node, "source_code", "")
        if source_code:
            source_hashes.append(hashlib.sha256(source_code.encode("utf-8")).hexdigest())
    custom_text = config.get_prompt_addition() if hasattr(config, "get_prompt_addition") else ""
    custom_hash = hashlib.sha256(custom_text.encode("utf-8")).hexdigest()
    output_language = getattr(config, "output_language", "en")
    return stable_hash(
        [
            module_name,
            "/".join(module_path),
            *comp_ids,
            *source_hashes,
            assigned_file,
            output_language,
            custom_hash,
            PROMPT_VERSION,
        ]
    )


@dataclass
class TaskSpec:
    """Lightweight task descriptor for cache planning."""

    doc_id: str
    kind: str
    module_path: list[str]
    output_file: str
    depends_on: list[str] = field(default_factory=list)
    input_hash: str = ""
    language: str = "en"
    prompt_version: str = ""


def build_generation_tasks(
    tree: Dict[str, Any],
    config: CodeWikiConfig,
) -> list[TaskSpec]:
    """Build task specs from the frozen tree for cache planning."""
    tasks: list[TaskSpec] = []

    def _walk(children: Dict[str, Any], parent_path: List[str]) -> list[str]:
        child_doc_ids: list[str] = []
        for key, info in children.items():
            current_path = parent_path + [key]
            nested = info.get("children") or {}
            nested_child_ids = (
                _walk(nested, current_path) if isinstance(nested, dict) and nested else []
            )
            doc_id = doc_id_for_path(tree, current_path)
            tasks.append(
                TaskSpec(
                    doc_id=doc_id,
                    kind="module" if not nested_child_ids else "overview",
                    module_path=current_path,
                    output_file=info.get("_doc_filename", module_doc_filename(current_path)),
                    depends_on=nested_child_ids,
                    input_hash=stable_hash(
                        [
                            *sorted(info.get("components", [])),
                            *nested_child_ids,
                            config.output_language,
                            "v7",
                        ]
                    ),
                    language=config.output_language,
                    prompt_version="v7",
                )
            )
            child_doc_ids.append(doc_id)
        return child_doc_ids

    top_level_ids = _walk(tree, [])
    tasks.append(
        TaskSpec(
            doc_id="overview:root",
            kind="overview",
            module_path=[],
            output_file=OVERVIEW_FILENAME,
            depends_on=top_level_ids,
            input_hash=stable_hash([*top_level_ids, config.output_language, "v7"]),
            language=config.output_language,
            prompt_version="v7",
        )
    )
    return tasks


def module_doc_exists(
    working_dir: str,
    module_path: List[str],
    module_tree: Optional[Dict[str, Any]] = None,
) -> bool:
    """Return True if a non-trivial .md file already exists for *module_path*."""
    found = find_module_doc(working_dir, module_path)
    return found is not None and os.path.getsize(found) > 100
