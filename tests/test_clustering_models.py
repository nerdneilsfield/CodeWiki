"""Tests for codewiki.src.be.clustering.models — TDD Phase 1.

Written BEFORE implementation per TDD workflow (RED phase).
"""
import json
import copy
import pytest

from codewiki.src.be.clustering.models import (
    ModuleMembers,
    ModuleConstraints,
    ModuleNode,
    ModuleTree,
    module_id_from_members,
    canonicalize_tree,
    validate_tree,
    to_legacy_dict,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _leaf(module_id: str, title: str, path: str, components: list[str]) -> ModuleNode:
    """Create a leaf ModuleNode with given components."""
    return ModuleNode(
        module_id=module_id,
        title=title,
        path=path,
        members=ModuleMembers(components=components),
    )


_REQUIRED_EXTRA_MODULES = [
    {"module_id": "getting-started", "title": "Getting Started", "path": "getting-started"},
    {"module_id": "tutorial", "title": "Tutorial", "path": "tutorial"},
    {"module_id": "best-practices", "title": "Best Practices", "path": "best-practices"},
]


def _simple_tree(root_components: list[str] | None = None) -> ModuleTree:
    """Build a minimal valid single-node tree."""
    components = root_components or ["comp_a", "comp_b"]
    root = ModuleNode(
        module_id=module_id_from_members(components),
        title="Root",
        path="",
        members=ModuleMembers(components=components),
        extra_top_level_modules=_REQUIRED_EXTRA_MODULES,
    )
    return ModuleTree(root=root)


# ---------------------------------------------------------------------------
# module_id_from_members
# ---------------------------------------------------------------------------

class TestModuleIdFromMembers:
    def test_module_id_deterministic(self):
        """Same component_ids always produce the same hash."""
        ids = ["comp_a", "comp_b", "comp_c"]
        assert module_id_from_members(ids) == module_id_from_members(ids)

    def test_module_id_different_inputs_different_hash(self):
        """Different component sets produce different hashes."""
        assert module_id_from_members(["comp_a"]) != module_id_from_members(["comp_b"])

    def test_module_id_order_independent(self):
        """Input order does not affect the hash — sorted internally."""
        ids = ["comp_c", "comp_a", "comp_b"]
        shuffled = ["comp_b", "comp_c", "comp_a"]
        assert module_id_from_members(ids) == module_id_from_members(shuffled)

    def test_module_id_length_12(self):
        """Hash is truncated to 12 hex characters."""
        result = module_id_from_members(["comp_x"])
        assert len(result) == 12

    def test_module_id_empty_list(self):
        """Empty component list returns a consistent 12-char hash."""
        result = module_id_from_members([])
        assert len(result) == 12
        assert module_id_from_members([]) == result  # deterministic

    def test_module_id_single_component(self):
        """Single component produces a valid hash."""
        result = module_id_from_members(["only_comp"])
        assert len(result) == 12


# ---------------------------------------------------------------------------
# canonicalize_tree
# ---------------------------------------------------------------------------

class TestCanonicalizeTree:
    def test_canonicalize_sorts_members_components(self):
        """Unsorted members.components are sorted after canonicalize."""
        root = ModuleNode(
            module_id="root",
            title="Root",
            path="",
            members=ModuleMembers(components=["comp_z", "comp_a", "comp_m"]),
            extra_top_level_modules=[],
        )
        tree = ModuleTree(root=root)
        result = canonicalize_tree(tree)
        assert result.root.members.components == ["comp_a", "comp_m", "comp_z"]

    def test_canonicalize_sorts_members_symbols(self):
        """Unsorted members.symbols are sorted after canonicalize."""
        root = ModuleNode(
            module_id="root",
            title="Root",
            path="",
            members=ModuleMembers(
                components=["comp_a"],
                symbols=["sym_z", "sym_a", "sym_b"],
            ),
            extra_top_level_modules=[],
        )
        tree = ModuleTree(root=root)
        result = canonicalize_tree(tree)
        assert result.root.members.symbols == ["sym_a", "sym_b", "sym_z"]

    def test_canonicalize_sorts_members_files(self):
        """Unsorted members.files are sorted after canonicalize."""
        root = ModuleNode(
            module_id="root",
            title="Root",
            path="",
            members=ModuleMembers(
                components=["comp_a"],
                files=["z/file.py", "a/file.py", "m/file.py"],
            ),
            extra_top_level_modules=[],
        )
        tree = ModuleTree(root=root)
        result = canonicalize_tree(tree)
        assert result.root.members.files == ["a/file.py", "m/file.py", "z/file.py"]

    def test_canonicalize_sorts_children_by_module_id(self):
        """Children in wrong module_id order are sorted after canonicalize."""
        child_z = _leaf("zzz_id", "Z Module", "z-module", ["comp_z"])
        child_a = _leaf("aaa_id", "A Module", "a-module", ["comp_a"])
        child_m = _leaf("mmm_id", "M Module", "m-module", ["comp_m"])
        root = ModuleNode(
            module_id="root",
            title="Root",
            path="",
            members=ModuleMembers(),
            children=[child_z, child_a, child_m],
            extra_top_level_modules=[],
        )
        tree = ModuleTree(root=root)
        result = canonicalize_tree(tree)
        ids = [c.module_id for c in result.root.children]
        assert ids == sorted(ids)

    def test_canonicalize_deep_three_levels(self):
        """Three-level nested tree has all levels sorted after canonicalize."""
        grandchild_b = _leaf("b_id", "B", "b", ["b_comp"])
        grandchild_a = _leaf("a_id", "A", "a", ["a_comp"])
        child = ModuleNode(
            module_id="child_id",
            title="Child",
            path="child",
            members=ModuleMembers(components=["child_comp_z", "child_comp_a"]),
            children=[grandchild_b, grandchild_a],
        )
        root = ModuleNode(
            module_id="root",
            title="Root",
            path="",
            members=ModuleMembers(components=["root_z", "root_a"]),
            children=[child],
            extra_top_level_modules=[],
        )
        tree = ModuleTree(root=root)
        result = canonicalize_tree(tree)

        # Level 1: root components sorted
        assert result.root.members.components == ["root_a", "root_z"]

        # Level 2: child components sorted
        child_node = result.root.children[0]
        assert child_node.members.components == ["child_comp_a", "child_comp_z"]

        # Level 3: grandchildren sorted by module_id
        grandchild_ids = [g.module_id for g in child_node.children]
        assert grandchild_ids == sorted(grandchild_ids)

    def test_canonicalize_does_not_mutate_original(self):
        """canonicalize_tree must return a new tree without mutating input."""
        root = ModuleNode(
            module_id="root",
            title="Root",
            path="",
            members=ModuleMembers(components=["z", "a"]),
            extra_top_level_modules=[],
        )
        tree = ModuleTree(root=root)
        original_order = list(tree.root.members.components)
        canonicalize_tree(tree)
        # Original should be unchanged
        assert tree.root.members.components == original_order


# ---------------------------------------------------------------------------
# validate_tree
# ---------------------------------------------------------------------------

def _two_leaf_tree(
    comp_leaf1: list[str],
    comp_leaf2: list[str],
    path1: str = "mod/leaf1",
    path2: str = "mod/leaf2",
    id1: str | None = None,
    id2: str | None = None,
) -> ModuleTree:
    """Build a tree with root containing two leaf children."""
    leaf1 = _leaf(
        id1 or module_id_from_members(comp_leaf1),
        "Leaf1",
        path1,
        comp_leaf1,
    )
    leaf2 = _leaf(
        id2 or module_id_from_members(comp_leaf2),
        "Leaf2",
        path2,
        comp_leaf2,
    )
    root = ModuleNode(
        module_id="root",
        title="Root",
        path="",
        members=ModuleMembers(),
        children=[leaf1, leaf2],
        extra_top_level_modules=[
            {"module_id": "getting-started", "title": "Getting Started", "path": "getting-started"},
            {"module_id": "tutorial", "title": "Tutorial", "path": "tutorial"},
            {"module_id": "best-practices", "title": "Best Practices", "path": "best-practices"},
        ],
    )
    return ModuleTree(root=root)


class TestValidateTree:
    def test_validate_tree_passes_valid(self):
        """Valid tree with all components assigned returns empty error list."""
        tree = _two_leaf_tree(["comp_a", "comp_b"], ["comp_c", "comp_d"])
        errors = validate_tree(tree, {"comp_a", "comp_b", "comp_c", "comp_d"})
        assert errors == []

    def test_validate_tree_detects_duplicate_component(self):
        """Component appearing in 2 modules produces an error."""
        tree = _two_leaf_tree(["comp_a", "comp_shared"], ["comp_shared", "comp_c"])
        errors = validate_tree(tree, {"comp_a", "comp_shared", "comp_c"})
        assert any("comp_shared" in e for e in errors)

    def test_validate_tree_detects_missing_component(self):
        """Component in all_component_ids but not assigned to any module produces an error."""
        tree = _two_leaf_tree(["comp_a"], ["comp_b"])
        errors = validate_tree(tree, {"comp_a", "comp_b", "comp_missing"})
        assert any("comp_missing" in e for e in errors)

    def test_validate_tree_detects_duplicate_path(self):
        """Two modules with the same path produce an error."""
        tree = _two_leaf_tree(["comp_a"], ["comp_b"], path1="dup/path", path2="dup/path")
        errors = validate_tree(tree, {"comp_a", "comp_b"})
        assert any("dup/path" in e for e in errors)

    def test_validate_tree_detects_duplicate_module_id(self):
        """Two modules with the same module_id produce an error."""
        tree = _two_leaf_tree(
            ["comp_a"],
            ["comp_b"],
            id1="same_id_abc",
            id2="same_id_abc",
        )
        errors = validate_tree(tree, {"comp_a", "comp_b"})
        assert any("same_id_abc" in e for e in errors)

    def test_validate_tree_empty_all_components(self):
        """If all_component_ids is empty and tree has no members, validation passes."""
        root = ModuleNode(
            module_id="root",
            title="Root",
            path="",
            members=ModuleMembers(),
            extra_top_level_modules=_REQUIRED_EXTRA_MODULES,
        )
        tree = ModuleTree(root=root)
        errors = validate_tree(tree, set())
        assert errors == []

    def test_validate_tree_empty_extra_top_level_modules_fails(self):
        """Root with empty extra_top_level_modules produces an error.

        Per v3.md L594, check_required_modules verifies required modules exist.
        """
        root = ModuleNode(
            module_id="root",
            title="Root",
            path="",
            members=ModuleMembers(components=["comp_a"]),
            extra_top_level_modules=[],  # empty — should fail
        )
        tree = ModuleTree(root=root)
        errors = validate_tree(tree, {"comp_a"})
        assert any("extra_top_level_modules" in e for e in errors)

    def test_validate_tree_single_valid_leaf(self):
        """Tree with a single leaf containing all components passes validation."""
        comp_ids = ["comp_x", "comp_y", "comp_z"]
        tree = _simple_tree(comp_ids)
        errors = validate_tree(tree, set(comp_ids))
        assert errors == []

    def test_validate_tree_returns_list_type(self):
        """validate_tree always returns a list, never None."""
        tree = _simple_tree()
        result = validate_tree(tree, {"comp_a", "comp_b"})
        assert isinstance(result, list)

    def test_validate_tree_multiple_errors_collected(self):
        """Multiple violations produce multiple errors (not fail-fast)."""
        # Duplicate component AND duplicate path
        tree = _two_leaf_tree(
            ["comp_shared"],
            ["comp_shared"],
            path1="same/path",
            path2="same/path",
        )
        errors = validate_tree(tree, {"comp_shared"})
        assert len(errors) >= 2


# ---------------------------------------------------------------------------
# to_legacy_dict
# ---------------------------------------------------------------------------

class TestToLegacyDict:
    def test_to_legacy_dict_format(self):
        """Legacy dict has 'path', 'components', and 'children' keys."""
        tree = _simple_tree(["comp_a", "comp_b"])
        result = to_legacy_dict(tree)
        # Root node is "Root"
        node = result["Root"]
        assert "path" in node
        assert "components" in node
        assert "children" in node

    def test_to_legacy_dict_uses_title_as_key(self):
        """Dict keys are node.title values."""
        child1 = _leaf("id1", "Auth Module", "modules/auth", ["comp_auth"])
        child2 = _leaf("id2", "DB Module", "modules/db", ["comp_db"])
        root = ModuleNode(
            module_id="root",
            title="Repository",
            path="",
            members=ModuleMembers(),
            children=[child1, child2],
            extra_top_level_modules=[],
        )
        tree = ModuleTree(root=root)
        result = to_legacy_dict(tree)
        assert "Repository" in result
        children = result["Repository"]["children"]
        assert "Auth Module" in children
        assert "DB Module" in children

    def test_to_legacy_dict_components_from_members(self):
        """The 'components' list matches members.components."""
        components = ["comp_x", "comp_y"]
        tree = _simple_tree(components)
        result = to_legacy_dict(tree)
        assert result["Root"]["components"] == components

    def test_to_legacy_dict_path_value(self):
        """The 'path' value matches the node's path field."""
        root = ModuleNode(
            module_id="root",
            title="Root",
            path="docs/root",
            members=ModuleMembers(components=["comp_a"]),
            extra_top_level_modules=[],
        )
        tree = ModuleTree(root=root)
        result = to_legacy_dict(tree)
        assert result["Root"]["path"] == "docs/root"

    def test_to_legacy_dict_nested(self):
        """Tree with children produces correctly nested legacy dict."""
        grandchild = _leaf("gc_id", "GC", "gc/path", ["gc_comp"])
        child = ModuleNode(
            module_id="child_id",
            title="Child",
            path="child/path",
            members=ModuleMembers(components=["child_comp"]),
            children=[grandchild],
        )
        root = ModuleNode(
            module_id="root",
            title="Root",
            path="",
            members=ModuleMembers(),
            children=[child],
            extra_top_level_modules=[],
        )
        tree = ModuleTree(root=root)
        result = to_legacy_dict(tree)
        assert "Child" in result["Root"]["children"]
        assert "GC" in result["Root"]["children"]["Child"]["children"]
        assert result["Root"]["children"]["Child"]["children"]["GC"]["path"] == "gc/path"

    def test_to_legacy_dict_leaf_has_empty_children(self):
        """Leaf nodes have an empty 'children' dict."""
        tree = _simple_tree(["comp_a"])
        result = to_legacy_dict(tree)
        assert result["Root"]["children"] == {}


# ---------------------------------------------------------------------------
# Round-trip serialization
# ---------------------------------------------------------------------------

class TestModuleTreeSerializable:
    def test_module_tree_serializable_round_trip(self):
        """model_dump() -> JSON -> model_validate() round-trip preserves all fields."""
        child = _leaf("child_abc", "子模块 (Child)", "modules/child", ["c1", "c2"])
        root = ModuleNode(
            module_id="root",
            title="Root",
            path="",
            description="Root module",
            aliases=["repo-root"],
            members=ModuleMembers(
                components=["c1", "c2"],
                symbols=["sym1"],
                files=["src/file.py"],
            ),
            evidence_refs=[{"file": "src/init.py", "range": {"start_line": 1, "end_line": 5}}],
            constraints=ModuleConstraints(
                public_api_symbols=["sym1"],
                boundary_edges=[{"from": "c1", "to": "c2", "type": "imports"}],
            ),
            children=[child],
            extra_top_level_modules=[{"module_id": "getting-started", "title": "Getting Started", "path": "getting-started"}],
        )
        tree = ModuleTree(
            schema_version="codewiki.module_tree.v2",
            generated_from={"commit": "abc123", "index_version": "v2"},
            root=root,
        )

        # Serialize to JSON string and back
        dumped = tree.model_dump()
        json_str = json.dumps(dumped)
        restored = ModuleTree.model_validate(json.loads(json_str))

        assert restored.schema_version == tree.schema_version
        assert restored.generated_from == tree.generated_from
        assert restored.root.module_id == tree.root.module_id
        assert restored.root.title == tree.root.title
        assert restored.root.description == tree.root.description
        assert restored.root.aliases == tree.root.aliases
        assert restored.root.members.components == tree.root.members.components
        assert restored.root.members.symbols == tree.root.members.symbols
        assert restored.root.members.files == tree.root.members.files
        assert restored.root.evidence_refs == tree.root.evidence_refs
        assert restored.root.constraints.public_api_symbols == tree.root.constraints.public_api_symbols
        assert restored.root.constraints.boundary_edges == tree.root.constraints.boundary_edges
        assert len(restored.root.children) == 1
        assert restored.root.children[0].module_id == child.module_id
        assert restored.root.extra_top_level_modules == tree.root.extra_top_level_modules

    def test_module_tree_schema_version_default(self):
        """Default schema_version is 'codewiki.module_tree.v2'."""
        root = ModuleNode(
            module_id="root",
            title="Root",
            path="",
            members=ModuleMembers(),
            extra_top_level_modules=[],
        )
        tree = ModuleTree(root=root)
        assert tree.schema_version == "codewiki.module_tree.v2"

    def test_module_node_default_fields(self):
        """ModuleNode can be created with only required fields; defaults are sane."""
        node = ModuleNode(
            module_id="test_id",
            title="Test",
            path="test/path",
            members=ModuleMembers(),
        )
        assert node.description == ""
        assert node.aliases == []
        assert node.evidence_refs == []
        assert node.children == []
        assert node.extra_top_level_modules == []
        assert node.constraints.public_api_symbols == []
        assert node.constraints.boundary_edges == []


# ---------------------------------------------------------------------------
# validate_tree: required modules check
# ---------------------------------------------------------------------------


class TestValidateTreeRequiredModules:
    """Verify validate_tree checks for required extra_top_level_modules."""

    def test_empty_extra_top_level_modules_fails(self):
        root = ModuleNode(
            module_id="root", title="Root", path="",
            members=ModuleMembers(),
            extra_top_level_modules=[],
        )
        tree = ModuleTree(root=root)
        errors = validate_tree(tree, set())
        assert any("extra_top_level_modules" in e for e in errors)

    def test_missing_required_module_fails(self):
        root = ModuleNode(
            module_id="root", title="Root", path="",
            members=ModuleMembers(),
            extra_top_level_modules=[
                {"module_id": "getting-started", "title": "Getting Started", "path": "getting-started"},
                # missing tutorial and best-practices
            ],
        )
        tree = ModuleTree(root=root)
        errors = validate_tree(tree, set())
        assert any("tutorial" in e for e in errors)
        assert any("best-practices" in e for e in errors)

    def test_all_required_modules_present_passes(self):
        root = ModuleNode(
            module_id="root", title="Root", path="",
            members=ModuleMembers(),
            extra_top_level_modules=[
                {"module_id": "getting-started", "title": "Getting Started", "path": "getting-started"},
                {"module_id": "tutorial", "title": "Tutorial", "path": "tutorial"},
                {"module_id": "best-practices", "title": "Best Practices", "path": "best-practices"},
            ],
        )
        tree = ModuleTree(root=root)
        errors = validate_tree(tree, set())
        assert not any("extra_top_level_modules" in e for e in errors)
        assert not any("required" in e.lower() for e in errors)
