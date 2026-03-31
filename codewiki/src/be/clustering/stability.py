"""Clustering stability metrics for quality assessment.

Aligned with v3.md section 5.3 L536-539.
"""
import hashlib
from dataclasses import dataclass


@dataclass
class StabilityReport:
    """Comparison metrics between two module trees."""

    member_jaccard: float        # Average Jaccard similarity of module members (0.0-1.0)
    path_stability: float        # Fraction of modules with identical paths (0.0-1.0)
    module_id_consistency: float  # Fraction of module_ids present in both trees (0.0-1.0)
    total_modules_a: int
    total_modules_b: int

    def summary(self) -> str:
        return (
            f"Stability: jaccard={self.member_jaccard:.3f}, "
            f"path={self.path_stability:.3f}, "
            f"id_consistency={self.module_id_consistency:.3f}"
        )

    @property
    def is_stable(self) -> bool:
        """Consider stable if all metrics >= 0.9."""
        return (
            self.member_jaccard >= 0.9
            and self.path_stability >= 0.9
            and self.module_id_consistency >= 0.9
        )


def measure_tree_stability(tree_a: dict, tree_b: dict) -> StabilityReport:
    """Compare two module trees (v1 legacy format) and compute stability metrics.

    Args:
        tree_a: First module tree dict {title: {path, components, children}}
        tree_b: Second module tree dict

    Returns:
        StabilityReport with comparison metrics.
    """
    # Flatten both trees to get all leaf modules
    modules_a = _flatten_tree(tree_a)
    modules_b = _flatten_tree(tree_b)

    # 1. Module ID consistency: how many module_ids appear in both trees?
    # Use a synthetic "module_id" from sorted members hash for comparison.
    ids_a = set(modules_a.keys())
    ids_b = set(modules_b.keys())
    all_ids = ids_a | ids_b
    common_ids = ids_a & ids_b
    id_consistency = len(common_ids) / len(all_ids) if all_ids else 1.0

    # 2. Path stability: among common modules, how many kept the same path?
    path_matches = sum(
        1 for mid in common_ids
        if modules_a[mid]["path"] == modules_b[mid]["path"]
    )
    path_stability = path_matches / len(common_ids) if common_ids else 1.0

    # 3. Member Jaccard: average Jaccard similarity of component sets.
    jaccard_sum = 0.0
    jaccard_count = 0
    for mid in common_ids:
        set_a = set(modules_a[mid]["components"])
        set_b = set(modules_b[mid]["components"])
        union = set_a | set_b
        intersection = set_a & set_b
        if union:
            jaccard_sum += len(intersection) / len(union)
            jaccard_count += 1
    member_jaccard = jaccard_sum / jaccard_count if jaccard_count > 0 else 1.0

    return StabilityReport(
        member_jaccard=member_jaccard,
        path_stability=path_stability,
        module_id_consistency=id_consistency,
        total_modules_a=len(modules_a),
        total_modules_b=len(modules_b),
    )


def _flatten_tree(tree: dict) -> dict[str, dict]:
    """Flatten a v1 module tree into {synthetic_module_id: {path, components}}.

    The synthetic module_id is computed from sorted component members,
    making it stable across runs and independent of title changes.
    Falls back to the module title when the component list is empty.
    """
    result = {}
    for title, info in tree.items():
        if not isinstance(info, dict):
            continue

        components = info.get("components", [])
        path = info.get("path", "")

        # Derive a stable synthetic ID from sorted members.
        # When the component list is empty, fall back to the title so that
        # two trees with identically-named empty modules still match.
        if components:
            key = "|".join(sorted(components))
            mid = hashlib.sha256(key.encode()).hexdigest()[:12]
        else:
            mid = title

        result[mid] = {
            "title": title,
            "path": path,
            "components": list(components),
        }

        # Recurse into children
        children = info.get("children", {})
        if children and isinstance(children, dict):
            result.update(_flatten_tree(children))

    return result
