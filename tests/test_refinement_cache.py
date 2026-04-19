import os
from types import SimpleNamespace

import pytest

from codewiki.src.be.refinement_cache import (
    compute_refinement_input_hash,
    load_refinement_payload,
    load_previous_children,
    normalized_doc_id,
    refinement_artifact_id,
    refinement_output_path,
    save_refinement_payload,
)


def test_refinement_artifact_id_adds_prefix():
    assert refinement_artifact_id("auth_layer") == "refinement:auth_layer"


def test_refinement_artifact_id_idempotent():
    assert refinement_artifact_id("refinement:auth_layer") == "refinement:auth_layer"


def test_refinement_artifact_id_root():
    assert refinement_artifact_id("root") == "refinement:root"


@pytest.mark.parametrize(
    "doc_id,expected",
    [
        ("auth_layer", "auth_layer"),
        ("Backend Services & Integrations", "backend_services_integrations"),
        ("auth/layer", "auth_layer"),
        ("Auth.Layer", "auth_layer"),
        ("a" * 200, "a" * 120),
    ],
)
def test_normalized_doc_id(doc_id, expected):
    assert normalized_doc_id(doc_id) == expected


def test_refinement_output_path(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    path = refinement_output_path(str(cache_dir), "Backend Services")
    assert path.endswith(os.path.join("_refinement", "backend_services.json"))
    assert path.startswith(str(cache_dir))


def _make_component(source_code: str) -> SimpleNamespace:
    return SimpleNamespace(source_code=source_code)


def test_compute_refinement_input_hash_stable_for_same_inputs():
    components = {
        "a.py::Foo": _make_component("def foo(): pass"),
        "a.py::Bar": _make_component("def bar(): pass"),
    }
    h1 = compute_refinement_input_hash(
        component_ids=["a.py::Foo", "a.py::Bar"],
        components=components,
        current_depth=1,
        max_depth=3,
        min_components_for_split=6,
        min_distinct_files_for_split=4,
        max_cluster_components=1000,
        identity_reuse_threshold=0.70,
        output_language="en",
    )
    h2 = compute_refinement_input_hash(
        component_ids=["a.py::Bar", "a.py::Foo"],
        components=components,
        current_depth=1,
        max_depth=3,
        min_components_for_split=6,
        min_distinct_files_for_split=4,
        max_cluster_components=1000,
        identity_reuse_threshold=0.70,
        output_language="en",
    )
    assert h1 == h2


def test_compute_refinement_input_hash_changes_when_source_changes():
    base_kwargs = dict(
        component_ids=["a.py::Foo"],
        current_depth=1,
        max_depth=3,
        min_components_for_split=6,
        min_distinct_files_for_split=4,
        max_cluster_components=1000,
        identity_reuse_threshold=0.70,
        output_language="en",
    )
    h_old = compute_refinement_input_hash(
        components={"a.py::Foo": _make_component("def foo(): pass")},
        **base_kwargs,
    )
    h_new = compute_refinement_input_hash(
        components={"a.py::Foo": _make_component("def foo(): return 42")},
        **base_kwargs,
    )
    assert h_old != h_new


def test_compute_refinement_input_hash_changes_when_threshold_changes():
    components = {"a.py::Foo": _make_component("def foo(): pass")}
    base_kwargs = dict(
        component_ids=["a.py::Foo"],
        components=components,
        current_depth=1,
        max_depth=3,
        min_components_for_split=6,
        min_distinct_files_for_split=4,
        max_cluster_components=1000,
        output_language="en",
    )
    h1 = compute_refinement_input_hash(identity_reuse_threshold=0.70, **base_kwargs)
    h2 = compute_refinement_input_hash(identity_reuse_threshold=0.80, **base_kwargs)
    assert h1 != h2


def test_save_and_load_refinement_payload_roundtrip(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    payload = {
        "module_id": "auth_layer",
        "title": "Auth Layer",
        "path": "auth_layer",
        "description": "Authentication and session management.",
        "_doc_filename": "auth_layer.md",
        "components": ["src/auth.py::AuthManager"],
        "children": {},
    }

    saved_path = save_refinement_payload(str(cache_dir), "auth_layer", payload)
    assert os.path.exists(saved_path)

    loaded = load_refinement_payload(str(cache_dir), "auth_layer")
    assert loaded == payload


def test_load_refinement_payload_returns_none_when_missing(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    assert load_refinement_payload(str(cache_dir), "missing") is None


def test_load_refinement_payload_returns_none_when_corrupt(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    corrupt_path = refinement_output_path(str(cache_dir), "broken")
    os.makedirs(os.path.dirname(corrupt_path), exist_ok=True)
    with open(corrupt_path, "w", encoding="utf-8") as fh:
        fh.write("{not json")

    assert load_refinement_payload(str(cache_dir), "broken") is None


def test_load_previous_children_returns_dict_when_present(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    save_refinement_payload(
        str(cache_dir),
        "auth",
        {
            "children": {
                "Login": {
                    "module_id": "login",
                    "title": "Login",
                    "path": "login",
                    "components": ["a.py::Login"],
                }
            }
        },
    )
    children = load_previous_children(str(cache_dir), "auth")
    assert "Login" in children
    assert children["Login"]["module_id"] == "login"


def test_load_previous_children_missing_returns_empty_dict(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    assert load_previous_children(str(cache_dir), "missing") == {}
