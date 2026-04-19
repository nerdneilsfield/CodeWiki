from __future__ import annotations

import pytest

from codewiki.src.be.incremental import (
    HardTriggerReason,
    compute_leaf_change_ratio,
    compute_parent_change_ratio,
    detect_hard_triggers,
    plan_invalidations,
    should_rerun_leaf,
    should_rerun_parent,
)


def test_leaf_change_ratio_no_changes():
    assert (
        compute_leaf_change_ratio(
            new_components={"a", "b", "c"},
            old_components={"a", "b", "c"},
            new_component_hashes={"a": "h", "b": "h", "c": "h"},
            old_component_hashes={"a": "h", "b": "h", "c": "h"},
        )
        == 0.0
    )


def test_leaf_change_ratio_partial():
    assert (
        compute_leaf_change_ratio(
            new_components={"a", "b", "c", "d"},
            old_components={"a", "b", "c", "d"},
            new_component_hashes={"a": "h", "b": "h", "c": "h", "d": "new"},
            old_component_hashes={"a": "h", "b": "h", "c": "h", "d": "old"},
        )
        == 0.25
    )


def test_leaf_change_ratio_added_component_counts_as_change():
    assert (
        compute_leaf_change_ratio(
            new_components={"a", "b", "c", "d"},
            old_components={"a", "b", "c"},
            new_component_hashes={"a": "h", "b": "h", "c": "h", "d": "h"},
            old_component_hashes={"a": "h", "b": "h", "c": "h"},
        )
        == 0.25
    )


def test_should_rerun_leaf_and_parent_thresholds():
    assert should_rerun_leaf(change_ratio=0.20, threshold=0.30) is False
    assert should_rerun_leaf(change_ratio=0.30, threshold=0.30) is True
    assert should_rerun_parent(change_ratio=0.29, threshold=0.30) is False
    assert should_rerun_parent(change_ratio=0.30, threshold=0.30) is True


def test_parent_change_ratio():
    assert compute_parent_change_ratio(
        changed_direct_children=1,
        total_direct_children=3,
    ) == pytest.approx(1 / 3)


def test_detect_hard_triggers():
    triggers = detect_hard_triggers(
        old_children={"A": {"module_id": "a", "title": "A", "path": "a"}},
        new_children={
            "A": {"module_id": "a", "title": "Renamed", "path": "a"},
            "B": {"module_id": "b", "title": "B", "path": "b"},
        },
    )
    assert HardTriggerReason.CHILD_ADDED in triggers
    assert HardTriggerReason.CHILD_TITLE_CHANGED in triggers


def test_plan_invalidations_leaf_and_parent():
    new_tree = {
        "Top": {
            "module_id": "top",
            "title": "Top",
            "path": "top",
            "components": [],
            "children": {
                "LeafA": {
                    "module_id": "leaf-a",
                    "title": "LeafA",
                    "path": "leaf-a",
                    "components": ["a"],
                    "children": {},
                },
                "LeafB": {
                    "module_id": "leaf-b",
                    "title": "LeafB",
                    "path": "leaf-b",
                    "components": ["b"],
                    "children": {},
                },
                "LeafC": {
                    "module_id": "leaf-c",
                    "title": "LeafC",
                    "path": "leaf-c",
                    "components": ["c", "d"],
                    "children": {},
                },
            },
        }
    }
    previous_tree = {
        "Top": {
            "module_id": "top",
            "title": "Top",
            "path": "top",
            "components": [],
            "children": {
                "LeafA": {
                    "module_id": "leaf-a",
                    "title": "LeafA",
                    "path": "leaf-a",
                    "components": ["a"],
                    "children": {},
                },
                "LeafB": {
                    "module_id": "leaf-b",
                    "title": "LeafB",
                    "path": "leaf-b",
                    "components": ["b"],
                    "children": {},
                },
                "LeafC": {
                    "module_id": "leaf-c",
                    "title": "LeafC",
                    "path": "leaf-c",
                    "components": ["c", "d"],
                    "children": {},
                },
            },
        }
    }

    invalidations = plan_invalidations(
        new_tree=new_tree,
        previous_tree=previous_tree,
        new_component_hashes={"a": "1", "b": "1", "c": "new", "d": "new"},
        old_component_hashes={"a": "1", "b": "1", "c": "old", "d": "old"},
        leaf_threshold=0.30,
        parent_threshold=0.30,
    )

    assert "module:leaf-c" in invalidations
    assert "module:top" in invalidations


def test_plan_invalidations_hard_trigger_child_added():
    new_tree = {
        "Top": {
            "module_id": "top",
            "title": "Top",
            "path": "top",
            "components": [],
            "children": {
                "A": {
                    "module_id": "a",
                    "title": "A",
                    "path": "a",
                    "components": ["x"],
                    "children": {},
                },
                "B": {
                    "module_id": "b",
                    "title": "B",
                    "path": "b",
                    "components": ["y"],
                    "children": {},
                },
            },
        }
    }
    old_tree = {
        "Top": {
            "module_id": "top",
            "title": "Top",
            "path": "top",
            "components": [],
            "children": {
                "A": {
                    "module_id": "a",
                    "title": "A",
                    "path": "a",
                    "components": ["x"],
                    "children": {},
                },
            },
        }
    }

    invalidations = plan_invalidations(
        new_tree=new_tree,
        previous_tree=old_tree,
        new_component_hashes={"x": "h", "y": "h"},
        old_component_hashes={"x": "h"},
        leaf_threshold=0.99,
        parent_threshold=0.99,
    )

    assert "module:top" in invalidations
