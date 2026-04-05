from __future__ import annotations

import asyncio
import logging
import random
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

import openai
from pydantic_ai.exceptions import UnexpectedModelBehavior
from tqdm import tqdm

from codewiki.src.be.errors import CancellationError, ErrorCategory, LLMError
from codewiki.src.be.pipeline import ModuleFailure, ModuleSkip, ModuleSummary
from codewiki.src.be.documentation_tree_utils import stable_hash
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
    gen_state=None,
    state_mgr=None,
    progress_factory: Callable[..., Any] = tqdm,
    cancel_token=None,
) -> ModuleSummary:
    ROOT_KEY = "__root__"
    max_concurrent = config.max_concurrent
    summary = ModuleSummary()

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

    leaf_count = 0
    for key, (_, _, _, is_leaf) in all_tasks.items():
        if is_leaf:
            await work_queue.put(key)
            leaf_count += 1

    total_tasks = len(all_tasks) + (1 if include_root else 0)
    logger.info(
        "📊 Dynamic queue: %s leaf tasks, %s parent tasks%s",
        leaf_count,
        len(all_tasks) - leaf_count,
        ", 1 root overview" if include_root else "",
    )

    progress = progress_factory(
        total=total_tasks,
        desc=desc,
        unit="module",
        dynamic_ncols=True,
        leave=True,
    )

    retry_delays = [10, 30, 90]

    def _jitter(base: float) -> float:
        return base + random.uniform(0, base * 0.5)

    def _is_context_length_error(exc: Exception) -> bool:
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
                        if gen_state and state_mgr:
                            # 通过 state_mgr 的锁保护对 gen_state 的读取，避免并发竞态
                            stale_update: dict[str, str] | None = None
                            async with state_mgr._lock:
                                parent_doc_id = (
                                    "overview:root"
                                    if parent_key == ROOT_KEY
                                    else doc_id_for_path(graph_tree, all_tasks[parent_key][0])
                                )
                                parent_task = gen_state.get_task(parent_doc_id)
                                if parent_task:
                                    if parent_key == ROOT_KEY:
                                        parent_components: list[str] = []
                                        child_keys = [
                                            key
                                            for key, value in child_to_parent.items()
                                            if value == ROOT_KEY
                                        ]
                                    else:
                                        _, _, parent_info, _ = all_tasks[parent_key]
                                        parent_components = sorted(
                                            parent_info.get("components", [])
                                        )
                                        child_keys = [
                                            key
                                            for key, value in child_to_parent.items()
                                            if value == parent_key
                                        ]
                                    child_doc_ids = [
                                        "overview:root"
                                        if child_key == ROOT_KEY
                                        else doc_id_for_path(graph_tree, all_tasks[child_key][0])
                                        for child_key in child_keys
                                    ]
                                    child_content_hashes = []
                                    for child_doc_id in child_doc_ids:
                                        child_task = gen_state.get_task(child_doc_id)
                                        if child_task and child_task.content_hash:
                                            child_content_hashes.append(child_task.content_hash)
                                    new_hash = stable_hash(
                                        [
                                            *parent_components,
                                            *child_doc_ids,
                                            *child_content_hashes,
                                            parent_task.language,
                                            "v7",
                                        ]
                                    )
                                    if (
                                        parent_task.status == "completed"
                                        and new_hash != parent_task.input_hash
                                    ):
                                        # mark_stale 内部也会获取锁，先记录，释放锁后再调用避免死锁
                                        stale_update = {parent_doc_id: new_hash}
                                    elif parent_task.status != "completed":
                                        parent_task.input_hash = new_hash
                            # mark_stale 自带锁，放在 async with 外面避免死锁
                            if stale_update is not None:
                                await state_mgr.mark_stale(stale_update)
                        if parent_key == ROOT_KEY:
                            logger.info("🔓 All top-level modules done — enqueueing root overview")
                        else:
                            logger.info("🔓 Parent unblocked: %s", all_tasks[parent_key][1])
                        await work_queue.put(parent_key)
                        active_tasks += 1
            if state_mgr:
                await state_mgr.flush()
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
                        if key == ROOT_KEY:
                            if state_mgr:
                                await state_mgr.mark_running("overview:root")
                            if generate_root_overview:
                                await generate_root_overview()
                            task_models_used = config.main_model
                        else:
                            path, name, info, _ = all_tasks[key]
                            if state_mgr and gen_state:
                                await state_mgr.mark_running(doc_id_for_path(graph_tree, path))
                            _, task_models_used = await process_module(
                                name,
                                components,
                                info.get("components", []),
                                path,
                                working_dir,
                                tree_manager,
                                gen_state=gen_state,
                                state_mgr=state_mgr,
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
                # 取消后将 task 状态从 running 重置为 ready，避免状态停留在 running
                if state_mgr and gen_state:
                    cancelled_doc_id = (
                        "overview:root"
                        if key == ROOT_KEY
                        else doc_id_for_path(graph_tree, all_tasks[key][0])
                    )
                    task_obj = gen_state.get_task(cancelled_doc_id)
                    if task_obj and task_obj.status == "running":
                        task_obj.status = "ready"
                        task_obj.updated_at = datetime.now(timezone.utc).isoformat()
            except Exception as e:
                logger.error("✗ Failed to process '%s' after all retries: %s", label, e)
                logger.error(traceback.format_exc())
                if state_mgr:
                    failed_doc_id = "overview:root"
                    if key != ROOT_KEY:
                        failed_doc_id = doc_id_for_path(graph_tree, all_tasks[key][0])
                    await state_mgr.mark_failed(failed_doc_id, str(e))
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
        progress.close()
    return summary


async def fill_missing_module_docs(
    *,
    config,
    working_dir: str,
    components: Dict[str, Any],
    tree_manager,
    run_module_queue: Callable[..., Awaitable[ModuleSummary]],
    module_doc_exists: Callable[..., bool],
    gen_state=None,
    cancel_token=None,
) -> ModuleSummary:
    """Retry missing module docs using the same dependency-aware queue."""

    def _count_missing(tree: Dict[str, Any], path: List[str]) -> int:
        count = 0
        for name, info in tree.items():
            module_path = path + [name]
            if not module_doc_exists(working_dir, module_path, tree, gen_state):
                count += 1
            children = info.get("children") or {}
            if children:
                count += _count_missing(children, module_path)
        return count

    def _missing_names(tree: Dict[str, Any], path: List[str]) -> List[str]:
        names: List[str] = []
        for name, info in tree.items():
            module_path = path + [name]
            if not module_doc_exists(working_dir, module_path, tree, gen_state):
                names.append("-".join(module_path))
            children = info.get("children") or {}
            if children:
                names.extend(_missing_names(children, module_path))
        return names

    summary = ModuleSummary()
    for attempt in range(config.max_retries):
        if cancel_token and cancel_token.is_cancelled:
            logger.info("⏹ Fill pass skipped (cancelled)")
            return summary
        module_tree = await tree_manager.get_snapshot()
        missing_count = _count_missing(module_tree, [])
        if missing_count == 0:
            return summary
        missing_names = _missing_names(module_tree, [])
        logger.warning(
            "↩ Fill pass %s/%s: %s module(s) without docs — %s%s",
            attempt + 1,
            config.max_retries,
            missing_count,
            ", ".join(missing_names[:5]),
            "..." if len(missing_names) > 5 else "",
        )
        batch_summary = await run_module_queue(
            graph_tree=module_tree,
            components=components,
            working_dir=working_dir,
            tree_manager=tree_manager,
            desc=f"Fill pass {attempt + 1}/{config.max_retries}",
            include_root=False,
            gen_state=gen_state,
        )
        summary.extend(batch_summary)
    return summary
