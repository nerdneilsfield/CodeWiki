"""Tests for clustering v2 pipeline — Phase 4 (naming) + Phase 5 (pipeline + integration).

TDD: Written BEFORE implementation (RED phase).
Tests cover:
1. heuristic_cluster_name / name_clusters
2. cluster_modules_v2 pipeline (legacy format, determinism, component coverage)
3. cluster_modules() dispatch (with / without index_products)
"""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from codewiki.src.be.clustering.models import (
    ModuleNode,
    ModuleTree,
    ModuleMembers,
    module_id_from_members,
    to_legacy_dict,
)
from codewiki.src.be.dependency_analyzer.models.core import Node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(cid: str, relative_path: str, depends_on=None) -> Node:
    """Create a minimal mock Node for testing."""
    return Node(
        id=cid,
        name=cid.split("::")[-1] if "::" in cid else cid,
        component_type="class",
        file_path=f"/repo/{relative_path}",
        relative_path=relative_path,
        depends_on=set(depends_on or []),
        source_code="",
        start_line=1,
        end_line=10,
    )


def _llm_result(content: str):
    from codewiki.src.be.llm_usage import LLMCallResult, LLMCallUsage

    return LLMCallResult(
        content=content,
        usage=LLMCallUsage(input_tokens=10, output_tokens=5, source="api"),
        model="test-model",
    )


def _make_index_products(edges=None):
    """Create a minimal mock IndexProducts with an edges list."""
    ip = MagicMock()
    ip.edges = edges or []
    return ip


def _make_components_and_leaf_nodes(spec: list[tuple[str, str]]) -> tuple[dict, list[str]]:
    """Build components dict and leaf_nodes list from (cid, relative_path) pairs."""
    components = {}
    leaf_nodes = []
    for cid, path in spec:
        components[cid] = _make_node(cid, path)
        leaf_nodes.append(cid)
    return components, leaf_nodes


# ---------------------------------------------------------------------------
# Part 1: naming.py — heuristic_cluster_name & name_clusters
# ---------------------------------------------------------------------------


class TestHeuristicClusterName:
    def test_uses_most_common_directory(self):
        """Title should reflect the most common directory among components."""
        from codewiki.src.be.clustering.naming import heuristic_cluster_name

        cluster = ["auth/login.py::Login", "auth/logout.py::Logout", "auth/token.py::Token"]
        file_map = {
            "auth/login.py::Login": "auth/login.py",
            "auth/logout.py::Logout": "auth/logout.py",
            "auth/token.py::Token": "auth/token.py",
        }
        title, description = heuristic_cluster_name(cluster, file_map)
        assert "auth" in title.lower() or "Auth" in title

    def test_description_contains_component_count(self):
        """Description must mention the number of components."""
        from codewiki.src.be.clustering.naming import heuristic_cluster_name

        cluster = ["db/conn.py::Conn", "db/pool.py::Pool"]
        file_map = {"db/conn.py::Conn": "db/conn.py", "db/pool.py::Pool": "db/pool.py"}
        _, description = heuristic_cluster_name(cluster, file_map)
        assert "2" in description

    def test_empty_cluster_returns_fallback(self):
        """Empty cluster should not raise — returns a fallback title."""
        from codewiki.src.be.clustering.naming import heuristic_cluster_name

        title, description = heuristic_cluster_name([], {})
        assert isinstance(title, str) and len(title) > 0
        assert isinstance(description, str)

    def test_components_without_paths_use_fallback(self):
        """Components with unknown paths should not crash."""
        from codewiki.src.be.clustering.naming import heuristic_cluster_name

        cluster = ["unknown_comp_1", "unknown_comp_2"]
        title, description = heuristic_cluster_name(cluster, {})
        assert isinstance(title, str) and len(title) > 0

    def test_returns_tuple_of_two_strings(self):
        from codewiki.src.be.clustering.naming import heuristic_cluster_name

        cluster = ["api/handler.py::Handler"]
        file_map = {"api/handler.py::Handler": "api/handler.py"}
        result = heuristic_cluster_name(cluster, file_map)
        assert isinstance(result, tuple) and len(result) == 2
        assert all(isinstance(s, str) for s in result)


class TestNameClusters:
    def test_returns_one_entry_per_cluster(self):
        from codewiki.src.be.clustering.naming import name_clusters

        clusters = [
            ["auth/login.py::Login", "auth/logout.py::Logout"],
            ["db/conn.py::Conn"],
        ]
        file_map = {
            "auth/login.py::Login": "auth/login.py",
            "auth/logout.py::Logout": "auth/logout.py",
            "db/conn.py::Conn": "db/conn.py",
        }
        results = name_clusters(clusters, file_map)
        assert len(results) == 2

    def test_each_entry_has_required_keys(self):
        from codewiki.src.be.clustering.naming import name_clusters

        clusters = [["api/v1.py::V1"]]
        file_map = {"api/v1.py::V1": "api/v1.py"}
        results = name_clusters(clusters, file_map)
        entry = results[0]
        assert "cluster_idx" in entry
        assert "title" in entry
        assert "description" in entry

    def test_cluster_idx_is_sequential(self):
        from codewiki.src.be.clustering.naming import name_clusters

        clusters = [["a/x.py::X"], ["b/y.py::Y"], ["c/z.py::Z"]]
        file_map = {
            "a/x.py::X": "a/x.py",
            "b/y.py::Y": "b/y.py",
            "c/z.py::Z": "c/z.py",
        }
        results = name_clusters(clusters, file_map)
        assert [r["cluster_idx"] for r in results] == [0, 1, 2]

    def test_empty_clusters_list(self):
        from codewiki.src.be.clustering.naming import name_clusters

        results = name_clusters([], {})
        assert results == []

    def test_config_parameter_is_optional(self):
        """name_clusters must accept config=None without raising."""
        from codewiki.src.be.clustering.naming import name_clusters

        clusters = [["x/a.py::A"]]
        file_map = {"x/a.py::A": "x/a.py"}
        results = name_clusters(clusters, file_map, config=None)
        assert len(results) == 1

    def test_naming_heuristic_uses_directory(self):
        """Module titles should relate to the directory structure."""
        from codewiki.src.be.clustering.naming import name_clusters

        clusters = [
            ["storage/s3.py::S3", "storage/local.py::Local", "storage/base.py::Base"],
        ]
        file_map = {
            "storage/s3.py::S3": "storage/s3.py",
            "storage/local.py::Local": "storage/local.py",
            "storage/base.py::Base": "storage/base.py",
        }
        results = name_clusters(clusters, file_map)
        title = results[0]["title"].lower()
        assert "storage" in title


# ---------------------------------------------------------------------------
# Part 2: pipeline.py — cluster_modules_v2
# ---------------------------------------------------------------------------


def _build_10_component_spec():
    """10 components split evenly across 2 directories."""
    spec = []
    for i in range(5):
        spec.append((f"auth/comp{i}.py::Comp{i}", f"auth/comp{i}.py"))
    for i in range(5):
        spec.append((f"api/comp{i}.py::Comp{i}", f"api/comp{i}.py"))
    return spec


class TestClusterModulesV2PipelineLegacyFormat:
    """test_pipeline_produces_legacy_format"""

    def test_output_has_module_keys_with_path_components_children(self):
        """Each top-level key in output must map to dict with path, components, children."""
        from codewiki.src.be.clustering.pipeline import cluster_modules_v2

        spec = _build_10_component_spec()
        components, leaf_nodes = _make_components_and_leaf_nodes(spec)
        index_products = _make_index_products()
        config = MagicMock()

        result = cluster_modules_v2(leaf_nodes, components, config, index_products)

        if result:  # may return {} if partitioner yields <= 1 cluster
            for key, val in result.items():
                assert isinstance(key, str), f"Key {key!r} is not a string"
                assert "path" in val, f"Missing 'path' in {key}"
                assert "components" in val, f"Missing 'components' in {key}"
                assert "children" in val, f"Missing 'children' in {key}"
                assert isinstance(val["path"], str)
                assert isinstance(val["components"], list)
                assert isinstance(val["children"], dict)

    def test_legacy_dict_has_correct_structure(self):
        """test_legacy_dict_has_correct_structure — alias for the above."""
        from codewiki.src.be.clustering.pipeline import cluster_modules_v2

        spec = _build_10_component_spec()
        components, leaf_nodes = _make_components_and_leaf_nodes(spec)
        index_products = _make_index_products()
        config = MagicMock()

        result = cluster_modules_v2(leaf_nodes, components, config, index_products)
        if not result:
            pytest.skip("Partitioner returned single cluster for this fixture")

        # Validate first-level entry
        first_val = next(iter(result.values()))
        assert isinstance(first_val["path"], str)
        assert isinstance(first_val["components"], list)
        assert isinstance(first_val["children"], dict)


class TestClusterModulesV2TooFew:
    """test_pipeline_too_few_components_returns_empty"""

    def test_two_components_returns_empty(self):
        from codewiki.src.be.clustering.pipeline import cluster_modules_v2

        spec = [("a/x.py::X", "a/x.py"), ("b/y.py::Y", "b/y.py")]
        components, leaf_nodes = _make_components_and_leaf_nodes(spec)
        index_products = _make_index_products()
        config = MagicMock()

        result = cluster_modules_v2(leaf_nodes, components, config, index_products)
        assert result == {}

    def test_three_components_returns_empty(self):
        from codewiki.src.be.clustering.pipeline import cluster_modules_v2

        spec = [("a/x.py::X", "a/x.py"), ("b/y.py::Y", "b/y.py"), ("c/z.py::Z", "c/z.py")]
        components, leaf_nodes = _make_components_and_leaf_nodes(spec)
        index_products = _make_index_products()
        config = MagicMock()

        result = cluster_modules_v2(leaf_nodes, components, config, index_products)
        assert result == {}

    def test_zero_components_returns_empty(self):
        from codewiki.src.be.clustering.pipeline import cluster_modules_v2

        result = cluster_modules_v2([], {}, MagicMock(), _make_index_products())
        assert result == {}


class TestClusterModulesV2Determinism:
    """test_pipeline_deterministic"""

    def test_same_input_three_times_produces_identical_output(self):
        from codewiki.src.be.clustering.pipeline import cluster_modules_v2

        spec = _build_10_component_spec()
        components, leaf_nodes = _make_components_and_leaf_nodes(spec)
        index_products = _make_index_products()
        config = MagicMock()

        results = [
            cluster_modules_v2(leaf_nodes, components, config, index_products) for _ in range(3)
        ]
        assert results[0] == results[1] == results[2], (
            "pipeline is non-deterministic across identical calls"
        )


class TestClusterModulesV2ComponentCoverage:
    """test_pipeline_components_all_assigned"""

    def _collect_all_components(self, result: dict) -> set:
        """Recursively collect all component IDs from a legacy dict."""
        found = set()
        for val in result.values():
            found.update(val.get("components", []))
            children = val.get("children", {})
            if children:
                found.update(self._collect_all_components(children))
        return found

    def test_all_input_components_appear_in_output(self):
        from codewiki.src.be.clustering.pipeline import cluster_modules_v2

        spec = _build_10_component_spec()
        components, leaf_nodes = _make_components_and_leaf_nodes(spec)
        index_products = _make_index_products()
        config = MagicMock()

        result = cluster_modules_v2(leaf_nodes, components, config, index_products)
        if not result:
            pytest.skip("Partitioner yielded single cluster; coverage check irrelevant")

        assigned = self._collect_all_components(result)
        expected = set(leaf_nodes)
        # root node wraps everything — assigned should include all input leaf nodes
        # (root's children components list aggregates everything)
        # We check at least that the module-level components collectively cover all inputs
        assert assigned >= expected, f"Missing components: {expected - assigned}"


class TestClusterModulesV2SingleCluster:
    """test_pipeline_single_cluster_returns_empty"""

    def test_all_components_same_dir_strong_colocation_may_give_single_cluster(self):
        """When all components share one directory, partitioner often yields 1 cluster → {}."""
        from codewiki.src.be.clustering.pipeline import cluster_modules_v2

        # 10 comps all in the same directory — co-location may collapse to single cluster
        spec = [(f"monolith/comp{i}.py::C{i}", f"monolith/comp{i}.py") for i in range(6)]
        components, leaf_nodes = _make_components_and_leaf_nodes(spec)
        index_products = _make_index_products()
        config = MagicMock()

        result = cluster_modules_v2(leaf_nodes, components, config, index_products)
        # Either {} (single cluster / too-few guard) OR a valid dict — never a crash
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Part 3: cluster_modules() dispatch
# ---------------------------------------------------------------------------


class TestClusterModulesDispatch:
    """test_cluster_modules_dispatch_with/without_index_products"""

    def test_with_index_products_calls_v2(self):
        """When index_products is provided, cluster_modules_v2 should be invoked."""
        from codewiki.src.be.cluster_modules import cluster_modules

        spec = _build_10_component_spec()
        components, leaf_nodes = _make_components_and_leaf_nodes(spec)
        index_products = _make_index_products()
        config = MagicMock()
        config.max_token_per_module = 1  # force threshold low so v1 won't short-circuit

        with patch(
            "codewiki.src.be.clustering.pipeline.cluster_modules_v2",
            return_value={"FakeModule": {"path": "x", "components": leaf_nodes, "children": {}}},
        ) as mock_v2:
            result = cluster_modules(
                leaf_nodes,
                components,
                config,
                index_products=index_products,
            )
        mock_v2.assert_called_once()
        assert result == {"FakeModule": {"path": "x", "components": leaf_nodes, "children": {}}}

    def test_without_index_products_skips_v2(self):
        """When index_products is None, the v2 pipeline must NOT be called."""
        from codewiki.src.be.cluster_modules import cluster_modules

        spec = _build_10_component_spec()
        components, leaf_nodes = _make_components_and_leaf_nodes(spec)
        config = MagicMock()
        config.max_token_per_module = 999_999  # token count >> threshold → short-circuits to {}

        with patch("codewiki.src.be.clustering.pipeline.cluster_modules_v2") as mock_v2:
            # No index_products kwarg → v1 path
            result = cluster_modules(leaf_nodes, components, config)

        mock_v2.assert_not_called()

    def test_v2_exception_falls_back_to_v1(self):
        """If v2 raises, cluster_modules should catch and fall back to v1."""
        from codewiki.src.be.cluster_modules import cluster_modules

        spec = _build_10_component_spec()
        components, leaf_nodes = _make_components_and_leaf_nodes(spec)
        index_products = _make_index_products()
        config = MagicMock()
        config.max_token_per_module = 999_999  # v1 will short-circuit to {} (fits in window)

        with patch(
            "codewiki.src.be.clustering.pipeline.cluster_modules_v2",
            side_effect=RuntimeError("simulated v2 failure"),
        ):
            # Should NOT raise — falls back to v1 which may return {}
            result = cluster_modules(
                leaf_nodes,
                components,
                config,
                index_products=index_products,
            )
        assert isinstance(result, dict)

    def test_v2_empty_result_falls_back_to_v1(self):
        """If v2 returns {}, cluster_modules should fall through to v1."""
        from codewiki.src.be.cluster_modules import cluster_modules

        spec = _build_10_component_spec()
        components, leaf_nodes = _make_components_and_leaf_nodes(spec)
        index_products = _make_index_products()
        config = MagicMock()
        config.max_token_per_module = 999_999  # v1 short-circuits to {}

        with patch(
            "codewiki.src.be.clustering.pipeline.cluster_modules_v2",
            return_value={},
        ) as mock_v2:
            result = cluster_modules(
                leaf_nodes,
                components,
                config,
                index_products=index_products,
            )
        mock_v2.assert_called_once()
        # v1 short-circuits because token_count <= threshold
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Part 4: _compute_module_path helper
# ---------------------------------------------------------------------------


class TestComputeModulePath:
    def test_returns_most_common_parent_dir(self):
        from codewiki.src.be.clustering.pipeline import _compute_module_path

        cluster = ["auth/a.py::A", "auth/b.py::B", "auth/c.py::C", "other/d.py::D"]
        file_map = {
            "auth/a.py::A": "auth/a.py",
            "auth/b.py::B": "auth/b.py",
            "auth/c.py::C": "auth/c.py",
            "other/d.py::D": "other/d.py",
        }
        path = _compute_module_path(cluster, file_map)
        assert path == "auth"

    def test_empty_cluster_returns_fallback(self):
        from codewiki.src.be.clustering.pipeline import _compute_module_path

        path = _compute_module_path([], {})
        assert path == "modules"

    def test_components_without_slash_returns_fallback(self):
        from codewiki.src.be.clustering.pipeline import _compute_module_path

        cluster = ["flat_comp"]
        file_map = {"flat_comp": "flat.py"}  # no directory component
        path = _compute_module_path(cluster, file_map)
        assert path == "modules"


# ---------------------------------------------------------------------------
# Part 5: LLM naming (RED — written before implementation)
# ---------------------------------------------------------------------------

import json


def _make_config_with_cluster_model(model: str = "gpt-4o-mini"):
    """Return a mock config that has cluster_model set."""
    cfg = MagicMock()
    cfg.cluster_model = model
    return cfg


def _make_config_without_cluster_model():
    """Return a mock config where cluster_model is None."""
    cfg = MagicMock()
    cfg.cluster_model = None
    return cfg


_TWO_CLUSTER_SPEC = [
    ["auth/login.py::Login", "auth/logout.py::Logout"],
    ["api/handler.py::Handler", "api/router.py::Router"],
]

_FILE_MAP = {
    "auth/login.py::Login": "auth/login.py",
    "auth/logout.py::Logout": "auth/logout.py",
    "api/handler.py::Handler": "api/handler.py",
    "api/router.py::Router": "api/router.py",
}

_VALID_LLM_RESPONSE = json.dumps(
    [
        {"cluster_idx": 0, "title": "认证模块 (Auth Module)", "description": "Handles auth"},
        {"cluster_idx": 1, "title": "接口模块 (API Module)", "description": "API routes"},
    ]
)


class TestLLMNamingHappyPath:
    """mock middleware returns valid JSON → titles from LLM are used."""

    def test_llm_naming_happy_path(self):
        from codewiki.src.be.clustering.naming import name_clusters

        config = _make_config_with_cluster_model()
        middleware = SimpleNamespace(call=MagicMock(return_value=_llm_result(_VALID_LLM_RESPONSE)))
        result = name_clusters(_TWO_CLUSTER_SPEC, _FILE_MAP, config, middleware=middleware)

        assert len(result) == 2
        assert result[0]["title"] == "认证模块 (Auth Module)"
        assert result[1]["title"] == "接口模块 (API Module)"
        assert result[0]["cluster_idx"] == 0
        assert result[1]["cluster_idx"] == 1


class TestLLMNamingFallbackOnInvalidJson:
    """mock returns garbage → heuristic titles used."""

    def test_llm_naming_fallback_on_invalid_json(self):
        from codewiki.src.be.clustering.naming import name_clusters

        config = _make_config_with_cluster_model()
        middleware = SimpleNamespace(
            call=MagicMock(return_value=_llm_result("this is not json at all %%%"))
        )
        result = name_clusters(_TWO_CLUSTER_SPEC, _FILE_MAP, config, middleware=middleware)

        # Should fall back to heuristic — still returns 2 entries
        assert len(result) == 2
        for entry in result:
            assert "cluster_idx" in entry
            assert "title" in entry
            assert "description" in entry
        # Heuristic titles should NOT be the LLM titles
        titles = {r["title"] for r in result}
        assert "认证模块 (Auth Module)" not in titles
        assert "接口模块 (API Module)" not in titles


class TestLLMNamingFallbackOnException:
    """mock raises → heuristic titles used."""

    def test_llm_naming_fallback_on_exception(self):
        from codewiki.src.be.clustering.naming import name_clusters

        config = _make_config_with_cluster_model()
        middleware = SimpleNamespace(call=MagicMock(side_effect=RuntimeError("LLM call failed")))
        result = name_clusters(_TWO_CLUSTER_SPEC, _FILE_MAP, config, middleware=middleware)

        assert len(result) == 2
        for entry in result:
            assert isinstance(entry["title"], str) and len(entry["title"]) > 0


class TestLLMNamingFallbackOnWrongCount:
    """mock returns fewer items than clusters → heuristic used."""

    def test_llm_naming_fallback_on_wrong_count(self):
        from codewiki.src.be.clustering.naming import name_clusters

        short_response = json.dumps(
            [
                {"cluster_idx": 0, "title": "Only One", "description": "Just one"},
            ]
        )
        config = _make_config_with_cluster_model()
        middleware = SimpleNamespace(call=MagicMock(return_value=_llm_result(short_response)))
        result = name_clusters(_TWO_CLUSTER_SPEC, _FILE_MAP, config, middleware=middleware)

        # Must still return 2 entries via heuristic fallback
        assert len(result) == 2
        titles = {r["title"] for r in result}
        assert "Only One" not in titles


class TestLLMNamingSkippedWithoutConfig:
    """config=None → heuristic only, middleware not called."""

    def test_llm_naming_skipped_without_config(self):
        from codewiki.src.be.clustering.naming import name_clusters

        middleware = SimpleNamespace(call=MagicMock())
        result = name_clusters(_TWO_CLUSTER_SPEC, _FILE_MAP, config=None, middleware=middleware)

        middleware.call.assert_not_called()
        assert len(result) == 2


class TestLLMNamingSkippedWithoutClusterModel:
    """config with cluster_model=None → heuristic only."""

    def test_llm_naming_skipped_without_cluster_model(self):
        from codewiki.src.be.clustering.naming import name_clusters

        config = _make_config_without_cluster_model()
        middleware = SimpleNamespace(call=MagicMock())
        result = name_clusters(_TWO_CLUSTER_SPEC, _FILE_MAP, config, middleware=middleware)

        middleware.call.assert_not_called()
        assert len(result) == 2


class TestNamingPromptConstraints:
    """Verify prompt builder produces correct content."""

    def test_naming_prompt_contains_constraints(self):
        from codewiki.src.be.clustering.naming import _build_naming_prompt

        prompt = _build_naming_prompt(_TWO_CLUSTER_SPEC, _FILE_MAP)
        assert "Do NOT" in prompt, "Prompt must contain 'Do NOT' constraint"

    def test_naming_prompt_contains_cluster_members(self):
        from codewiki.src.be.clustering.naming import _build_naming_prompt

        prompt = _build_naming_prompt(_TWO_CLUSTER_SPEC, _FILE_MAP)
        # Each component ID should appear in the prompt
        for cid in ["auth/login.py::Login", "api/handler.py::Handler"]:
            assert cid in prompt, f"Prompt must list component '{cid}'"

    def test_naming_prompt_requests_json(self):
        from codewiki.src.be.clustering.naming import _build_naming_prompt

        prompt = _build_naming_prompt(_TWO_CLUSTER_SPEC, _FILE_MAP)
        prompt_lower = prompt.lower()
        assert "json" in prompt_lower, "Prompt must request JSON output"


class TestHeuristicStillWorks:
    """Direct call to heuristic_cluster_name still returns valid tuple."""

    def test_heuristic_still_works(self):
        from codewiki.src.be.clustering.naming import heuristic_cluster_name

        cluster = ["auth/login.py::Login", "auth/logout.py::Logout"]
        file_map = {
            "auth/login.py::Login": "auth/login.py",
            "auth/logout.py::Logout": "auth/logout.py",
        }
        result = heuristic_cluster_name(cluster, file_map)

        assert isinstance(result, tuple)
        assert len(result) == 2
        title, description = result
        assert isinstance(title, str) and len(title) > 0
        assert isinstance(description, str) and len(description) > 0
        assert "auth" in title.lower() or "Auth" in title


# ---------------------------------------------------------------------------
# Part 6: Naming freeze
# ---------------------------------------------------------------------------

from codewiki.src.be.clustering.pipeline import _apply_naming_freeze
from codewiki.src.be.clustering.models import module_id_from_members


class TestNamingFreezeReusesOldTitle:
    """When module_id matches previous tree, old title is reused."""

    def test_freeze_replaces_title(self):
        clusters = [["comp_a", "comp_b"]]
        mid = module_id_from_members(["comp_a", "comp_b"])
        names = [{"cluster_idx": 0, "title": "New Title", "description": "New desc"}]
        previous_tree = {
            "Old Title": {
                "path": "old/path",
                "components": ["comp_a", "comp_b"],
                "children": {},
            }
        }
        result = _apply_naming_freeze(clusters, names, previous_tree)
        assert result[0]["title"] == "Old Title"

    def test_freeze_keeps_new_title_when_no_match(self):
        clusters = [["comp_c", "comp_d"]]
        names = [{"cluster_idx": 0, "title": "New Title", "description": "desc"}]
        previous_tree = {
            "Old Title": {
                "path": "old/path",
                "components": ["comp_a", "comp_b"],
                "children": {},
            }
        }
        result = _apply_naming_freeze(clusters, names, previous_tree)
        assert result[0]["title"] == "New Title"

    def test_freeze_with_empty_previous_tree(self):
        clusters = [["comp_a"]]
        names = [{"cluster_idx": 0, "title": "New", "description": ""}]
        result = _apply_naming_freeze(clusters, names, {})
        assert result[0]["title"] == "New"

    def test_freeze_with_none_previous_tree(self):
        clusters = [["comp_a"]]
        names = [{"cluster_idx": 0, "title": "New", "description": ""}]
        result = _apply_naming_freeze(clusters, names, None)
        assert result[0]["title"] == "New"

    def test_freeze_partial_match(self):
        """Two clusters: one matches previous tree, one doesn't."""
        clusters = [["comp_a", "comp_b"], ["comp_c", "comp_d"]]
        names = [
            {"cluster_idx": 0, "title": "New A", "description": ""},
            {"cluster_idx": 1, "title": "New C", "description": ""},
        ]
        previous_tree = {
            "Frozen A": {
                "path": "path/a",
                "components": ["comp_a", "comp_b"],
                "children": {},
            }
        }
        result = _apply_naming_freeze(clusters, names, previous_tree)
        assert result[0]["title"] == "Frozen A"  # frozen
        assert result[1]["title"] == "New C"  # not frozen

    def test_freeze_matches_nested_children(self):
        """Previous tree with nested children: freeze still works."""
        clusters = [["comp_x", "comp_y"]]
        names = [{"cluster_idx": 0, "title": "New", "description": ""}]
        previous_tree = {
            "Parent": {
                "path": "parent",
                "components": [],
                "children": {
                    "Child Frozen": {
                        "path": "parent/child",
                        "components": ["comp_x", "comp_y"],
                        "children": {},
                    }
                },
            }
        }
        result = _apply_naming_freeze(clusters, names, previous_tree)
        assert result[0]["title"] == "Child Frozen"

    def test_freeze_includes_path(self):
        """Frozen entry should include frozen_path from previous tree."""
        clusters = [["comp_a", "comp_b"]]
        names = [{"cluster_idx": 0, "title": "New", "description": ""}]
        previous_tree = {
            "Old": {
                "path": "stable/old/path",
                "components": ["comp_a", "comp_b"],
                "children": {},
            }
        }
        result = _apply_naming_freeze(clusters, names, previous_tree)
        assert result[0].get("frozen_path") == "stable/old/path"

    def test_non_frozen_entry_has_no_frozen_path(self):
        """Non-frozen entries should not have frozen_path."""
        clusters = [["comp_c"]]
        names = [{"cluster_idx": 0, "title": "New", "description": ""}]
        result = _apply_naming_freeze(clusters, names, {})
        assert "frozen_path" not in result[0]


# ---------------------------------------------------------------------------
# Part 7: LLM naming validation edge cases
# ---------------------------------------------------------------------------


class TestLLMNamingValidation:
    """Verify LLM naming rejects empty titles/descriptions."""

    def test_empty_title_triggers_fallback(self):
        from codewiki.src.be.clustering.naming import name_clusters

        clusters = [["auth/a.py::A"]]
        file_map = {"auth/a.py::A": "auth/a.py"}
        config = type("C", (), {"cluster_model": "test-model"})()
        response = json.dumps([{"cluster_idx": 0, "title": "", "description": "desc"}])
        middleware = SimpleNamespace(call=MagicMock(return_value=_llm_result(response)))
        result = name_clusters(clusters, file_map, config, middleware=middleware)
        # Empty title → LLM rejected → heuristic used
        assert result[0]["title"] != ""

    def test_empty_description_triggers_fallback(self):
        from codewiki.src.be.clustering.naming import name_clusters

        clusters = [["auth/a.py::A"]]
        file_map = {"auth/a.py::A": "auth/a.py"}
        config = type("C", (), {"cluster_model": "test-model"})()
        response = json.dumps([{"cluster_idx": 0, "title": "Auth", "description": ""}])
        middleware = SimpleNamespace(call=MagicMock(return_value=_llm_result(response)))
        result = name_clusters(clusters, file_map, config, middleware=middleware)
        # Empty description → LLM rejected → heuristic used
        assert result[0]["description"] != ""
