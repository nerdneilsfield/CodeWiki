from codewiki.src.be.identity_reuse import (
    IdentityMatch,
    find_dominant_match,
    find_merge_predecessor,
    find_split_successor,
    match_overlap,
)


def test_match_overlap_full_overlap():
    assert match_overlap({"a", "b", "c"}, {"a", "b", "c"}) == 1.0


def test_match_overlap_partial():
    assert match_overlap({"a", "b", "c", "d"}, {"a", "b", "x", "y"}) == 0.5


def test_match_overlap_zero():
    assert match_overlap({"a"}, {"b"}) == 0.0


def test_match_overlap_empty_new_returns_zero():
    assert match_overlap(set(), {"a"}) == 0.0


def test_identity_match_dataclass_fields():
    match = IdentityMatch(
        old_key="auth_layer",
        old_module_id="auth_layer",
        old_title="Auth Layer",
        old_path="auth_layer",
        overlap=0.85,
        margin=0.30,
    )
    assert match.old_key == "auth_layer"
    assert match.overlap == 0.85
    assert match.margin == 0.30


def test_find_dominant_match_exact_set():
    new = {"a.py::A", "b.py::B"}
    old_siblings = {
        "Auth": {
            "module_id": "auth",
            "title": "Auth",
            "path": "auth",
            "components": ["a.py::A", "b.py::B"],
            "children": {},
        },
        "Other": {
            "module_id": "other",
            "title": "Other",
            "path": "other",
            "components": ["x.py::X"],
            "children": {},
        },
    }
    match = find_dominant_match(new, old_siblings, threshold=0.70, margin=0.15)
    assert match is not None
    assert match.old_key == "Auth"
    assert match.overlap == 1.0


def test_find_dominant_match_dominant_overlap():
    new = {"a.py::A", "b.py::B", "c.py::C", "d.py::D"}
    old_siblings = {
        "Auth": {
            "module_id": "auth",
            "title": "Auth",
            "path": "auth",
            "components": ["a.py::A", "b.py::B", "c.py::C"],
            "children": {},
        },
        "Other": {
            "module_id": "other",
            "title": "Other",
            "path": "other",
            "components": ["x.py::X"],
            "children": {},
        },
    }
    match = find_dominant_match(new, old_siblings, threshold=0.70, margin=0.15)
    assert match is not None
    assert match.old_key == "Auth"


def test_find_dominant_match_below_threshold():
    new = {"a", "b", "c", "d"}
    old_siblings = {
        "Auth": {
            "module_id": "auth",
            "title": "Auth",
            "path": "auth",
            "components": ["a"],
            "children": {},
        },
    }
    assert find_dominant_match(new, old_siblings, threshold=0.70, margin=0.15) is None


def test_find_dominant_match_two_close_candidates_rejected():
    new = {"a", "b", "c", "d"}
    old_siblings = {
        "Auth": {
            "module_id": "auth",
            "title": "Auth",
            "path": "auth",
            "components": ["a", "b", "c"],
            "children": {},
        },
        "AuthV2": {
            "module_id": "auth_v2",
            "title": "AuthV2",
            "path": "auth_v2",
            "components": ["a", "b", "d"],
            "children": {},
        },
    }
    assert find_dominant_match(new, old_siblings, threshold=0.70, margin=0.15) is None


def test_find_split_successor_dominant():
    old_components = {"a", "b", "c", "d"}
    new_groups = {
        "AuthCore": {"components": ["a", "b", "c"]},
        "AuthExtra": {"components": ["d", "e"]},
    }
    assert (
        find_split_successor(old_components, new_groups, threshold=0.70, margin=0.15) == "AuthCore"
    )


def test_find_split_successor_margin_check():
    old_components = {"a", "b", "c", "d"}
    new_groups = {
        "G1": {"components": ["a", "b", "c"]},
        "G2": {"components": ["a", "b", "d"]},
    }
    assert find_split_successor(old_components, new_groups, 0.70, 0.15) is None


def test_find_merge_predecessor_dominant():
    new_components = {"a", "b", "c", "d", "e"}
    old_siblings = {
        "OldA": {
            "module_id": "old_a",
            "title": "OldA",
            "path": "old_a",
            "components": ["a", "b", "c", "d"],
            "children": {},
        },
        "OldB": {
            "module_id": "old_b",
            "title": "OldB",
            "path": "old_b",
            "components": ["e"],
            "children": {},
        },
    }
    match = find_merge_predecessor(new_components, old_siblings, threshold=0.70, margin=0.15)
    assert match is not None
    assert match.old_key == "OldA"
