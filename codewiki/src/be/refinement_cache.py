"""Helpers for refinement cache artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

from codewiki.src.be.documentation_tree_utils import stable_hash
from codewiki.src.be.prompt_template import REFINEMENT_PROMPT_VERSION
from codewiki.src.config import REFINEMENT_DIR

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_MAX_NORMALIZED_LEN = 120


def refinement_artifact_id(doc_id: str) -> str:
    """Return the canonical cache artifact id for a refinement subtree."""
    if doc_id.startswith("refinement:"):
        return doc_id
    return f"refinement:{doc_id}"


def normalized_doc_id(doc_id: str) -> str:
    """Return a filesystem-safe identifier for a document id."""
    lowered = doc_id.lower()
    cleaned = _NORMALIZE_RE.sub("_", lowered).strip("_")
    return cleaned[:_MAX_NORMALIZED_LEN]


def refinement_output_path(cache_dir: str, doc_id: str) -> str:
    """Return the absolute JSON path for a refinement subtree payload."""
    return os.path.join(cache_dir, REFINEMENT_DIR, f"{normalized_doc_id(doc_id)}.json")


def _component_source_code(component: Any) -> str:
    if component is None:
        return ""
    if isinstance(component, dict):
        return str(component.get("source_code", "") or "")
    return str(getattr(component, "source_code", "") or "")


def compute_refinement_input_hash(
    *,
    component_ids: list[str],
    components: dict[str, Any],
    current_depth: int,
    max_depth: int,
    min_components_for_split: int,
    min_distinct_files_for_split: int,
    max_cluster_components: int,
    identity_reuse_threshold: float,
    output_language: str,
) -> str:
    """Return the SHA256 digest for the refinement cache invalidation inputs."""
    component_hashes: list[str] = []
    for component_id in sorted(component_ids):
        component_hashes.append(
            hashlib.sha256(
                _component_source_code(components.get(component_id)).encode("utf-8")
            ).hexdigest()
        )
    return stable_hash(
        [
            *sorted(component_ids),
            *component_hashes,
            str(current_depth),
            str(max_depth),
            str(min_components_for_split),
            str(min_distinct_files_for_split),
            str(max_cluster_components),
            f"{identity_reuse_threshold:.4f}",
            output_language,
            REFINEMENT_PROMPT_VERSION,
        ]
    )


def save_refinement_payload(cache_dir: str, doc_id: str, payload: dict[str, Any]) -> str:
    """Persist a refinement subtree payload to disk and return its path."""
    path = refinement_output_path(cache_dir, doc_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
    return path


def load_refinement_payload(cache_dir: str, doc_id: str) -> dict[str, Any] | None:
    """Load a refinement subtree payload if it exists and is valid JSON."""
    path = refinement_output_path(cache_dir, doc_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def load_previous_children(cache_dir: str, doc_id: str) -> dict[str, Any]:
    """Return the children mapping from the previous refinement payload."""
    payload = load_refinement_payload(cache_dir, doc_id)
    if not payload:
        return {}
    return payload.get("children", {}) or {}
