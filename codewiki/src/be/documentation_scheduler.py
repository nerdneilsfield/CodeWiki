from __future__ import annotations

import asyncio
import inspect
import logging
import os
import random
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

import openai
from pydantic_ai.exceptions import UnexpectedModelBehavior
from tqdm import tqdm

from codewiki.src.be.cache_manager import module_artifact_id, overview_artifact_id
from codewiki.src.be.errors import CancellationError, ErrorCategory, LLMError
from codewiki.src.be.documentation_tree_utils import (
    compute_module_input_hash,
    select_effective_component_ids,
    stable_hash,
)
from codewiki.src.be.parent_segments import (
    compute_assembled_parent_input_hash,
    compute_child_segment_input_hash,
    compute_opening_input_hash,
    compute_overview_input_hash,
    generate_or_assemble_parent_doc,
)
from codewiki.src.be.pipeline import ModuleFailure, ModuleSkip, ModuleSummary
from codewiki.src.be.prompt_template import PROMPT_VERSION
from codewiki.src.utils import doc_id_for_path

logger = logging.getLogger(__name__)

_MAX_RETRY_AFTER = 120.0


def _sleep_with_jitter(base_delay: float) -> None:
    """Sleep for base_delay plus bounded jitter."""
    actual = base_delay + random.uniform(0, base_delay * 0.5)
    time.sleep(actual)


def _parse_retry_after(exc: Exception) -> float | None:
    """Parse and clamp Retry-After from OpenAI rate-limit errors."""
    if not isinstance(exc, openai.RateLimitError):
        return None
    headers = getattr(getattr(exc, "response", None), "headers", {})
    val = headers.get("retry-after") or headers.get("Retry-After")
    if val:
        try:
            seconds = float(val)
        except (ValueError, OverflowError):
            return None
        if not (0 <= seconds < float("inf")):
            return None
        return min(seconds, _MAX_RETRY_AFTER)
    return None


def is_leaf_module(module_info: Dict[str, Any]) -> bool:
    """Check if a module is a leaf module."""
    children = module_info.get("children", {})
    return not children or (isinstance(children, dict) and len(children) == 0)


def get_processing_order(
    module_tree: Dict[str, Any],
    parent_path: Optional[List[str]] = None,
) -> List[tuple[List[str], str]]:
    """Get topological processing order with leaf modules first."""
    if parent_path is None:
        parent_path = []
    processing_order = []

    def collect_modules(tree: Dict[str, Any], path: List[str]):
        for module_name, module_info in tree.items():
            current_path = path + [module_name]
            if (
                module_info.get("children")
                and isinstance(module_info["children"], dict)
                and module_info["children"]
            ):
                collect_modules(module_info["children"], current_path)
                processing_order.append((current_path, module_name))
            else:
                processing_order.append((current_path, module_name))

    collect_modules(module_tree, parent_path)
    return processing_order


def get_processing_levels(
    module_tree: Dict[str, Any],
    parent_path: Optional[List[str]] = None,
) -> List[List[tuple]]:
    """Group modules into dependency-safe levels for parallel processing."""
    if parent_path is None:
        parent_path = []

    node_levels: Dict[str, tuple] = {}

    def assign_levels(tree: Dict[str, Any], path: List[str]):
        for name, info in tree.items():
            current_path = path + [name]
            key = "/".join(current_path)
            children = info.get("children") or {}
            if not children or not isinstance(children, dict):
                node_levels[key] = (0, current_path, name, info)
            else:
                assign_levels(children, current_path)
                child_max = max(
                    node_levels["/".join(current_path + [cn])][0]
                    for cn in children
                    if "/".join(current_path + [cn]) in node_levels
                )
                node_levels[key] = (child_max + 1, current_path, name, info)

    assign_levels(module_tree, parent_path)

    by_level: Dict[int, List[tuple]] = {}
    for _key, (level, path, name, info) in node_levels.items():
        by_level.setdefault(level, []).append((path, name, info))

    return [by_level[i] for i in sorted(by_level.keys())]


async def run_module_queue(
    *,
    config,
    graph_tree: Dict[str, Any],
    components: Dict[str, Any],
    working_dir: str,
    tree_manager,
    process_module: Callable[..., Awaitable[tuple[Dict[str, Any], str]]],
    generate_root_overview: Optional[Callable[[], Awaitable[None]]] = None,
    desc: str = "Generating docs",
    include_root: bool = True,
    cache_manager=None,
    progress_factory: Callable[..., Any] = tqdm,
    cancel_token=None,
    middleware=None,
) -> ModuleSummary:
    ROOT_KEY = "__root__"
    max_concurrent = config.max_concurrent
    summary = ModuleSummary()

    def _structural_snapshot(tree: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, info in tree.items():
            children = info.get("children") or {}
            out[key] = {
                "module_id": info.get("module_id"),
                "path": info.get("path"),
                "_doc_filename": info.get("_doc_filename"),
                "children": _structural_snapshot(children) if isinstance(children, dict) else {},
            }
        return out

    initial_skeleton = _structural_snapshot(graph_tree)

    all_tasks: Dict[str, tuple] = {}
    pending_count: Dict[str, int] = {}
    child_to_parent: Dict[str, str] = {}

    def _walk(tree: Dict[str, Any], parent_path: List[str], parent_key: Optional[str] = None):
        for name, info in tree.items():
            current_path = parent_path + [name]
            key = "/".join(current_path)
            children = info.get("children") or {}
            is_queue_leaf = not children or not isinstance(children, dict)
            all_tasks[key] = (current_path, name, info, is_queue_leaf)
            if parent_key is not None:
                child_to_parent[key] = parent_key
            if not is_queue_leaf:
                pending_count[key] = len(children)
                _walk(children, current_path, parent_key=key)

    _walk(graph_tree, [])
    summary.total = len(all_tasks)

    top_level_keys = list(graph_tree.keys())
    if include_root:
        pending_count[ROOT_KEY] = len(top_level_keys)
        for name in top_level_keys:
            child_to_parent[name] = ROOT_KEY

    work_queue: asyncio.Queue[str] = asyncio.Queue()
    done_queue: asyncio.Queue[tuple[str, bool, bool, str | None]] = asyncio.Queue()
    process_module_params = inspect.signature(process_module).parameters
    accepts_var_kwargs = any(
        param.kind == inspect.Parameter.VAR_KEYWORD for param in process_module_params.values()
    )
    accepts_cache_manager = "cache_manager" in process_module_params or accepts_var_kwargs

    leaf_count = 0
    for key, (path, _, _, is_leaf) in all_tasks.items():
        if is_leaf:
            if cache_manager:
                artifact_id = module_artifact_id(doc_id_for_path(graph_tree, path))
                input_hash = compute_module_input_hash(
                    path[-1],
                    path,
                    all_tasks[key][2],
                    components,
                    config,
                    assigned_file=all_tasks[key][2].get("_doc_filename", ""),
                )
                if cache_manager.is_valid(artifact_id, input_hash):
                    await done_queue.put((key, True, False, None))
                    leaf_count += 1
                    continue
            await work_queue.put(key)
            leaf_count += 1

    total_tasks = len(all_tasks) + (1 if include_root else 0)
    logger.info(
        "📊 Dynamic queue: %s leaf tasks, %s parent tasks%s",
        leaf_count,
        len(all_tasks) - leaf_count,
        ", 1 root overview" if include_root else "",
    )

    # Use a fresh stderr reference — previous tqdm.close() may have left
    # the cached file object in a broken state on some platforms.
    progress = progress_factory(
        total=total_tasks,
        desc=desc,
        unit="module",
        dynamic_ncols=True,
        leave=True,
        file=sys.stderr,
    )

    retry_delays = [10, 30, 90]

    def _jitter(base: float) -> float:
        return base + random.uniform(0, base * 0.5)

    def _is_context_length_error(exc: Exception) -> bool:
        # pydantic-ai's own token limit check
        try:
            from pydantic_ai.exceptions import UsageLimitExceeded

            if isinstance(exc, UsageLimitExceeded):
                return True
        except ImportError:
            pass
        if isinstance(exc, openai.APIStatusError) and exc.status_code == 400:
            msg = str(exc)
            return (
                "input length" in msg
                or "Range of input" in msg
                or "context_length_exceeded" in msg
                or "maximum context length" in msg.lower()
            )
        return False

    def _retry_delay(attempt: int, exc: Exception) -> int:
        if isinstance(exc, LLMError):
            if exc.category == ErrorCategory.RESOURCE_EXHAUSTED:
                return 0
            if not exc.is_retryable:
                return 0
            return retry_delays[attempt - 1]
        is_model_quality = isinstance(exc, UnexpectedModelBehavior) or (
            isinstance(exc, openai.APIStatusError) and exc.status_code == 400
        )
        if is_model_quality:
            return 0
        return retry_delays[attempt - 1]

    async def _coordinator() -> None:
        active_tasks = leaf_count
        while active_tasks > 0:
            key, success, retried, error = await done_queue.get()
            active_tasks -= 1
            progress.update(1)
            if key != ROOT_KEY:
                doc_id = doc_id_for_path(graph_tree, all_tasks[key][0])
                if success:
                    summary.completed.append(doc_id)
                    if retried:
                        summary.retried_then_succeeded.append(doc_id)
                else:
                    summary.failed.append(
                        ModuleFailure(
                            doc_id=doc_id,
                            error=error or "unknown error",
                            retried=retried,
                        )
                    )
            if success:
                parent_key = child_to_parent.get(key)
                if parent_key is not None:
                    pending_count[parent_key] -= 1
                    # 防御性检查：pending_count 不应为负，可能是重复完成导致
                    if pending_count[parent_key] < 0:
                        logger.warning(
                            "pending_count for '%s' went negative — possible duplicate completion",
                            parent_key,
                        )
                        pending_count[parent_key] = 0
                    if pending_count[parent_key] == 0:
                        del pending_count[parent_key]
                        if cache_manager:
                            if parent_key != ROOT_KEY:
                                parent_path, parent_name, parent_info, _ = all_tasks[parent_key]
                                parent_doc_id = doc_id_for_path(graph_tree, parent_path)
                                parent_artifact = module_artifact_id(parent_doc_id)
                                child_keys_for_parent = [
                                    child_key
                                    for child_key, value in child_to_parent.items()
                                    if value == parent_key
                                ]
                                direct_child_pairs: list[tuple[str, str]] = []
                                child_seg_hashes: list[str] = []
                                for child_key in child_keys_for_parent:
                                    child_path, child_name, child_info, _ = all_tasks[child_key]
                                    child_doc_id = doc_id_for_path(graph_tree, child_path)
                                    child_input_hash = cache_manager.get_input_hash(
                                        module_artifact_id(child_doc_id)
                                    ) or compute_module_input_hash(
                                        child_name,
                                        child_path,
                                        child_info,
                                        components,
                                        config,
                                        assigned_file=child_info.get("_doc_filename", ""),
                                    )
                                    direct_child_pairs.append((child_doc_id, child_input_hash))
                                    child_seg_hashes.append(
                                        compute_child_segment_input_hash(
                                            child_module_id=child_doc_id,
                                            child_title=child_info.get("title", child_name),
                                            child_path=child_info.get("path", ""),
                                            child_description=child_info.get("description", ""),
                                            child_input_hash=child_input_hash,
                                            output_language=config.output_language,
                                        )
                                    )

                                parent_hash = compute_assembled_parent_input_hash(
                                    opening_hash=compute_opening_input_hash(
                                        title=parent_info.get("title", parent_name),
                                        path=parent_info.get("path", parent_name),
                                        description=parent_info.get("description", ""),
                                        output_language=config.output_language,
                                    ),
                                    overview_hash=compute_overview_input_hash(
                                        title=parent_info.get("title", parent_name),
                                        path=parent_info.get("path", parent_name),
                                        description=parent_info.get("description", ""),
                                        direct_child_pairs=direct_child_pairs,
                                        output_language=config.output_language,
                                    ),
                                    child_segment_hashes=child_seg_hashes,
                                    output_language=config.output_language,
                                )
                                parent_output = parent_info.get("_doc_filename", "")
                                parent_path_on_disk = (
                                    os.path.join(working_dir, parent_output)
                                    if parent_output
                                    else ""
                                )
                                if (
                                    cache_manager.is_valid(parent_artifact, parent_hash)
                                    and parent_path_on_disk
                                    and os.path.exists(parent_path_on_disk)
                                ):
                                    await done_queue.put((parent_key, True, False, None))
                                    active_tasks += 1
                                    continue
                        if parent_key == ROOT_KEY:
                            logger.info("🔓 All top-level modules done — enqueueing root overview")
                        else:
                            logger.info("🔓 Parent unblocked: %s", all_tasks[parent_key][1])
                        await work_queue.put(parent_key)
                        active_tasks += 1
            done_queue.task_done()
            if cancel_token and cancel_token.is_cancelled:
                logger.info("⏹ Scheduler cancelled — stopping work queue")
                break
        unresolved_keys = list(pending_count.keys())
        if unresolved_keys:
            unresolved_labels = [
                "overview" if key == ROOT_KEY else all_tasks[key][1] for key in unresolved_keys
            ]
            for key in unresolved_keys:
                if key == ROOT_KEY:
                    continue
                summary.skipped.append(
                    ModuleSkip(
                        doc_id=doc_id_for_path(graph_tree, all_tasks[key][0]),
                        reason="dependency failed",
                    )
                )
            progress.update(len(unresolved_keys))
            logger.warning(
                "Skipping %s task(s) because dependencies failed: %s",
                len(unresolved_labels),
                ", ".join(unresolved_labels[:5]) + ("..." if len(unresolved_labels) > 5 else ""),
            )

    async def _worker(_worker_id: int):
        while True:
            if cancel_token and cancel_token.is_cancelled:
                return
            try:
                key = await work_queue.get()
            except asyncio.CancelledError:
                return
            if cancel_token and cancel_token.is_cancelled:
                work_queue.task_done()
                return
            label = "overview" if key == ROOT_KEY else all_tasks[key][1]
            success = False
            retried = False
            error_message = None
            try:
                progress.set_postfix_str(label, refresh=False)
                task_t0 = asyncio.get_running_loop().time()
                task_models_used = ""
                last_exc = None
                for attempt in range(len(retry_delays) + 1):
                    if attempt > 0:
                        retried = True
                        if last_exc is None:
                            raise RuntimeError("retry loop exited without capturing an exception")
                        delay = _retry_delay(attempt, last_exc)
                        logger.warning(
                            "  ↻ Retrying '%s'%s (attempt %s/%s) after: %s",
                            label,
                            f" in {delay}s" if delay else " immediately",
                            attempt,
                            len(retry_delays),
                            last_exc,
                        )
                        if delay:
                            retry_after = _parse_retry_after(last_exc)
                            actual_delay = (
                                retry_after if retry_after is not None else _jitter(delay)
                            )
                            if cancel_token:
                                cancel_token.check()
                                # Cancel-aware sleep：每秒检查一次取消信号，避免长时间阻塞
                                remaining = actual_delay
                                while remaining > 0:
                                    await asyncio.sleep(min(1.0, remaining))
                                    remaining -= 1.0
                                    if cancel_token.is_cancelled:
                                        raise CancellationError(
                                            "Operation cancelled during retry wait"
                                        )
                            else:
                                await asyncio.sleep(actual_delay)
                    try:
                        if cancel_token:
                            cancel_token.check()
                        if attempt > 0:
                            logger.info(
                                "  ▶ Retrying '%s' now (attempt %s/%s)",
                                label,
                                attempt,
                                len(retry_delays),
                            )
                        if key == ROOT_KEY:
                            if cache_manager:
                                cache_manager.mark_running("overview:root")
                            if generate_root_overview:
                                await generate_root_overview()
                            task_models_used = config.main_model
                        else:
                            path, name, info, _ = all_tasks[key]
                            task_doc_id = doc_id_for_path(graph_tree, path)
                            task_artifact_id = module_artifact_id(task_doc_id)
                            task_component_ids = select_effective_component_ids(info, components)
                            if cache_manager:
                                cache_manager.mark_running(task_artifact_id)
                            if info.get("children"):
                                if middleware is None:
                                    raise RuntimeError(
                                        "run_module_queue requires middleware= when the tree contains parent nodes"
                                    )
                                assembly = await generate_or_assemble_parent_doc(
                                    parent_doc_id=info.get("module_id") or task_doc_id,
                                    parent_node=info,
                                    working_dir=working_dir,
                                    cache_dir=cache_manager.cache_dir,
                                    cache_manager=cache_manager,
                                    middleware=middleware,
                                    cluster_model=config.cluster_model,
                                    output_language=config.output_language,
                                )
                                task_input_hash = assembly.input_hash
                                task_models_used = assembly.model
                                output_path = assembly.output_path
                            else:
                                task_input_hash = compute_module_input_hash(
                                    name,
                                    path,
                                    info,
                                    components,
                                    config,
                                    assigned_file=info.get("_doc_filename", ""),
                                )
                                process_args = (
                                    name,
                                    components,
                                    task_component_ids,
                                    path,
                                    working_dir,
                                    tree_manager,
                                )
                                process_kwargs = {}
                                if accepts_cache_manager and cache_manager is not None:
                                    process_kwargs["cache_manager"] = cache_manager
                                _, task_models_used = await process_module(
                                    *process_args, **process_kwargs
                                )
                                output_file = info.get("_doc_filename", "")
                                output_path = (
                                    os.path.join(working_dir, output_file) if output_file else ""
                                )
                            if cache_manager:
                                output_file = info.get("_doc_filename", "")
                                cache_manager.mark_done(
                                    task_artifact_id,
                                    input_hash=task_input_hash,
                                    output_path=output_path,
                                    model=task_models_used,
                                    output_file=output_file,
                                )
                        last_exc = None
                        success = True
                        break
                    except CancellationError:
                        raise  # never retry cancellation
                    except Exception as exc:
                        last_exc = exc
                        if _is_context_length_error(exc):
                            logger.warning(
                                "  ✗ '%s' prompt exceeds model context limit — skipping retries",
                                label,
                            )
                            break
                        # 不可重试的 LLM 错误直接退出重试循环
                        if isinstance(exc, LLMError) and not exc.is_retryable:
                            logger.warning(
                                "  ✗ '%s' non-retryable error — skipping retries: %s",
                                label,
                                exc,
                            )
                            break

                if last_exc is not None:
                    raise last_exc

                task_elapsed = asyncio.get_running_loop().time() - task_t0
                model_suffix = f" (model: {task_models_used})" if task_models_used else ""
                logger.info("✓ Task '%s' completed in %.1fs%s", label, task_elapsed, model_suffix)

            except CancellationError:
                logger.info("⏹ Task '%s' cancelled — resetting to ready", label)
                if cache_manager:
                    cancelled_artifact_id = (
                        "overview:root"
                        if key == ROOT_KEY
                        else module_artifact_id(doc_id_for_path(graph_tree, all_tasks[key][0]))
                    )
                    entry = cache_manager.get_entry(cancelled_artifact_id)
                    if entry and entry.status == "running":
                        cache_manager.invalidate(cancelled_artifact_id)
            except Exception as e:
                logger.error("✗ Failed to process '%s' after all retries: %s", label, e)
                logger.error(traceback.format_exc())
                if cache_manager:
                    failed_artifact_id = (
                        "overview:root"
                        if key == ROOT_KEY
                        else module_artifact_id(doc_id_for_path(graph_tree, all_tasks[key][0]))
                    )
                    cache_manager.mark_failed(failed_artifact_id, str(e))
                error_message = str(e)
            finally:
                await done_queue.put(
                    (key, success, retried, error_message if not success else None)
                )
                work_queue.task_done()

    workers = [asyncio.create_task(_worker(i)) for i in range(max_concurrent)]
    coordinator = asyncio.create_task(_coordinator())
    try:
        await coordinator
        if not (cancel_token and cancel_token.is_cancelled):
            await work_queue.join()
    finally:
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        if not coordinator.done():
            coordinator.cancel()
        await asyncio.gather(coordinator, return_exceptions=True)
        try:
            progress.close()
        except (ValueError, OSError):
            pass  # stderr may already be closed (e.g. during shutdown)
    assert _structural_snapshot(graph_tree) == initial_skeleton, (
        "module_tree was mutated during scheduling — Plan 2 forbids this"
    )
    return summary


async def fill_missing_module_docs(
    *,
    config,
    working_dir: str,
    components: Dict[str, Any],
    tree_manager,
    run_module_queue: Callable[..., Awaitable[ModuleSummary]],
    module_doc_exists: Callable[..., bool],
    cache_manager=None,
    cancel_token=None,
) -> ModuleSummary:
    """Retry missing module docs using the same dependency-aware queue."""

    def _retry_names(tree: Dict[str, Any], path: List[str]) -> List[str]:
        names: List[str] = []
        for name, info in tree.items():
            module_path = path + [name]
            if cache_manager is None:
                if not module_doc_exists(working_dir, module_path, tree):
                    names.append("-".join(module_path))
            else:
                doc_id = doc_id_for_path(tree, module_path)
                entry = cache_manager.get_entry(module_artifact_id(doc_id))
                if entry is None:
                    logger.warning(
                        "fill_pass: encountered tree node %r with no cache entry — this should not happen with a frozen tree",
                        doc_id,
                    )
                elif entry.status in ("failed", "stale", "missing", "running"):
                    names.append("-".join(module_path))
            children = info.get("children") or {}
            if children:
                names.extend(_retry_names(children, module_path))
        return names

    summary = ModuleSummary()
    for attempt in range(config.max_retries):
        if cancel_token and cancel_token.is_cancelled:
            logger.info("⏹ Fill pass skipped (cancelled)")
            return summary
        module_tree = await tree_manager.get_snapshot()
        retry_names = _retry_names(module_tree, [])
        missing_count = len(retry_names)
        if missing_count == 0:
            return summary
        logger.warning(
            "↩ Fill pass %s/%s: %s module(s) without docs — %s%s",
            attempt + 1,
            config.max_retries,
            missing_count,
            ", ".join(retry_names[:5]),
            "..." if len(retry_names) > 5 else "",
        )
        batch_summary = await run_module_queue(
            graph_tree=module_tree,
            components=components,
            working_dir=working_dir,
            tree_manager=tree_manager,
            desc=f"Fill pass {attempt + 1}/{config.max_retries}",
            include_root=False,
            cache_manager=cache_manager,
        )
        summary.extend(batch_summary)
    return summary
