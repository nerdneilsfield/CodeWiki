"""LLM-constrained naming for clusters. v2 first version: naming only, no boundary adjustment."""
import logging
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)


def heuristic_cluster_name(
    cluster_components: list[str],
    component_file_map: dict[str, str],
) -> tuple[str, str]:
    """Generate a fallback name from directory structure.

    Returns: (title, description)
    - title: most common directory name + component type summary
    - description: "Contains N components in <directories>"
    """
    if not cluster_components:
        return "Unnamed Module", "Contains 0 components"

    dirs = []
    for cid in cluster_components:
        path = component_file_map.get(cid, "")
        parts = path.split("/")
        if len(parts) > 1:
            dirs.append(parts[0] if len(parts) <= 2 else "/".join(parts[:2]))

    if dirs:
        most_common_dir = Counter(dirs).most_common(1)[0][0]
        title = most_common_dir.replace("/", " ").replace("_", " ").title()
    else:
        title = f"Module ({len(cluster_components)} components)"

    description = f"Contains {len(cluster_components)} components"
    return title, description


def name_clusters(
    clusters: list[list[str]],
    component_file_map: dict[str, str],
    config: Any = None,
) -> list[dict]:
    """Name clusters. Uses heuristic naming (LLM naming deferred to future version).

    For v2 first version, we use heuristic naming only. LLM naming will be added
    when the pipeline is stable and we can validate the constrained prompt.

    Args:
        clusters: list of component_id lists (from partitioner)
        component_file_map: component_id -> relative_path
        config: Config (unused in v2 first version, reserved for LLM naming)

    Returns:
        list of {"cluster_idx": int, "title": str, "description": str}
    """
    results = []
    for idx, cluster in enumerate(clusters):
        title, description = heuristic_cluster_name(cluster, component_file_map)
        results.append({
            "cluster_idx": idx,
            "title": title,
            "description": description,
        })
    return results
