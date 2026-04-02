"""Clustering v2 data models and tree utilities.

Aligned with v3.md sections 5.2 (schema) and 5.4 (VALIDATE_TREE pseudocode).
"""

import copy
import hashlib
from typing import Optional

from pydantic import BaseModel


class TreeValidationError(Exception):
    """Raised when validate_tree finds invariant violations."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"Tree validation failed with {len(errors)} error(s): {errors}")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ModuleMembers(BaseModel):
    """Members of a module cluster."""

    components: list[str] = []  # component_ids, sorted lexicographically
    symbols: list[str] = []  # symbol_ids derived from components, sorted
    files: list[str] = []  # unique file paths, sorted


class ModuleConstraints(BaseModel):
    """Constraints and boundary information for a module."""

    public_api_symbols: list[str] = []
    boundary_edges: list[dict] = []  # {"from": str, "to": str, "type": str}


class ModuleNode(BaseModel):
    """A node in the module tree."""

    module_id: str  # stable hash from component_ids
    title: str  # display name, bilingual
    path: str  # document path, unique and predictable
    description: str = ""
    aliases: list[str] = []  # old names / synonyms
    members: ModuleMembers = ModuleMembers()
    evidence_refs: list[dict] = []  # simplified SourceRange dicts
    constraints: ModuleConstraints = ModuleConstraints()
    children: list["ModuleNode"] = []
    extra_top_level_modules: list[dict] = []  # only used on root node


class ModuleTree(BaseModel):
    """Top-level container for the module tree."""

    schema_version: str = "codewiki.module_tree.v2"
    generated_from: dict = {}  # {"commit": str, "index_version": str}
    root: ModuleNode  # single root, children inside


# ---------------------------------------------------------------------------
# Helper: stable module_id
# ---------------------------------------------------------------------------


def module_id_from_members(component_ids: list[str]) -> str:
    """Generate a stable module_id from sorted component_ids.

    Uses SHA-256 of the pipe-joined sorted list, truncated to 12 hex chars.
    This is deterministic and order-independent (v3.md L525).
    """
    key = "|".join(sorted(component_ids))
    return hashlib.sha256(key.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Helper: canonicalize_tree
# ---------------------------------------------------------------------------


def _canonicalize_node(node: ModuleNode) -> ModuleNode:
    """Return a new ModuleNode with sorted members and children (recursive)."""
    sorted_members = ModuleMembers(
        components=sorted(node.members.components),
        symbols=sorted(node.members.symbols),
        files=sorted(node.members.files),
    )
    sorted_children = sorted(
        [_canonicalize_node(c) for c in node.children],
        key=lambda n: n.module_id,
    )
    return ModuleNode(
        module_id=node.module_id,
        title=node.title,
        path=node.path,
        description=node.description,
        aliases=list(node.aliases),
        members=sorted_members,
        evidence_refs=list(node.evidence_refs),
        constraints=ModuleConstraints(
            public_api_symbols=list(node.constraints.public_api_symbols),
            boundary_edges=list(node.constraints.boundary_edges),
        ),
        children=sorted_children,
        extra_top_level_modules=list(node.extra_top_level_modules),
    )


def canonicalize_tree(tree: ModuleTree) -> ModuleTree:
    """Return a new tree with all members sorted and children sorted by module_id.

    Does not mutate the input tree (immutable pattern, v3.md L528, L347).
    """
    return ModuleTree(
        schema_version=tree.schema_version,
        generated_from=dict(tree.generated_from),
        root=_canonicalize_node(tree.root),
    )


# ---------------------------------------------------------------------------
# Helper: validate_tree
# ---------------------------------------------------------------------------


def _collect_nodes(node: ModuleNode) -> list[ModuleNode]:
    """Collect all nodes in the tree via DFS."""
    result = [node]
    for child in node.children:
        result.extend(_collect_nodes(child))
    return result


def validate_tree(tree: ModuleTree, all_component_ids: set[str]) -> list[str]:
    """Validate tree invariants per v3.md L588-600.

    Returns a list of error strings; empty list means the tree is valid.

    Checks:
    1. Every component appears in at most one module (no duplicates).
    2. Every component from all_component_ids is assigned to some module.
    3. All path fields are unique across the entire tree.
    4. All module_id fields are unique across the entire tree.
    5. Tree has no cycles (children don't reference ancestors).
    6. extra_top_level_modules exists on root (can be empty list).
    """
    errors: list[str] = []
    all_nodes = _collect_nodes(tree.root)

    # --- Check 1: no duplicate components across modules ---
    seen_components: dict[str, str] = {}  # component_id -> module_id first seen
    for node in all_nodes:
        for comp in node.members.components:
            if comp in seen_components:
                errors.append(
                    f"Duplicate component '{comp}' found in module '{node.module_id}' "
                    f"and '{seen_components[comp]}'"
                )
            else:
                seen_components[comp] = node.module_id

    # --- Check 2: all expected components are assigned ---
    assigned = set(seen_components.keys())
    for comp in sorted(all_component_ids):  # sorted for deterministic error order
        if comp not in assigned:
            errors.append(f"Component '{comp}' is not assigned to any module")

    # --- Check 3: unique paths ---
    seen_paths: dict[str, str] = {}  # path -> module_id first seen
    for node in all_nodes:
        if node.path in seen_paths:
            errors.append(
                f"Duplicate path '{node.path}' found in module '{node.module_id}' "
                f"and '{seen_paths[node.path]}'"
            )
        else:
            seen_paths[node.path] = node.module_id

    # --- Check 4: unique module_ids ---
    seen_ids: dict[str, str] = {}  # module_id -> first title seen
    for node in all_nodes:
        if node.module_id in seen_ids:
            errors.append(
                f"Duplicate module_id '{node.module_id}' found in node titled "
                f"'{node.title}' (first seen as '{seen_ids[node.module_id]}')"
            )
        else:
            seen_ids[node.module_id] = node.title

    # --- Check 5: no cycles (DFS with ancestor tracking) ---
    def _check_cycles(node: ModuleNode, ancestor_ids: set[str]) -> list[str]:
        cycle_errors: list[str] = []
        for child in node.children:
            if child.module_id in ancestor_ids:
                cycle_errors.append(
                    f"Cycle detected: module '{child.module_id}' is both ancestor and child"
                )
            else:
                cycle_errors.extend(_check_cycles(child, ancestor_ids | {child.module_id}))
        return cycle_errors

    errors.extend(_check_cycles(tree.root, {tree.root.module_id}))

    # --- Check 6: required top-level modules exist (v3.md L594) ---
    _REQUIRED_MODULE_IDS = {"getting-started", "tutorial", "best-practices"}
    if not tree.root.extra_top_level_modules:
        errors.append(
            "Root node must have non-empty extra_top_level_modules "
            f"(required: {sorted(_REQUIRED_MODULE_IDS)})"
        )
    else:
        present_ids = {m.get("module_id", "") for m in tree.root.extra_top_level_modules}
        missing = _REQUIRED_MODULE_IDS - present_ids
        if missing:
            errors.append(f"Missing required top-level modules: {sorted(missing)}")

    return errors


# ---------------------------------------------------------------------------
# Helper: to_legacy_dict
# ---------------------------------------------------------------------------


def _node_to_legacy(node: ModuleNode) -> dict:
    """Recursively convert a ModuleNode to v1-compatible dict."""
    return {
        "path": node.path,
        "components": list(node.members.components),
        "children": {child.title: _node_to_legacy(child) for child in node.children},
    }


def to_legacy_dict(tree: ModuleTree) -> dict:
    """Convert ModuleTree to v1-compatible dict format.

    Output shape:
    {
        "module_title": {
            "path": "...",
            "components": ["comp_id_1", ...],
            "children": { ... recursive ... }
        }
    }

    Uses node.title as key, node.members.components as components list
    (v3.md: members.components directly maps to legacy "components" field).
    """
    return {tree.root.title: _node_to_legacy(tree.root)}
