"""Cached parent-document segment generation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from inspect import isawaitable

from codewiki.src.be.cache_manager import CacheManager
from codewiki.src.be.documentation_tree_utils import stable_hash
from codewiki.src.be.prompt_template import (
    PROMPT_VERSION,
    format_parent_child_summary_prompt,
    format_parent_opening_prompt,
    format_parent_overview_prompt,
)
from codewiki.src.config import MODULE_PARTS_DIR


def parent_opening_artifact_id(doc_id: str) -> str:
    return f"module:{doc_id}:segment:opening"


def parent_overview_artifact_id(doc_id: str) -> str:
    return f"module:{doc_id}:segment:overview"


def parent_child_segment_artifact_id(parent_doc_id: str, child_doc_id: str) -> str:
    return f"module:{parent_doc_id}:segment:child:{child_doc_id}"


def doc_stem_from_filename(doc_filename: str) -> str:
    return os.path.splitext(doc_filename)[0]


def parent_segment_dir(cache_dir: str, doc_stem: str) -> str:
    return os.path.join(cache_dir, MODULE_PARTS_DIR, doc_stem)


def parent_segment_path(
    cache_dir: str,
    doc_stem: str,
    segment_type: str,
    child_doc_stem: str | None = None,
) -> str:
    base = parent_segment_dir(cache_dir, doc_stem)
    if segment_type == "opening":
        return os.path.join(base, "opening.md")
    if segment_type == "overview":
        return os.path.join(base, "overview.md")
    if segment_type == "child":
        if not child_doc_stem:
            raise ValueError("child_doc_stem is required for child segments")
        return os.path.join(base, f"child_{child_doc_stem}.md")
    raise ValueError(f"unknown segment_type: {segment_type!r}")


def compute_opening_input_hash(
    *,
    title: str,
    path: str,
    description: str,
    output_language: str,
) -> str:
    return stable_hash(["opening", title, path, description, output_language, PROMPT_VERSION])


def compute_overview_input_hash(
    *,
    title: str,
    path: str,
    description: str,
    direct_child_pairs: list[tuple[str, str]],
    output_language: str,
) -> str:
    flat = ["overview", title, path, description]
    for child_id, child_hash in sorted(direct_child_pairs, key=lambda item: item[0]):
        flat.extend([f"child:{child_id}", f"hash:{child_hash}"])
    flat.extend([output_language, PROMPT_VERSION])
    return stable_hash(flat)


def compute_child_segment_input_hash(
    *,
    child_module_id: str,
    child_title: str,
    child_path: str,
    child_description: str,
    child_input_hash: str,
    output_language: str,
) -> str:
    return stable_hash(
        [
            "child",
            child_module_id,
            child_title,
            child_path,
            child_description,
            child_input_hash,
            output_language,
            PROMPT_VERSION,
        ]
    )


def compute_assembled_parent_input_hash(
    *,
    opening_hash: str,
    overview_hash: str,
    child_segment_hashes: list[str],
    output_language: str,
) -> str:
    return stable_hash(
        [
            "assembled",
            opening_hash,
            overview_hash,
            *sorted(child_segment_hashes),
            output_language,
            PROMPT_VERSION,
        ]
    )


async def _call_middleware(middleware, prompt: str, model: str):
    result = middleware.call(prompt, model=model, temperature=0.0)
    if isawaitable(result):
        result = await result
    return result


async def generate_segment(
    *,
    artifact_id: str,
    input_hash: str,
    prompt: str,
    model: str,
    middleware,
    cache_manager: CacheManager,
    output_path: str,
) -> str:
    cache_manager.plan_task(artifact_id, output_file=output_path)
    cache_manager.mark_running(artifact_id)
    try:
        result = await _call_middleware(middleware, prompt, model)
    except Exception as exc:
        cache_manager.mark_failed(artifact_id, error=str(exc))
        raise
    text = result.content
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp = output_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, output_path)
    cache_manager.mark_done(
        artifact_id,
        input_hash=input_hash,
        output_path=output_path,
        model=getattr(result, "model", model),
    )
    return text


def _read_text(path: str) -> str:
    if not path or not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


async def _ensure_segment(
    *,
    artifact_id: str,
    input_hash: str,
    prompt: str,
    model: str,
    middleware,
    cache_manager: CacheManager,
    output_path: str,
) -> str:
    if cache_manager.is_valid(artifact_id, input_hash):
        existing = _read_text(output_path)
        if existing:
            return existing
    return await generate_segment(
        artifact_id=artifact_id,
        input_hash=input_hash,
        prompt=prompt,
        model=model,
        middleware=middleware,
        cache_manager=cache_manager,
        output_path=output_path,
    )


@dataclass
class ParentAssemblyResult:
    output_path: str
    input_hash: str
    model: str


async def generate_or_assemble_parent_doc(
    *,
    parent_doc_id: str,
    parent_node: dict,
    working_dir: str,
    cache_dir: str,
    cache_manager: CacheManager,
    middleware,
    cluster_model: str,
    output_language: str,
) -> ParentAssemblyResult:
    title = parent_node.get("title", parent_doc_id)
    path = parent_node.get("path", parent_doc_id)
    description = parent_node.get("description", "")
    doc_filename = parent_node["_doc_filename"]
    doc_stem = doc_stem_from_filename(doc_filename)
    children = parent_node.get("children") or {}

    opening_hash = compute_opening_input_hash(
        title=title,
        path=path,
        description=description,
        output_language=output_language,
    )
    opening_text = await _ensure_segment(
        artifact_id=parent_opening_artifact_id(parent_doc_id),
        input_hash=opening_hash,
        prompt=format_parent_opening_prompt(
            title=title,
            path=path,
            description=description,
            output_language=output_language,
        ),
        model=cluster_model,
        middleware=middleware,
        cache_manager=cache_manager,
        output_path=parent_segment_path(cache_dir, doc_stem, "opening"),
    )

    direct_child_pairs: list[tuple[str, str]] = []
    child_segment_hashes: list[str] = []
    child_sections: list[tuple[str, str]] = []
    child_meta: list[dict[str, str]] = []
    for child_title, child in children.items():
        child_doc_id = child.get("module_id") or child_title
        child_input_hash = cache_manager.get_input_hash(f"module:{child_doc_id}") or ""
        direct_child_pairs.append((child_doc_id, child_input_hash))
        child_meta.append(
            {
                "title": child.get("title", child_title),
                "path": child.get("path", ""),
                "description": child.get("description", ""),
            }
        )

    overview_hash = compute_overview_input_hash(
        title=title,
        path=path,
        description=description,
        direct_child_pairs=direct_child_pairs,
        output_language=output_language,
    )
    overview_text = await _ensure_segment(
        artifact_id=parent_overview_artifact_id(parent_doc_id),
        input_hash=overview_hash,
        prompt=format_parent_overview_prompt(
            title=title,
            path=path,
            description=description,
            children=child_meta,
            output_language=output_language,
        ),
        model=cluster_model,
        middleware=middleware,
        cache_manager=cache_manager,
        output_path=parent_segment_path(cache_dir, doc_stem, "overview"),
    )

    for child_title, child in children.items():
        child_doc_id = child.get("module_id") or child_title
        child_input_hash = cache_manager.get_input_hash(f"module:{child_doc_id}") or ""
        child_seg_hash = compute_child_segment_input_hash(
            child_module_id=child_doc_id,
            child_title=child.get("title", child_title),
            child_path=child.get("path", ""),
            child_description=child.get("description", ""),
            child_input_hash=child_input_hash,
            output_language=output_language,
        )
        child_segment_hashes.append(child_seg_hash)
        child_doc_filename = child.get("_doc_filename", "")
        child_doc_stem = (
            doc_stem_from_filename(child_doc_filename) if child_doc_filename else child_doc_id
        )
        child_text = await _ensure_segment(
            artifact_id=parent_child_segment_artifact_id(parent_doc_id, child_doc_id),
            input_hash=child_seg_hash,
            prompt=format_parent_child_summary_prompt(
                parent_title=title,
                child_title=child.get("title", child_title),
                child_path=child.get("path", ""),
                child_description=child.get("description", ""),
                child_doc_excerpt=_read_text(os.path.join(working_dir, child_doc_filename))
                if child_doc_filename
                else "",
                output_language=output_language,
            ),
            model=cluster_model,
            middleware=middleware,
            cache_manager=cache_manager,
            output_path=parent_segment_path(
                cache_dir,
                doc_stem,
                "child",
                child_doc_stem=child_doc_stem,
            ),
        )
        child_sections.append((child.get("title", child_title), child_text))

    assembled_lines = [
        f"# {title}",
        "",
        opening_text.rstrip(),
        "",
        "## Architecture Overview",
        "",
        overview_text.rstrip(),
    ]
    if child_sections:
        assembled_lines.extend(["", "## Modules", ""])
        for child_title, child_text in child_sections:
            assembled_lines.extend([f"### {child_title}", "", child_text.rstrip(), ""])
    assembled = "\n".join(assembled_lines).rstrip() + "\n"

    final_path = os.path.join(working_dir, doc_filename)
    os.makedirs(os.path.dirname(final_path) or ".", exist_ok=True)
    with open(final_path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(assembled)
    os.replace(final_path + ".tmp", final_path)

    parent_input_hash = compute_assembled_parent_input_hash(
        opening_hash=opening_hash,
        overview_hash=overview_hash,
        child_segment_hashes=child_segment_hashes,
        output_language=output_language,
    )
    return ParentAssemblyResult(
        output_path=final_path,
        input_hash=parent_input_hash,
        model=cluster_model,
    )


def force_invalidate_parent_segments(
    *,
    parent_doc_id: str,
    parent_node: dict,
    cache_manager: CacheManager,
) -> list[str]:
    artifact_ids = [
        parent_opening_artifact_id(parent_doc_id),
        parent_overview_artifact_id(parent_doc_id),
    ]
    for child in (parent_node.get("children") or {}).values():
        child_doc_id = child.get("module_id") or child.get("title", "")
        if child_doc_id:
            artifact_ids.append(parent_child_segment_artifact_id(parent_doc_id, child_doc_id))
    for artifact_id in artifact_ids:
        cache_manager.invalidate(artifact_id)
    return artifact_ids
