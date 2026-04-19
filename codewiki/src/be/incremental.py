"""Incremental change ratios and rerun decisions.

Pure logic, no I/O. See spec §Incremental Change Propagation.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from codewiki.src.be.cache_manager import module_artifact_id


class HardTriggerReason(str, Enum):
    CHILD_ADDED = "child_added"
    CHILD_REMOVED = "child_removed"
    CHILD_TITLE_CHANGED = "child_title_changed"
    CHILD_PATH_CHANGED = "child_path_changed"
    CHILD_IDENTITY_LOST = "child_identity_lost"


def compute_leaf_change_ratio(
    *,
    new_components: set[str],
    old_components: set[str],
    new_component_hashes: dict[str, str],
    old_component_hashes: dict[str, str],
) -> float:
    """Return the fraction of current leaf components that changed."""
    if not new_components:
        return 0.0

    changed = 0
    for component_id in new_components:
        if component_id not in old_components:
            changed += 1
            continue
        if new_component_hashes.get(component_id, "") != old_component_hashes.get(component_id, ""):
            changed += 1

    for component_id in old_components - new_components:
        if component_id not in new_components:
            changed += 1

    return min(changed / len(new_components), 1.0)


def should_rerun_leaf(*, change_ratio: float, threshold: float) -> bool:
    return change_ratio >= threshold


def compute_parent_change_ratio(
    *,
    changed_direct_children: int,
    total_direct_children: int,
) -> float:
    if total_direct_children <= 0:
        return 0.0
    return changed_direct_children / total_direct_children


def should_rerun_parent(*, change_ratio: float, threshold: float) -> bool:
    return change_ratio >= threshold


def _child_index(children: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for info in children.values():
        module_id = info.get("module_id")
        if module_id:
            by_id[str(module_id)] = info
    return by_id


def detect_hard_triggers(
    *,
    old_children: dict[str, Any],
    new_children: dict[str, Any],
) -> set[HardTriggerReason]:
    """Detect structural changes that bypass the ratio threshold."""
    reasons: set[HardTriggerReason] = set()
    old_by_id = _child_index(old_children or {})
    new_by_id = _child_index(new_children or {})

    old_ids = set(old_by_id)
    new_ids = set(new_by_id)
    added = new_ids - old_ids
    removed = old_ids - new_ids
    if added:
        reasons.add(HardTriggerReason.CHILD_ADDED)
    if removed:
        reasons.add(HardTriggerReason.CHILD_REMOVED)

    for module_id in old_ids & new_ids:
        old = old_by_id[module_id]
        new = new_by_id[module_id]
        if old.get("title") != new.get("title"):
            reasons.add(HardTriggerReason.CHILD_TITLE_CHANGED)
        if old.get("path") != new.get("path"):
            reasons.add(HardTriggerReason.CHILD_PATH_CHANGED)

    return reasons


def plan_invalidations(
    *,
    new_tree: dict,
    previous_tree: dict,
    new_component_hashes: dict[str, str],
    old_component_hashes: dict[str, str],
    leaf_threshold: float,
    parent_threshold: float,
) -> list[str]:
    """Return module artifact ids that should be invalidated for this run."""
    invalidations: list[str] = []
    seen: set[str] = set()

    def _add(artifact_id: str) -> None:
        if artifact_id in seen:
            return
        seen.add(artifact_id)
        invalidations.append(artifact_id)

    def _walk(new_subtree: dict[str, Any], old_subtree: dict[str, Any]) -> bool:
        node_invalidated = False
        for key in sorted(new_subtree):
            new_node = new_subtree[key] or {}
            old_node = old_subtree.get(key, {}) if isinstance(old_subtree, dict) else {}
            module_id = new_node.get("module_id")
            if not module_id:
                continue

            new_children = new_node.get("children") or {}
            old_children = old_node.get("children") or {}
            if isinstance(new_children, dict) and new_children:
                changed_direct_children = 0
                for child_key in sorted(new_children):
                    child_new = new_children[child_key] or {}
                    child_old = (
                        old_children.get(child_key, {}) if isinstance(old_children, dict) else {}
                    )
                    if _walk({child_key: child_new}, {child_key: child_old}):
                        changed_direct_children += 1

                hard_triggers = detect_hard_triggers(
                    old_children=old_children if isinstance(old_children, dict) else {},
                    new_children=new_children,
                )
                ratio = compute_parent_change_ratio(
                    changed_direct_children=changed_direct_children,
                    total_direct_children=len(new_children),
                )
                if hard_triggers or should_rerun_parent(
                    change_ratio=ratio,
                    threshold=parent_threshold,
                ):
                    _add(module_artifact_id(str(module_id)))
                    node_invalidated = True
                continue

            new_components = set(new_node.get("components") or [])
            old_components = set(old_node.get("components") or [])
            ratio = compute_leaf_change_ratio(
                new_components=new_components,
                old_components=old_components,
                new_component_hashes=new_component_hashes,
                old_component_hashes=old_component_hashes,
            )
            if should_rerun_leaf(change_ratio=ratio, threshold=leaf_threshold):
                _add(module_artifact_id(str(module_id)))
                node_invalidated = True

        return node_invalidated

    _walk(new_tree or {}, previous_tree or {})
    return invalidations
