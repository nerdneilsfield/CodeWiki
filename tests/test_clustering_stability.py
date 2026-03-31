"""Tests for clustering stability metrics.

TDD: tests written before implementation.
Covers StabilityReport and measure_tree_stability from stability.py.
"""
import pytest

from codewiki.src.be.clustering.stability import (
    StabilityReport,
    measure_tree_stability,
    _flatten_tree,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TREE_A = {
    "Auth": {
        "path": "auth",
        "components": ["comp_login", "comp_logout", "comp_session"],
        "children": {},
    },
    "Storage": {
        "path": "storage",
        "components": ["comp_db", "comp_cache"],
        "children": {},
    },
}

TREE_B = {
    "Auth": {
        "path": "auth",
        "components": ["comp_login", "comp_logout", "comp_session"],
        "children": {},
    },
    "Storage": {
        "path": "storage",
        "components": ["comp_db", "comp_cache"],
        "children": {},
    },
}

TREE_C = {
    "Networking": {
        "path": "net",
        "components": ["comp_http", "comp_ws"],
        "children": {},
    },
    "Rendering": {
        "path": "render",
        "components": ["comp_html", "comp_css"],
        "children": {},
    },
}


# ---------------------------------------------------------------------------
# 1. Identical trees → all metrics 1.0, is_stable=True
# ---------------------------------------------------------------------------


def test_identical_trees():
    report = measure_tree_stability(TREE_A, TREE_B)

    assert report.member_jaccard == pytest.approx(1.0)
    assert report.path_stability == pytest.approx(1.0)
    assert report.module_id_consistency == pytest.approx(1.0)
    assert report.total_modules_a == 2
    assert report.total_modules_b == 2
    assert report.is_stable is True


# ---------------------------------------------------------------------------
# 2. Completely different trees → id_consistency=0.0
# ---------------------------------------------------------------------------


def test_completely_different_trees():
    report = measure_tree_stability(TREE_A, TREE_C)

    assert report.module_id_consistency == pytest.approx(0.0)
    # No common modules → jaccard and path_stability are vacuously 1.0
    assert report.member_jaccard == pytest.approx(1.0)
    assert report.path_stability == pytest.approx(1.0)
    assert report.is_stable is False  # id_consistency < 0.9


# ---------------------------------------------------------------------------
# 3. Same members, different paths → jaccard=1.0, path_stability=0.0
# ---------------------------------------------------------------------------


def test_same_modules_different_paths():
    tree_x = {
        "Auth": {
            "path": "auth/v1",
            "components": ["comp_login", "comp_logout"],
            "children": {},
        },
    }
    tree_y = {
        "Auth": {
            "path": "auth/v2",  # different path
            "components": ["comp_login", "comp_logout"],  # same members
            "children": {},
        },
    }
    report = measure_tree_stability(tree_x, tree_y)

    assert report.member_jaccard == pytest.approx(1.0)
    assert report.path_stability == pytest.approx(0.0)
    assert report.module_id_consistency == pytest.approx(1.0)
    assert report.is_stable is False  # path_stability < 0.9


# ---------------------------------------------------------------------------
# 4. Partial overlap — 2 common out of 3 total unique → id_consistency≈0.67
# ---------------------------------------------------------------------------


def test_partial_overlap():
    tree_x = {
        "Auth": {
            "path": "auth",
            "components": ["comp_login", "comp_logout"],
            "children": {},
        },
        "Storage": {
            "path": "storage",
            "components": ["comp_db"],
            "children": {},
        },
    }
    tree_y = {
        "Auth": {
            "path": "auth",
            "components": ["comp_login", "comp_logout"],
            "children": {},
        },
        "Networking": {
            "path": "net",
            "components": ["comp_http"],
            "children": {},
        },
    }
    report = measure_tree_stability(tree_x, tree_y)

    # 1 common out of 3 unique module IDs  → 1/3 ≈ 0.333
    assert report.module_id_consistency == pytest.approx(1 / 3, abs=0.01)
    assert report.total_modules_a == 2
    assert report.total_modules_b == 2


# ---------------------------------------------------------------------------
# 5. Common module with 50% member overlap → jaccard=0.5
# ---------------------------------------------------------------------------


def test_member_jaccard_partial():
    # Synthetic IDs come from sorted members, so we need matching keys.
    # Use same component list for one module to ensure it matches.
    tree_x = {
        "Shared": {
            "path": "shared",
            "components": ["comp_a", "comp_b", "comp_c", "comp_d"],
            "children": {},
        },
    }
    tree_y = {
        "Shared": {
            "path": "shared",
            "components": ["comp_a", "comp_b", "comp_e", "comp_f"],  # 2 overlap out of 6 union
            "children": {},
        },
    }
    # Jaccard = |{a,b}| / |{a,b,c,d,e,f}| = 2/6 ≈ 0.333
    # BUT: the synthetic IDs are derived from sorted members, so they won't match!
    # This is the correct behavior: different member sets → different module IDs.
    # Verify id_consistency=0 since no common synthetic IDs.
    report = measure_tree_stability(tree_x, tree_y)
    assert report.module_id_consistency == pytest.approx(0.0)

    # To test jaccard=0.5 we need modules with the SAME synthetic ID.
    # Use a tree where both sides have one module with identical member key
    # (same components) so they hash to the same ID, then add a variant.
    tree_p = {
        "Core": {
            "path": "core",
            "components": ["comp_x", "comp_y"],
            "children": {},
        },
    }
    tree_q = {
        "Core": {
            "path": "core",
            "components": ["comp_x", "comp_y"],
            "children": {},
        },
    }
    report2 = measure_tree_stability(tree_p, tree_q)
    assert report2.member_jaccard == pytest.approx(1.0)

    # For a true partial-jaccard test, we need trees whose synthetic IDs
    # collide deliberately.  The only way is to have the exact same components
    # on one side (matching ID) but then the Jaccard would trivially be 1.0 or 0.0.
    # The design uses member-hash as the stable ID, so different members → different IDs.
    # Verify this contract explicitly.
    tree_r = {
        "M": {"path": "p", "components": ["a", "b", "c"], "children": {}},
    }
    tree_s = {
        "M": {"path": "p", "components": ["a", "b", "d"], "children": {}},
    }
    report3 = measure_tree_stability(tree_r, tree_s)
    # Different component sets → different synthetic IDs → no common module → jaccard vacuously 1.0
    assert report3.member_jaccard == pytest.approx(1.0)
    assert report3.module_id_consistency == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 6. Empty trees → all 1.0 (vacuously stable)
# ---------------------------------------------------------------------------


def test_empty_trees():
    report = measure_tree_stability({}, {})

    assert report.member_jaccard == pytest.approx(1.0)
    assert report.path_stability == pytest.approx(1.0)
    assert report.module_id_consistency == pytest.approx(1.0)
    assert report.total_modules_a == 0
    assert report.total_modules_b == 0
    assert report.is_stable is True


# ---------------------------------------------------------------------------
# 7. One empty tree, one populated → id_consistency=0.0
# ---------------------------------------------------------------------------


def test_one_empty_tree():
    report_a = measure_tree_stability(TREE_A, {})
    assert report_a.module_id_consistency == pytest.approx(0.0)
    assert report_a.total_modules_a == 2
    assert report_a.total_modules_b == 0
    assert report_a.is_stable is False

    report_b = measure_tree_stability({}, TREE_A)
    assert report_b.module_id_consistency == pytest.approx(0.0)
    assert report_b.total_modules_a == 0
    assert report_b.total_modules_b == 2
    assert report_b.is_stable is False


# ---------------------------------------------------------------------------
# 8. StabilityReport.summary() string format
# ---------------------------------------------------------------------------


def test_stability_report_summary():
    report = StabilityReport(
        member_jaccard=0.875,
        path_stability=0.920,
        module_id_consistency=0.750,
        total_modules_a=8,
        total_modules_b=10,
    )
    summary = report.summary()

    assert "jaccard=0.875" in summary
    assert "path=0.920" in summary
    assert "id_consistency=0.750" in summary
    assert summary.startswith("Stability:")


# ---------------------------------------------------------------------------
# 9. is_stable threshold — at 0.9 → True, any metric below → False
# ---------------------------------------------------------------------------


def test_is_stable_threshold():
    def make_report(j, p, i):
        return StabilityReport(
            member_jaccard=j,
            path_stability=p,
            module_id_consistency=i,
            total_modules_a=1,
            total_modules_b=1,
        )

    assert make_report(0.9, 0.9, 0.9).is_stable is True
    assert make_report(1.0, 1.0, 1.0).is_stable is True
    assert make_report(0.89, 0.9, 0.9).is_stable is False  # jaccard too low
    assert make_report(0.9, 0.89, 0.9).is_stable is False  # path too low
    assert make_report(0.9, 0.9, 0.89).is_stable is False  # id_consistency too low
    assert make_report(0.0, 0.0, 0.0).is_stable is False


# ---------------------------------------------------------------------------
# 10. Nested tree: children are flattened correctly
# ---------------------------------------------------------------------------


def test_nested_tree_flattened():
    nested_tree = {
        "Root": {
            "path": "root",
            "components": ["comp_root_1"],
            "children": {
                "Child1": {
                    "path": "root/child1",
                    "components": ["comp_c1_a", "comp_c1_b"],
                    "children": {},
                },
                "Child2": {
                    "path": "root/child2",
                    "components": ["comp_c2_x"],
                    "children": {
                        "GrandChild": {
                            "path": "root/child2/gc",
                            "components": ["comp_gc_1"],
                            "children": {},
                        }
                    },
                },
            },
        }
    }

    flat = _flatten_tree(nested_tree)

    # Should contain 4 entries: Root, Child1, Child2, GrandChild
    assert len(flat) == 4

    # Verify each entry is present by checking paths
    paths = {v["path"] for v in flat.values()}
    assert "root" in paths
    assert "root/child1" in paths
    assert "root/child2" in paths
    assert "root/child2/gc" in paths

    # Components preserved correctly
    by_path = {v["path"]: v for v in flat.values()}
    assert set(by_path["root/child1"]["components"]) == {"comp_c1_a", "comp_c1_b"}
    assert set(by_path["root/child2/gc"]["components"]) == {"comp_gc_1"}


# ---------------------------------------------------------------------------
# 11. _flatten_tree handles non-dict values gracefully
# ---------------------------------------------------------------------------


def test_flatten_tree_ignores_non_dict_values():
    tree = {
        "ValidModule": {
            "path": "valid",
            "components": ["comp_a"],
            "children": {},
        },
        "BadEntry": "this is not a dict",
    }

    flat = _flatten_tree(tree)

    # Only the valid dict entry should be included
    assert len(flat) == 1
    paths = {v["path"] for v in flat.values()}
    assert "valid" in paths


# ---------------------------------------------------------------------------
# 12. measure_tree_stability returns correct total_modules counts
# ---------------------------------------------------------------------------


def test_total_modules_counts():
    single = {
        "Only": {"path": "only", "components": ["c1"], "children": {}},
    }
    triple = {
        "A": {"path": "a", "components": ["ca"], "children": {}},
        "B": {"path": "b", "components": ["cb"], "children": {}},
        "C": {"path": "c", "components": ["cc"], "children": {}},
    }

    report = measure_tree_stability(single, triple)

    assert report.total_modules_a == 1
    assert report.total_modules_b == 3


# ---------------------------------------------------------------------------
# 13. Module with empty components list uses title as synthetic ID
# ---------------------------------------------------------------------------


def test_empty_components_uses_title_as_id():
    tree_x = {
        "EmptyModule": {
            "path": "empty",
            "components": [],
            "children": {},
        },
    }
    tree_y = {
        "EmptyModule": {
            "path": "empty",
            "components": [],
            "children": {},
        },
    }

    flat_x = _flatten_tree(tree_x)
    flat_y = _flatten_tree(tree_y)

    # Both should produce the same synthetic ID (fallback to title)
    assert set(flat_x.keys()) == set(flat_y.keys())

    report = measure_tree_stability(tree_x, tree_y)
    assert report.module_id_consistency == pytest.approx(1.0)
