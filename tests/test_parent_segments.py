import os

import pytest

from codewiki.src.be.parent_segments import (
    compute_assembled_parent_input_hash,
    compute_child_segment_input_hash,
    compute_opening_input_hash,
    compute_overview_input_hash,
    doc_stem_from_filename,
    parent_child_segment_artifact_id,
    parent_opening_artifact_id,
    parent_overview_artifact_id,
    parent_segment_dir,
    parent_segment_path,
)


def test_parent_opening_artifact_id():
    assert parent_opening_artifact_id("auth_layer") == "module:auth_layer:segment:opening"


def test_parent_overview_artifact_id():
    assert parent_overview_artifact_id("auth_layer") == "module:auth_layer:segment:overview"


def test_parent_child_segment_artifact_id():
    assert (
        parent_child_segment_artifact_id("auth_layer", "login_flow")
        == "module:auth_layer:segment:child:login_flow"
    )


def test_doc_stem_from_filename():
    assert doc_stem_from_filename("auth_layer.md") == "auth_layer"


def test_parent_segment_dir(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    path = parent_segment_dir(str(cache_dir), "auth_layer")
    assert path.endswith(os.path.join("_module_parts", "auth_layer"))


def test_parent_segment_path_variants(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    assert parent_segment_path(str(cache_dir), "auth_layer", "opening").endswith(
        os.path.join("_module_parts", "auth_layer", "opening.md")
    )
    assert parent_segment_path(str(cache_dir), "auth_layer", "overview").endswith(
        os.path.join("_module_parts", "auth_layer", "overview.md")
    )
    assert parent_segment_path(
        str(cache_dir), "auth_layer", "child", child_doc_stem="login_flow"
    ).endswith(os.path.join("_module_parts", "auth_layer", "child_login_flow.md"))
    with pytest.raises(ValueError):
        parent_segment_path(str(cache_dir), "auth_layer", "child")


def test_hash_functions():
    assert compute_opening_input_hash(
        title="Auth", path="auth", description="Authentication.", output_language="en"
    ) == compute_opening_input_hash(
        title="Auth", path="auth", description="Authentication.", output_language="en"
    )
    assert compute_opening_input_hash(
        title="Auth", path="auth", description="Authentication.", output_language="en"
    ) != compute_opening_input_hash(
        title="Auth", path="auth", description="Sessions.", output_language="en"
    )

    overview_a = compute_overview_input_hash(
        title="Auth",
        path="auth",
        description="x",
        direct_child_pairs=[("login", "h1"), ("logout", "h2")],
        output_language="en",
    )
    overview_b = compute_overview_input_hash(
        title="Auth",
        path="auth",
        description="x",
        direct_child_pairs=[("logout", "h2"), ("login", "h1")],
        output_language="en",
    )
    assert overview_a == overview_b

    child_a = compute_child_segment_input_hash(
        child_module_id="login",
        child_title="Login",
        child_path="login",
        child_description="Login flow.",
        child_input_hash="abcd",
        output_language="en",
    )
    child_b = compute_child_segment_input_hash(
        child_module_id="login",
        child_title="Login",
        child_path="login",
        child_description="Login flow.",
        child_input_hash="zzzz",
        output_language="en",
    )
    assert child_a != child_b

    assert compute_assembled_parent_input_hash(
        opening_hash="o1",
        overview_hash="v1",
        child_segment_hashes=["c1", "c2"],
        output_language="en",
    ) == compute_assembled_parent_input_hash(
        opening_hash="o1",
        overview_hash="v1",
        child_segment_hashes=["c2", "c1"],
        output_language="en",
    )
