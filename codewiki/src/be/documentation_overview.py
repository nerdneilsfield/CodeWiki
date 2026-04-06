from __future__ import annotations

import asyncio
import json
import logging
import os
import traceback
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from codewiki.src.be.documentation_tree_utils import hash_mapping
from codewiki.src.be.generation_state import GenerationState, GenerationStateManager
from codewiki.src.be.llm_middleware import LLMMiddleware
from codewiki.src.be.llm_usage import LLMUsageStats
from codewiki.src.be.module_tree_manager import ModuleTreeManager
from codewiki.src.be.prompt_template import format_overview_prompt
from codewiki.src.codewiki_config import CodeWikiConfig
from codewiki.src.config import MODULE_TREE_FILENAME, OVERVIEW_FILENAME
from codewiki.src.utils import content_hash, doc_id_for_path, file_manager, find_module_doc

logger = logging.getLogger(__name__)

# Token overhead for JSON structure, prompt template, and system prompt
_OVERVIEW_OVERHEAD = 30_000


def _split_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs (separated by blank lines)."""
    paragraphs: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if not line.strip() and current:
            paragraphs.append("\n".join(current))
            current = []
        else:
            current.append(line)
    if current:
        paragraphs.append("\n".join(current))
    return paragraphs


def _budget_child_docs(
    docs: dict[str, str],
    max_tokens: int,
) -> dict[str, str]:
    """Progressively fill child docs into a token budget.

    Strategy (from minimal to full):
    1. Start with first paragraph + last paragraph of each doc
    2. If budget remains, add 2nd paragraph to each doc
    3. Continue adding paragraphs until budget is full

    This ensures every module gets at least its summary and conclusion,
    then detail is added uniformly across all modules.
    """

    from codewiki.src.be.utils import count_tokens

    def _estimate_tokens(text: str) -> int:
        return count_tokens(text)

    # Split all docs into paragraphs
    doc_paras: dict[str, list[str]] = {}
    for name, text in docs.items():
        paras = _split_paragraphs(text)
        doc_paras[name] = paras if paras else [""]

    # Phase 1: first paragraph + last paragraph for each doc
    included: dict[str, list[int]] = {}  # name → list of included paragraph indices
    for name, paras in doc_paras.items():
        if len(paras) <= 2:
            included[name] = list(range(len(paras)))
        else:
            included[name] = [0, len(paras) - 1]

    def _build_result() -> dict[str, str]:
        result = {}
        for name, paras in doc_paras.items():
            idx_set = included[name]
            parts = []
            for i in sorted(idx_set):
                parts.append(paras[i])
            omitted = len(paras) - len(idx_set)
            if omitted > 0:
                # Insert truncation marker between included parts
                text = "\n\n".join(parts)
                text += f"\n\n_({omitted} sections omitted)_"
            else:
                text = "\n\n".join(parts)
            result[name] = text
        return result

    def _total_tokens() -> int:
        return sum(_estimate_tokens(t) for t in _build_result().values())

    # Phase 2: progressively add middle paragraphs
    # Find the max number of paragraphs across all docs
    max_paras = max((len(p) for p in doc_paras.values()), default=0)

    # Add paragraphs layer by layer: 2nd paragraph, then 3rd, etc.
    # (index 1, then 2, ... skipping 0 and last which are already included)
    for para_idx in range(1, max_paras - 1):
        if _total_tokens() >= max_tokens:
            break
        for name, paras in doc_paras.items():
            if para_idx >= len(paras) - 1:
                continue  # skip: out of range or is the last paragraph
            if para_idx in included[name]:
                continue  # already included
            # Try adding this paragraph
            included[name] = sorted(set(included[name]) | {para_idx})
            if _total_tokens() > max_tokens:
                # Over budget — remove it, skip this doc but try others
                included[name] = [i for i in included[name] if i != para_idx]
                continue

    result = _build_result()
    total = _total_tokens()
    logger.info(
        "📏 Overview docs: %d modules, ~%dK tokens (budget %dK)",
        len(result),
        total // 1000,
        max_tokens // 1000,
    )
    return result


@dataclass
class OverviewContext:
    config: CodeWikiConfig
    module_tree: Dict[str, Any]
    working_dir: str
    gen_state: Optional[GenerationState] = None
    state_mgr: Optional[GenerationStateManager] = None
    tree_manager: Optional[ModuleTreeManager] = None
    middleware: LLMMiddleware | None = None
    usage_stats: Optional[LLMUsageStats] = None


def strip_tree_for_overview(tree: Dict[str, Any]) -> Dict[str, Any]:
    """Return a lightweight copy with only hierarchy needed for overviews."""
    light: Dict[str, Any] = {}
    for name, info in tree.items():
        entry: Dict[str, Any] = {}
        children = info.get("children")
        if isinstance(children, dict) and children:
            entry["children"] = strip_tree_for_overview(children)
        light[name] = entry
    return light


def build_overview_structure(
    ctx: OverviewContext,
    module_path: List[str],
) -> Dict[str, Any]:
    """Build a lightweight structure for overview generation."""
    module_tree = ctx.module_tree
    working_dir = ctx.working_dir
    gen_state = ctx.gen_state

    if len(module_path) == 0:
        result: Dict[str, Any] = {}
        raw_docs: dict[str, str] = {}
        for name, info in module_tree.items():
            entry: Dict[str, Any] = {"is_target_for_overview_generation": True}
            children = info.get("children")
            if isinstance(children, dict) and children:
                entry["children"] = {cn: {} for cn in children}
            child_path = None
            if gen_state:
                child_doc_id = doc_id_for_path(module_tree, [name])
                child_file = gen_state.get_output_file(child_doc_id)
                if child_file:
                    candidate = os.path.join(working_dir, child_file)
                    if os.path.exists(candidate):
                        child_path = candidate
            if child_path is None:
                child_path = find_module_doc(working_dir, [name])
            if child_path:
                raw_docs[name] = file_manager.load_text(child_path)
            else:
                logger.warning("Module docs not found for [%s]", name)
                raw_docs[name] = ""
            result[name] = entry

        budgeted = _budget_child_docs(
            raw_docs, max(ctx.config.max_input_tokens - _OVERVIEW_OVERHEAD, 50_000)
        )
        for name in result:
            result[name]["docs"] = budgeted.get(name, "")
        return result

    result = strip_tree_for_overview(module_tree)
    node = result
    for i, path_part in enumerate(module_path):
        if path_part not in node:
            node[path_part] = {}
        if i < len(module_path) - 1:
            node = node[path_part].setdefault("children", {})
        else:
            node[path_part]["is_target_for_overview_generation"] = True

    target_original = module_tree
    for p in module_path:
        target_original = target_original[p]
    children = target_original.get("children") or {}
    target_node = node[module_path[-1]]
    target_children = target_node.setdefault("children", {})
    raw_docs: dict[str, str] = {}
    for child_name in children:
        if child_name not in target_children:
            target_children[child_name] = {}
        child_path = None
        if gen_state:
            child_doc_id = doc_id_for_path(module_tree, module_path + [child_name])
            child_file = gen_state.get_output_file(child_doc_id)
            if child_file:
                candidate = os.path.join(working_dir, child_file)
                if os.path.exists(candidate):
                    child_path = candidate
        if child_path is None:
            child_path = find_module_doc(working_dir, module_path + [child_name])
        if child_path:
            raw_docs[child_name] = file_manager.load_text(child_path)
        else:
            logger.warning("Module docs not found for %s", module_path + [child_name])
            raw_docs[child_name] = ""

    budgeted = _budget_child_docs(
        raw_docs, max(ctx.config.max_input_tokens - _OVERVIEW_OVERHEAD, 50_000)
    )
    for child_name in target_children:
        target_children[child_name]["docs"] = budgeted.get(child_name, "")
    return result


def collect_child_doc_hashes(
    ctx: OverviewContext,
    module_path: List[str],
) -> Dict[str, str]:
    """Return content hashes for direct child docs of a module."""
    module_tree = ctx.module_tree
    gen_state = ctx.gen_state
    working_dir = ctx.working_dir

    if not module_path:
        children_dict = module_tree
    else:
        target = module_tree
        for p in module_path:
            target = target[p]
        children_dict = target.get("children") or {}

    hashes: Dict[str, str] = {}
    for child_name in children_dict:
        child_doc_id = doc_id_for_path(module_tree, module_path + [child_name])
        task = gen_state.get_task(child_doc_id) if gen_state else None
        if task and task.content_hash:
            hashes[child_name] = task.content_hash
        elif gen_state is None:
            child_path = find_module_doc(working_dir, module_path + [child_name])
            hashes[child_name] = content_hash(child_path) if child_path else ""
        else:
            hashes[child_name] = ""
    return hashes


async def generate_parent_module_docs(
    ctx: OverviewContext,
    module_path: List[str],
) -> Dict[str, Any]:
    """Generate overview/parent docs from child documentation."""
    module_tree = ctx.module_tree
    working_dir = ctx.working_dir
    gen_state = ctx.gen_state
    state_mgr = ctx.state_mgr
    config = ctx.config

    module_name = (
        module_path[-1] if module_path else os.path.basename(os.path.normpath(config.repo_path))
    )

    if ctx.tree_manager:
        module_tree = await ctx.tree_manager.get_snapshot()
    elif not module_tree:
        module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)
        module_tree = file_manager.load_json(module_tree_path) or {}

    if len(module_path) == 0:
        output_path = os.path.join(working_dir, OVERVIEW_FILENAME)
    else:
        doc_id = doc_id_for_path(module_tree, module_path)
        output_file = gen_state.get_output_file(doc_id) if gen_state else None
        output_path = os.path.join(
            working_dir,
            output_file
            or doc_id_for_path(module_tree, module_path).split("module:", 1)[-1] + ".md",
        )

    child_hashes = collect_child_doc_hashes(
        OverviewContext(
            config=config,
            module_tree=module_tree,
            working_dir=working_dir,
            gen_state=gen_state,
            middleware=ctx.middleware,
        ),
        module_path,
    )
    current_input_hash = hash_mapping(
        child_hashes,
        extra=[config.output_language, "overview-v7", "/".join(module_path)],
    )
    parent_doc_id = (
        "overview:root" if not module_path else doc_id_for_path(module_tree, module_path)
    )

    existing = output_path if os.path.exists(output_path) else None
    if existing and os.path.getsize(existing) > 100:
        parent_task = gen_state.get_task(parent_doc_id) if gen_state else None
        if (
            parent_task
            and parent_task.status == "completed"
            and parent_task.input_hash == current_input_hash
        ):
            logger.debug("✓ Docs already exists at %s (children unchanged)", existing)
            return module_tree
        logger.info("↻ Child docs changed for '%s', regenerating", module_name)

    repo_structure = build_overview_structure(
        OverviewContext(
            config=config,
            module_tree=module_tree,
            working_dir=working_dir,
            gen_state=gen_state,
            middleware=ctx.middleware,
        ),
        module_path,
    )

    prompt = format_overview_prompt(
        name=module_name,
        repo_structure=json.dumps(repo_structure, indent=4),
        is_repo=(len(module_path) == 0),
        output_language=config.output_language,
    )

    try:
        result = await asyncio.to_thread(ctx.middleware.call, prompt)
        parent_docs = result.content

        if "<OVERVIEW>" in parent_docs and "</OVERVIEW>" in parent_docs:
            parent_content = parent_docs.split("<OVERVIEW>")[1].split("</OVERVIEW>")[0].strip()
        else:
            parent_content = parent_docs.strip()
        file_manager.save_text(parent_content, output_path)

        if state_mgr and gen_state and gen_state.get_task(parent_doc_id):
            await state_mgr.mark_completed(
                parent_doc_id,
                content_hash=content_hash(output_path),
                model=config.main_model,
                input_hash=current_input_hash,
            )

        logger.debug("Successfully generated parent documentation for: %s", module_name)
        return module_tree

    except Exception as e:
        logger.error("Error generating parent documentation for %s: %s", module_name, str(e))
        logger.error("Traceback: %s", traceback.format_exc())
        raise
