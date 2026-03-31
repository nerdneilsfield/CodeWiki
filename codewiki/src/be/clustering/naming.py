"""LLM-constrained naming for clusters. v2: LLM naming with heuristic fallback."""
import logging
from collections import Counter
from typing import Any

try:
    from codewiki.src.be.llm_services import call_llm
except ImportError:  # pragma: no cover — may not be available in tests without full stack
    call_llm = None  # type: ignore[assignment]

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


def _build_naming_prompt(
    clusters: list[list[str]],
    component_file_map: dict[str, str],
    components: dict[str, Any] | None = None,
) -> str:
    """Build constrained prompt for LLM naming.

    v3.md L337-338: LLM generates title + description per cluster.
    LLM does NOT rearrange members.
    """
    lines = [
        "You are naming pre-formed code clusters. Each cluster is already fixed.",
        "",
        "CONSTRAINT: Do NOT add, remove, or move components between clusters.",
        "You must name every cluster exactly as listed — do not skip any.",
        "",
        "For each cluster, provide:",
        '  - "title": Use format: 中文名 (English Name)',
        '  - "description": One sentence describing the cluster\'s purpose.',
        "",
        "Clusters to name:",
    ]

    for idx, cluster in enumerate(clusters):
        lines.append(f"\nCluster {idx}:")
        # Group members by directory for readability
        by_dir: dict[str, list[str]] = {}
        for cid in cluster:
            file_path = component_file_map.get(cid, "")
            dir_part = file_path.rsplit("/", 1)[0] if "/" in file_path else "(root)"
            by_dir.setdefault(dir_part, []).append(cid)

        for dir_path, members in sorted(by_dir.items()):
            lines.append(f"  [{dir_path}]")
            for cid in members:
                lines.append(f"    - {cid}")

    lines += [
        "",
        "Return ONLY a JSON array with one object per cluster, in this exact schema:",
        '[{"cluster_idx": 0, "title": "...", "description": "..."}, ...]',
        "",
        f"There are {len(clusters)} clusters (indices 0 to {len(clusters) - 1}).",
        "Return exactly that many objects, no more, no less.",
    ]

    return "\n".join(lines)


def _name_clusters_with_llm(
    clusters: list[list[str]],
    component_file_map: dict[str, str],
    config: Any,
    components: dict[str, Any] | None = None,
) -> list[dict] | None:
    """Call LLM for cluster naming. Returns None on failure.

    Uses json_repair.loads for robust JSON parsing of LLM output.
    Validates that the response is a list with the correct number of items
    and correct cluster_idx values.
    """
    import json_repair

    prompt = _build_naming_prompt(clusters, component_file_map, components)

    try:
        raw = call_llm(prompt, config, model=config.cluster_model)
    except Exception as e:
        logger.warning("call_llm raised during cluster naming: %s", e)
        return None

    try:
        parsed = json_repair.loads(raw)
    except Exception as e:
        logger.warning("JSON parse failed for cluster naming response: %s", e)
        return None

    if not isinstance(parsed, list):
        logger.warning(
            "LLM naming response is not a list (got %s)", type(parsed).__name__
        )
        return None

    if len(parsed) != len(clusters):
        logger.warning(
            "LLM naming returned %d items for %d clusters", len(parsed), len(clusters)
        )
        return None

    # Validate each entry has required keys, correct cluster_idx,
    # non-empty title/description, and bilingual title format
    for i, entry in enumerate(parsed):
        if not isinstance(entry, dict):
            logger.warning("LLM naming entry %d is not a dict", i)
            return None
        title = entry.get("title", "")
        description = entry.get("description", "")
        if not title or not description:
            logger.warning("LLM naming entry %d has empty title or description", i)
            return None
        if entry.get("cluster_idx") != i:
            logger.warning(
                "LLM naming entry %d has wrong cluster_idx %r", i, entry.get("cluster_idx")
            )
            return None
        # Validate bilingual title: should contain "(" for "中文名 (English Name)" format
        # If LLM returns monolingual title, still accept but log warning
        if "(" not in title and not _is_cjk_only(title):
            logger.info(
                "LLM naming entry %d title lacks bilingual format: %r", i, title
            )

    return parsed


def _is_cjk_only(text: str) -> bool:
    """Check if text is predominantly CJK characters (no need for bilingual parens)."""
    cjk_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf')
    return cjk_count > len(text) * 0.5


def name_clusters(
    clusters: list[list[str]],
    component_file_map: dict[str, str],
    config: Any = None,
    components: dict[str, Any] | None = None,
) -> list[dict]:
    """Name clusters using LLM with heuristic fallback.

    Tries LLM naming first (if config provided with cluster_model).
    Falls back to heuristic naming on any failure.

    Args:
        clusters: list of component_id lists (from partitioner)
        component_file_map: component_id -> relative_path
        config: Config with cluster_model field (optional)
        components: full component dict for richer context (optional)

    Returns:
        list of {"cluster_idx": int, "title": str, "description": str}
    """
    # Try LLM naming when config and cluster_model are available
    if config is not None and getattr(config, "cluster_model", None):
        try:
            result = _name_clusters_with_llm(
                clusters, component_file_map, config, components
            )
            if result is not None and len(result) == len(clusters):
                return result
        except Exception as e:
            logger.warning("LLM naming failed, using heuristic: %s", e)

    # Heuristic fallback
    results = []
    for idx, cluster in enumerate(clusters):
        title, description = heuristic_cluster_name(cluster, component_file_map)
        results.append({
            "cluster_idx": idx,
            "title": title,
            "description": description,
        })
    return results
