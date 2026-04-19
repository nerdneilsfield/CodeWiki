"""Tree refinement helpers for building a frozen module tree before doc generation."""

from __future__ import annotations

import json
import logging
from inspect import isawaitable
from typing import Any

from codewiki.src.be.cache_manager import CacheManager, module_artifact_id
from codewiki.src.be.identity_reuse import (
    find_dominant_match,
    find_split_successor,
)
from codewiki.src.be.prompt_template import format_refinement_prompt
from codewiki.src.be.refinement_cache import (
    compute_refinement_input_hash,
    load_refinement_payload,
    load_previous_children,
    refinement_artifact_id,
    refinement_output_path,
    save_refinement_payload,
)
from codewiki.src.codewiki_config import RefinementConfig

logger = logging.getLogger(__name__)


def should_attempt_split(
    component_ids: list[str],
    components: dict[str, Any],
    refinement_cfg: RefinementConfig,
    current_depth: int,
) -> bool:
    """Return True when a node is eligible for one more refinement split."""
    if current_depth >= refinement_cfg.max_depth:
        return False
    if len(component_ids) < refinement_cfg.min_components_for_split:
        return False
    distinct_files = {
        getattr(components[cid], "file_path", "")
        for cid in component_ids
        if cid in components and getattr(components[cid], "file_path", "")
    }
    if len(distinct_files) < refinement_cfg.min_distinct_files_for_split:
        return False
    return True


def assign_doc_filename(
    *,
    used_files: dict[str, str],
    artifact_id: str,
    preferred_stem: str,
) -> str:
    """Assign a collision-free markdown filename to an artifact."""
    for existing_name, owner in used_files.items():
        if owner == artifact_id:
            return existing_name

    candidate = f"{preferred_stem}.md"
    if candidate not in used_files:
        used_files[candidate] = artifact_id
        return candidate

    suffix = 2
    while True:
        candidate = f"{preferred_stem}_{suffix}.md"
        if candidate not in used_files:
            used_files[candidate] = artifact_id
            return candidate
        suffix += 1


def _format_components_block(component_ids: list[str], components: dict[str, Any]) -> str:
    lines: list[str] = []
    for cid in sorted(component_ids):
        node = components.get(cid)
        if node is None:
            continue
        file_path = getattr(node, "file_path", "") or getattr(node, "relative_path", "")
        lines.append(f"- {cid} ({file_path})")
    return "\n".join(lines)


def _parse_refinement_response(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
        if stripped.startswith("json"):
            stripped = stripped[4:].lstrip()
    return json.loads(stripped)


async def refine_one_node(
    *,
    parent_doc_id: str,
    parent_title: str,
    parent_path: str,
    component_ids: list[str],
    components: dict[str, Any],
    current_depth: int,
    refinement_cfg: RefinementConfig,
    output_language: str,
    cluster_model: str,
    middleware,
    cache_manager: CacheManager,
    cache_dir: str,
    used_files: dict[str, str],
) -> dict[str, Any]:
    """Refine a single node into child nodes, using refinement cache when valid."""
    artifact_id = refinement_artifact_id(parent_doc_id)
    output_path = refinement_output_path(cache_dir, parent_doc_id)
    input_hash = compute_refinement_input_hash(
        component_ids=component_ids,
        components=components,
        current_depth=current_depth,
        max_depth=refinement_cfg.max_depth,
        min_components_for_split=refinement_cfg.min_components_for_split,
        min_distinct_files_for_split=refinement_cfg.min_distinct_files_for_split,
        max_cluster_components=refinement_cfg.max_cluster_components,
        identity_reuse_threshold=refinement_cfg.identity_reuse_threshold,
        output_language=output_language,
    )

    if cache_manager.is_valid(artifact_id, input_hash):
        cached = load_refinement_payload(cache_dir, parent_doc_id)
        if cached is not None:
            children = cached.get("children", {}) or {}
            for child in children.values():
                filename = child.get("_doc_filename")
                child_module_id = child.get("module_id", "")
                if filename and child_module_id:
                    used_files.setdefault(filename, module_artifact_id(child_module_id))
            return children
        logger.warning(
            "refinement cache entry %s is valid but payload %s is missing",
            artifact_id,
            output_path,
        )

    cache_manager.plan_task(artifact_id, output_file=output_path)
    cache_manager.mark_running(artifact_id)

    if not should_attempt_split(component_ids, components, refinement_cfg, current_depth):
        save_refinement_payload(cache_dir, parent_doc_id, {"children": {}})
        cache_manager.mark_done(
            artifact_id,
            input_hash=input_hash,
            output_path=output_path,
            model="",
        )
        return {}

    prompt = format_refinement_prompt(
        parent_title=parent_title,
        parent_path=parent_path,
        components_block=_format_components_block(component_ids, components),
        current_depth=current_depth,
        max_depth=refinement_cfg.max_depth,
        min_components_for_split=refinement_cfg.min_components_for_split,
        min_distinct_files_for_split=refinement_cfg.min_distinct_files_for_split,
        output_language=output_language,
    )
    try:
        result = middleware.call(prompt, model=cluster_model, temperature=0.0)
        if isawaitable(result):
            result = await result
        parsed = _parse_refinement_response(result.text)
    except Exception as exc:
        cache_manager.mark_failed(artifact_id, error=str(exc))
        raise

    if not parsed.get("should_split"):
        save_refinement_payload(cache_dir, parent_doc_id, {"children": {}})
        cache_manager.mark_done(
            artifact_id,
            input_hash=input_hash,
            output_path=output_path,
            model=getattr(result, "model", ""),
        )
        return {}

    children_raw = parsed.get("children", {}) or {}
    previous_children = load_previous_children(cache_dir, parent_doc_id)
    available_old: dict[str, Any] = dict(previous_children)
    children: dict[str, Any] = {}
    unmatched_titles: list[str] = []
    for title, child in children_raw.items():
        new_components = set(child.get("components") or [])
        match = find_dominant_match(
            new_components,
            available_old,
            threshold=refinement_cfg.identity_reuse_threshold,
            margin=0.15,
        )
        if match is not None:
            old_info = available_old.pop(match.old_key)
            module_id = (
                match.old_module_id or child.get("module_id") or title.lower().replace(" ", "_")
            )
            path = match.old_path or child.get("path") or module_id
            child_artifact = module_artifact_id(module_id)
            old_filename = old_info.get("_doc_filename")
            if old_filename and used_files.get(old_filename) in (None, child_artifact):
                used_files[old_filename] = child_artifact
                filename = old_filename
            else:
                filename = assign_doc_filename(
                    used_files=used_files,
                    artifact_id=child_artifact,
                    preferred_stem=path or module_id,
                )
            children[title] = {
                "module_id": module_id,
                "title": child.get("title", title),
                "path": path,
                "description": child.get("description", old_info.get("description", "")),
                "_doc_filename": filename,
                "components": list(child.get("components", [])),
                "children": {},
            }
            continue

        module_id = child.get("module_id") or title.lower().replace(" ", "_")
        path = child.get("path") or module_id
        children[title] = {
            "module_id": module_id,
            "title": child.get("title", title),
            "path": path,
            "description": child.get("description", ""),
            "_doc_filename": "",
            "components": list(child.get("components", [])),
            "children": {},
        }
        unmatched_titles.append(title)

    for old_key in list(available_old.keys()):
        old_info = available_old[old_key]
        successor_title = find_split_successor(
            set(old_info.get("components") or []),
            {title: {"components": children[title]["components"]} for title in unmatched_titles},
            threshold=refinement_cfg.identity_reuse_threshold,
            margin=0.15,
        )
        if successor_title is None:
            continue
        reused = children[successor_title]
        reused["module_id"] = old_info.get("module_id") or reused["module_id"]
        reused["path"] = old_info.get("path") or reused["path"]
        old_filename = old_info.get("_doc_filename")
        child_artifact = module_artifact_id(reused["module_id"])
        if old_filename and used_files.get(old_filename) in (None, child_artifact):
            used_files[old_filename] = child_artifact
            reused["_doc_filename"] = old_filename
        available_old.pop(old_key, None)
        unmatched_titles.remove(successor_title)

    for title in unmatched_titles:
        child_info = children[title]
        child_info["_doc_filename"] = assign_doc_filename(
            used_files=used_files,
            artifact_id=module_artifact_id(child_info["module_id"]),
            preferred_stem=child_info["path"] or child_info["module_id"],
        )

    save_refinement_payload(cache_dir, parent_doc_id, {"children": children})
    cache_manager.mark_done(
        artifact_id,
        input_hash=input_hash,
        output_path=output_path,
        model=getattr(result, "model", ""),
    )
    return children


def _seed_used_files_from_cache(cache_manager: CacheManager) -> dict[str, str]:
    """Seed doc-filename ownership from current cache registry."""
    used: dict[str, str] = {}
    for output_file, artifact_id in cache_manager.output_file_assignments().items():
        if not output_file:
            continue
        if "/" in output_file or "\\" in output_file:
            continue
        used[output_file] = artifact_id
    return used


async def refine_tree(
    *,
    module_tree: dict[str, Any],
    components: dict[str, Any],
    refinement_cfg: RefinementConfig,
    output_language: str,
    cluster_model: str,
    middleware,
    cache_manager: CacheManager,
    cache_dir: str,
) -> dict[str, Any]:
    """Recursively refine every node in a top-level tree."""
    used_files = _seed_used_files_from_cache(cache_manager)

    async def _walk(node: dict[str, Any], depth: int) -> None:
        module_id = node.get("module_id") or node.get("path") or "node"
        preferred_stem = node.get("path") or module_id
        node["_doc_filename"] = assign_doc_filename(
            used_files=used_files,
            artifact_id=module_artifact_id(module_id),
            preferred_stem=preferred_stem,
        )
        children = await refine_one_node(
            parent_doc_id=module_id,
            parent_title=node.get("title", module_id),
            parent_path=node.get("path", module_id),
            component_ids=list(node.get("components") or []),
            components=components,
            current_depth=depth,
            refinement_cfg=refinement_cfg,
            output_language=output_language,
            cluster_model=cluster_model,
            middleware=middleware,
            cache_manager=cache_manager,
            cache_dir=cache_dir,
            used_files=used_files,
        )
        node["children"] = children
        for child in children.values():
            await _walk(child, depth + 1)

    for top in module_tree.values():
        await _walk(top, 1)
    return module_tree
