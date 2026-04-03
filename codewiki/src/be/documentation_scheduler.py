from __future__ import annotations

import asyncio
import logging
import random
import traceback
from typing import Any, Awaitable, Callable, Dict, List, Optional

import openai
from pydantic_ai.exceptions import UnexpectedModelBehavior
from tqdm import tqdm

from codewiki.src.be.llm_services import _MAX_RETRY_AFTER
from codewiki.src.utils import doc_id_for_path

logger = logging.getLogger(__name__)


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
) -> None:
    ROOT_KEY = "__root__"
    max_concurrent = config.max_concurrent

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

    top_level_keys = list(graph_tree.keys())
    if include_root:
        pending_count[ROOT_KEY] = len(top_level_keys)
        for name in top_level_keys:
            child_to_parent[name] = ROOT_KEY

    work_queue: asyncio.Queue[str] = asyncio.Queue()
    done_queue: asyncio.Queue[tuple[str, bool]] = asyncio.Queue()

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

    def _get_retry_after(exc: Exception) -> float | None:
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
        is_model_quality = isinstance(exc, UnexpectedModelBehavior) or (
            isinstance(exc, openai.APIStatusError) and exc.status_code == 400
        )
        if is_model_quality:
            return 0
        return retry_delays[attempt - 1]

    async def _coordinator() -> None:
        active_tasks = leaf_count
        while active_tasks > 0:
            key, success = await done_queue.get()
            active_tasks -= 1
            progress.update(1)
            if success:
                parent_key = child_to_parent.get(key)
                if parent_key is not None:
                    pending_count[parent_key] -= 1
                    if pending_count[parent_key] == 0:
                        del pending_count[parent_key]
                        if parent_key == ROOT_KEY:
                            logger.info("🔓 All top-level modules done — enqueueing root overview")
                        else:
                            logger.info("🔓 Parent unblocked: %s", all_tasks[parent_key][1])
                        await work_queue.put(parent_key)
                        active_tasks += 1
            done_queue.task_done()
        unresolved_keys = list(pending_count.keys())
        if unresolved_keys:
            unresolved_labels = [
                "overview" if key == ROOT_KEY else all_tasks[key][1] for key in unresolved_keys
            ]
            progress.update(len(unresolved_keys))
            logger.warning(
                "Skipping %s task(s) because dependencies failed: %s",
                len(unresolved_labels),
                ", ".join(unresolved_labels[:5]) + ("..." if len(unresolved_labels) > 5 else ""),
            )

    async def _worker(_worker_id: int):
        while True:
            try:
                key = await work_queue.get()
            except asyncio.CancelledError:
                return
            label = "overview" if key == ROOT_KEY else all_tasks[key][1]
            success = False
            try:
                progress.set_postfix_str(label, refresh=False)
                task_t0 = asyncio.get_event_loop().time()
                task_models_used = ""
                last_exc = None
                for attempt in range(len(retry_delays) + 1):
                    if attempt > 0:
                        assert last_exc is not None
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
                            retry_after = _get_retry_after(last_exc)
                            actual_delay = (
                                retry_after if retry_after is not None else _jitter(delay)
                            )
                            await asyncio.sleep(actual_delay)
                    try:
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
                    except Exception as exc:
                        last_exc = exc
                        if _is_context_length_error(exc):
                            logger.warning(
                                "  ✗ '%s' prompt exceeds model context limit — skipping retries",
                                label,
                            )
                            break

                if last_exc is not None:
                    raise last_exc

                task_elapsed = asyncio.get_event_loop().time() - task_t0
                model_suffix = f" (model: {task_models_used})" if task_models_used else ""
                logger.info("✓ Task '%s' completed in %.1fs%s", label, task_elapsed, model_suffix)

            except Exception as e:
                logger.error("✗ Failed to process '%s' after all retries: %s", label, e)
                logger.error(traceback.format_exc())
                if state_mgr:
                    failed_doc_id = "overview:root"
                    if key != ROOT_KEY:
                        failed_doc_id = doc_id_for_path(graph_tree, all_tasks[key][0])
                    await state_mgr.mark_failed(failed_doc_id, str(e))
            finally:
                await done_queue.put((key, success))
                work_queue.task_done()

    workers = [asyncio.create_task(_worker(i)) for i in range(max_concurrent)]
    coordinator = asyncio.create_task(_coordinator())
    try:
        await coordinator
        await work_queue.join()
    finally:
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        if not coordinator.done():
            coordinator.cancel()
        await asyncio.gather(coordinator, return_exceptions=True)
        progress.close()


async def fill_missing_module_docs(
    *,
    config,
    working_dir: str,
    components: Dict[str, Any],
    tree_manager,
    run_module_queue: Callable[..., Awaitable[None]],
    module_doc_exists: Callable[..., bool],
    gen_state=None,
) -> None:
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

    for attempt in range(config.max_retries):
        module_tree = await tree_manager.get_snapshot()
        missing_count = _count_missing(module_tree, [])
        if missing_count == 0:
            return
        missing_names = _missing_names(module_tree, [])
        logger.warning(
            "↩ Fill pass %s/%s: %s module(s) without docs — %s%s",
            attempt + 1,
            config.max_retries,
            missing_count,
            ", ".join(missing_names[:5]),
            "..." if len(missing_names) > 5 else "",
        )
        await run_module_queue(
            graph_tree=module_tree,
            components=components,
            working_dir=working_dir,
            tree_manager=tree_manager,
            desc=f"Fill pass {attempt + 1}/{config.max_retries}",
            include_root=False,
            gen_state=gen_state,
        )
