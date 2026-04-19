"""Identity reuse matching helpers for refinement and clustering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class IdentityMatch:
    old_key: str
    old_module_id: str
    old_title: str
    old_path: str
    overlap: float
    margin: float


def match_overlap(new_components: set[str], old_components: set[str]) -> float:
    """Return new-normalized overlap: |new ∩ old| / |new|."""
    if not new_components:
        return 0.0
    return len(new_components & old_components) / len(new_components)


def find_dominant_match(
    new_components: set[str],
    old_siblings: dict[str, Any],
    threshold: float,
    margin: float,
) -> IdentityMatch | None:
    if not old_siblings or not new_components:
        return None

    scored: list[tuple[float, str, dict[str, Any]]] = []
    for old_key, old_info in old_siblings.items():
        old_components = set(old_info.get("components") or [])
        if not old_components:
            continue
        if new_components == old_components:
            return IdentityMatch(
                old_key=old_key,
                old_module_id=old_info.get("module_id", ""),
                old_title=old_info.get("title", old_key),
                old_path=old_info.get("path", ""),
                overlap=1.0,
                margin=1.0,
            )
        scored.append((match_overlap(new_components, old_components), old_key, old_info))

    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    best_overlap, best_key, best_info = scored[0]
    second_overlap = scored[1][0] if len(scored) > 1 else 0.0
    delta = best_overlap - second_overlap
    if best_overlap < threshold or delta < margin:
        return None

    return IdentityMatch(
        old_key=best_key,
        old_module_id=best_info.get("module_id", ""),
        old_title=best_info.get("title", best_key),
        old_path=best_info.get("path", ""),
        overlap=best_overlap,
        margin=delta,
    )


def _split_overlap(new_components: set[str], old_components: set[str]) -> float:
    """Return old-normalized overlap: |new ∩ old| / |old|."""
    if not old_components:
        return 0.0
    return len(new_components & old_components) / len(old_components)


def find_split_successor(
    old_components: set[str],
    new_groups: dict[str, Any],
    threshold: float,
    margin: float,
) -> str | None:
    if not old_components or not new_groups:
        return None

    scored: list[tuple[float, str]] = []
    for new_key, new_info in new_groups.items():
        new_components = set(new_info.get("components") or [])
        if not new_components:
            continue
        scored.append((_split_overlap(new_components, old_components), new_key))

    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    best_overlap, best_key = scored[0]
    second_overlap = scored[1][0] if len(scored) > 1 else 0.0
    if best_overlap < threshold or best_overlap - second_overlap < margin:
        return None
    return best_key


def find_merge_predecessor(
    new_components: set[str],
    old_siblings: dict[str, Any],
    threshold: float,
    margin: float,
) -> IdentityMatch | None:
    """Merge direction uses the same new-normalized overlap as dominant matching."""
    return find_dominant_match(new_components, old_siblings, threshold, margin)
