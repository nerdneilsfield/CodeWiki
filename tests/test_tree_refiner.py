import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from codewiki.src.be.cache_manager import CacheManager
from codewiki.src.be.dependency_analyzer.models.core import Node
from codewiki.src.be.llm_usage import LLMCallResult
from codewiki.src.be.refinement_cache import save_refinement_payload
from codewiki.src.be.tree_refiner import (
    assign_doc_filename,
    refine_one_node,
    refine_tree,
    should_attempt_split,
)
from codewiki.src.codewiki_config import RefinementConfig


def _node(component_id: str, file_path: str, source_code: str = "pass") -> Node:
    return Node(
        id=component_id,
        name=component_id.split("::")[-1],
        component_type="function",
        file_path=file_path,
        relative_path=file_path,
        source_code=source_code,
    )


def _components(*pairs: tuple[str, str]) -> dict[str, Node]:
    return {cid: _node(cid, fp) for cid, fp in pairs}


@pytest.fixture
def cache_dir(tmp_path):
    path = tmp_path / ".codewiki"
    path.mkdir()
    return str(path)


def _llm_returning(payload: dict):
    fake_result = LLMCallResult(content=json.dumps(payload), usage=None, model="fake-model")
    middleware = MagicMock()
    middleware.call = AsyncMock(return_value=fake_result)
    return middleware


def test_should_attempt_split_too_few_components():
    cfg = RefinementConfig(min_components_for_split=6, min_distinct_files_for_split=4)
    comps = _components(("a.py::A", "a.py"), ("b.py::B", "b.py"))
    assert should_attempt_split(["a.py::A", "b.py::B"], comps, cfg, current_depth=1) is False


def test_should_attempt_split_too_few_distinct_files():
    cfg = RefinementConfig(min_components_for_split=4, min_distinct_files_for_split=4)
    comps = _components(
        ("a.py::A", "a.py"),
        ("a.py::B", "a.py"),
        ("a.py::C", "a.py"),
        ("a.py::D", "a.py"),
    )
    assert (
        should_attempt_split(
            ["a.py::A", "a.py::B", "a.py::C", "a.py::D"],
            comps,
            cfg,
            1,
        )
        is False
    )


def test_should_attempt_split_meets_thresholds():
    cfg = RefinementConfig(min_components_for_split=4, min_distinct_files_for_split=3)
    comps = _components(
        ("a.py::A", "a.py"),
        ("b.py::B", "b.py"),
        ("c.py::C", "c.py"),
        ("d.py::D", "d.py"),
    )
    assert (
        should_attempt_split(
            ["a.py::A", "b.py::B", "c.py::C", "d.py::D"],
            comps,
            cfg,
            1,
        )
        is True
    )


def test_should_attempt_split_max_depth_reached():
    cfg = RefinementConfig(max_depth=2, min_components_for_split=2, min_distinct_files_for_split=2)
    comps = _components(("a.py::A", "a.py"), ("b.py::B", "b.py"))
    assert should_attempt_split(["a.py::A", "b.py::B"], comps, cfg, current_depth=2) is False


def test_should_attempt_split_below_max_depth():
    cfg = RefinementConfig(max_depth=2, min_components_for_split=2, min_distinct_files_for_split=2)
    comps = _components(("a.py::A", "a.py"), ("b.py::B", "b.py"))
    assert should_attempt_split(["a.py::A", "b.py::B"], comps, cfg, current_depth=1) is True


def test_assign_doc_filename_simple():
    used: dict[str, str] = {}
    name = assign_doc_filename(
        used_files=used,
        artifact_id="module:auth_layer",
        preferred_stem="auth_layer",
    )
    assert name == "auth_layer.md"
    assert used["auth_layer.md"] == "module:auth_layer"


def test_assign_doc_filename_collision_with_other_artifact():
    used = {"auth_layer.md": "module:other_thing"}
    name = assign_doc_filename(
        used_files=used,
        artifact_id="module:auth_layer",
        preferred_stem="auth_layer",
    )
    assert name == "auth_layer_2.md"


def test_assign_doc_filename_idempotent_for_same_artifact():
    used = {"auth_layer.md": "module:auth_layer"}
    name = assign_doc_filename(
        used_files=used,
        artifact_id="module:auth_layer",
        preferred_stem="auth_layer",
    )
    assert name == "auth_layer.md"


def test_assign_doc_filename_walks_until_free():
    used = {
        "auth_layer.md": "module:other_a",
        "auth_layer_2.md": "module:other_b",
        "auth_layer_3.md": "module:other_c",
    }
    name = assign_doc_filename(
        used_files=used,
        artifact_id="module:auth_layer",
        preferred_stem="auth_layer",
    )
    assert name == "auth_layer_4.md"


@pytest.mark.asyncio
async def test_refine_one_node_calls_llm_when_no_cache(cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    components = {
        "a.py::A": _node("a.py::A", "a.py"),
        "b.py::B": _node("b.py::B", "b.py"),
        "c.py::C": _node("c.py::C", "c.py"),
        "d.py::D": _node("d.py::D", "d.py"),
    }
    cfg = RefinementConfig(max_depth=3, min_components_for_split=2, min_distinct_files_for_split=2)
    middleware = _llm_returning(
        {
            "should_split": True,
            "children": {
                "Group A": {
                    "module_id": "group_a",
                    "title": "Group A",
                    "path": "group_a",
                    "description": "First half.",
                    "components": ["a.py::A", "b.py::B"],
                },
                "Group B": {
                    "module_id": "group_b",
                    "title": "Group B",
                    "path": "group_b",
                    "description": "Second half.",
                    "components": ["c.py::C", "d.py::D"],
                },
            },
        }
    )
    children = await refine_one_node(
        parent_doc_id="root",
        parent_title="Root",
        parent_path="root",
        component_ids=list(components.keys()),
        components=components,
        current_depth=1,
        refinement_cfg=cfg,
        output_language="en",
        cluster_model="cluster",
        middleware=middleware,
        cache_manager=cache,
        cache_dir=cache_dir,
        used_files={},
    )
    assert middleware.call.await_count == 1
    assert set(children) == {"Group A", "Group B"}
    assert children["Group A"]["_doc_filename"] == "group_a.md"
    entry = cache.get_entry("refinement:root")
    assert entry is not None
    assert entry.status == "valid"


@pytest.mark.asyncio
async def test_refine_one_node_uses_cache_when_valid(cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    components = {"a.py::A": _node("a.py::A", "a.py"), "b.py::B": _node("b.py::B", "b.py")}
    cfg = RefinementConfig(max_depth=3, min_components_for_split=2, min_distinct_files_for_split=2)
    middleware1 = _llm_returning(
        {
            "should_split": True,
            "children": {
                "G": {
                    "module_id": "g",
                    "title": "G",
                    "path": "g",
                    "description": "All.",
                    "components": ["a.py::A", "b.py::B"],
                }
            },
        }
    )
    await refine_one_node(
        parent_doc_id="root",
        parent_title="Root",
        parent_path="root",
        component_ids=list(components.keys()),
        components=components,
        current_depth=1,
        refinement_cfg=cfg,
        output_language="en",
        cluster_model="cluster",
        middleware=middleware1,
        cache_manager=cache,
        cache_dir=cache_dir,
        used_files={},
    )
    middleware2 = _llm_returning({"should_split": False, "children": {}})
    children = await refine_one_node(
        parent_doc_id="root",
        parent_title="Root",
        parent_path="root",
        component_ids=list(components.keys()),
        components=components,
        current_depth=1,
        refinement_cfg=cfg,
        output_language="en",
        cluster_model="cluster",
        middleware=middleware2,
        cache_manager=cache,
        cache_dir=cache_dir,
        used_files={},
    )
    assert middleware2.call.await_count == 0
    assert "G" in children


@pytest.mark.asyncio
async def test_refine_tree_recurses_until_max_depth(cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    components = {f"f{i}.py::C{i}": _node(f"f{i}.py::C{i}", f"f{i}.py") for i in range(8)}
    top = {
        "Top": {
            "module_id": "top",
            "title": "Top",
            "path": "top",
            "description": "All.",
            "components": list(components.keys()),
            "children": {},
        }
    }
    cfg = RefinementConfig(max_depth=2, min_components_for_split=2, min_distinct_files_for_split=2)

    async def fake_call(prompt, model=None, temperature=0.0, **_):
        if "Parent module: Top" in prompt or "父模块标题：Top" in prompt:
            return LLMCallResult(
                content=json.dumps(
                    {
                        "should_split": True,
                        "children": {
                            "Left": {
                                "module_id": "left",
                                "title": "Left",
                                "path": "left",
                                "description": "L.",
                                "components": [f"f{i}.py::C{i}" for i in range(4)],
                            },
                            "Right": {
                                "module_id": "right",
                                "title": "Right",
                                "path": "right",
                                "description": "R.",
                                "components": [f"f{i}.py::C{i}" for i in range(4, 8)],
                            },
                        },
                    }
                ),
                usage=None,
                model="fake",
            )
        return LLMCallResult(
            content=json.dumps({"should_split": False, "children": {}}),
            usage=None,
            model="fake",
        )

    middleware = MagicMock()
    middleware.call = fake_call
    refined = await refine_tree(
        module_tree=top,
        components=components,
        refinement_cfg=cfg,
        output_language="en",
        cluster_model="c",
        middleware=middleware,
        cache_manager=cache,
        cache_dir=cache_dir,
    )
    assert set(refined["Top"]["children"]) == {"Left", "Right"}
    assert refined["Top"]["children"]["Left"]["children"] == {}
    assert refined["Top"]["_doc_filename"]
    assert refined["Top"]["children"]["Left"]["_doc_filename"]


@pytest.mark.asyncio
async def test_refine_tree_collision_against_existing_cache(cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    cache.plan_task("module:legacy", output_file="top.md")
    components = {"a.py::A": _node("a.py::A", "a.py"), "b.py::B": _node("b.py::B", "b.py")}
    top = {
        "Top": {
            "module_id": "top",
            "title": "Top",
            "path": "top",
            "description": ".",
            "components": list(components.keys()),
            "children": {},
        }
    }
    cfg = RefinementConfig(max_depth=1, min_components_for_split=2, min_distinct_files_for_split=2)
    middleware = MagicMock()
    middleware.call = AsyncMock(
        return_value=LLMCallResult(
            content=json.dumps({"should_split": False, "children": {}}),
            usage=None,
            model="fake",
        )
    )
    refined = await refine_tree(
        module_tree=top,
        components=components,
        refinement_cfg=cfg,
        output_language="en",
        cluster_model="c",
        middleware=middleware,
        cache_manager=cache,
        cache_dir=cache_dir,
    )
    assert refined["Top"]["_doc_filename"] != "top.md"
    assert refined["Top"]["_doc_filename"].startswith("top_")


@pytest.mark.asyncio
async def test_refine_one_node_reuses_identity_from_previous_run(cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    components = {
        "a.py::A": _node("a.py::A", "a.py"),
        "b.py::B": _node("b.py::B", "b.py"),
        "c.py::C": _node("c.py::C", "c.py"),
        "d.py::D": _node("d.py::D", "d.py"),
    }
    save_refinement_payload(
        cache_dir,
        "root",
        {
            "children": {
                "AuthLayer": {
                    "module_id": "auth_layer",
                    "title": "AuthLayer",
                    "path": "auth_layer",
                    "_doc_filename": "auth_layer.md",
                    "components": ["a.py::A", "b.py::B"],
                    "children": {},
                },
                "DataLayer": {
                    "module_id": "data_layer",
                    "title": "DataLayer",
                    "path": "data_layer",
                    "_doc_filename": "data_layer.md",
                    "components": ["c.py::C", "d.py::D"],
                    "children": {},
                },
            }
        },
    )
    middleware = MagicMock()
    middleware.call = AsyncMock(
        return_value=LLMCallResult(
            content=json.dumps(
                {
                    "should_split": True,
                    "children": {
                        "Authentication": {
                            "module_id": "authentication",
                            "title": "Authentication",
                            "path": "authentication",
                            "description": ".",
                            "components": ["a.py::A", "b.py::B"],
                        },
                        "DataAccess": {
                            "module_id": "data_access",
                            "title": "DataAccess",
                            "path": "data_access",
                            "description": ".",
                            "components": ["c.py::C", "d.py::D"],
                        },
                    },
                }
            ),
            usage=None,
            model="fake",
        )
    )
    cfg = RefinementConfig(
        max_depth=2,
        min_components_for_split=2,
        min_distinct_files_for_split=2,
        identity_reuse_threshold=0.70,
    )
    children = await refine_one_node(
        parent_doc_id="root",
        parent_title="Root",
        parent_path="root",
        component_ids=list(components.keys()),
        components=components,
        current_depth=1,
        refinement_cfg=cfg,
        output_language="en",
        cluster_model="c",
        middleware=middleware,
        cache_manager=cache,
        cache_dir=cache_dir,
        used_files={},
    )
    assert children["Authentication"]["module_id"] == "auth_layer"
    assert children["Authentication"]["_doc_filename"] == "auth_layer.md"
    assert children["DataAccess"]["module_id"] == "data_layer"
    assert children["DataAccess"]["_doc_filename"] == "data_layer.md"


@pytest.mark.asyncio
async def test_refine_one_node_split_successor_kicks_in_when_normal_match_fails(cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    save_refinement_payload(
        cache_dir,
        "root",
        {
            "children": {
                "Auth": {
                    "module_id": "auth",
                    "title": "Auth",
                    "path": "auth",
                    "_doc_filename": "auth.md",
                    "components": ["a", "b", "c", "d"],
                    "children": {},
                }
            }
        },
    )
    components = {cid: _node(cid, f"{cid}.py") for cid in list("abcdefghij")}
    middleware = MagicMock()
    middleware.call = AsyncMock(
        return_value=LLMCallResult(
            content=json.dumps(
                {
                    "should_split": True,
                    "children": {
                        "AuthMega": {
                            "module_id": "auth_mega",
                            "title": "AuthMega",
                            "path": "auth_mega",
                            "description": ".",
                            "components": list("abcdefghij"),
                        }
                    },
                }
            ),
            usage=None,
            model="fake",
        )
    )
    cfg = RefinementConfig(
        max_depth=2,
        min_components_for_split=2,
        min_distinct_files_for_split=2,
        identity_reuse_threshold=0.70,
    )
    children = await refine_one_node(
        parent_doc_id="root",
        parent_title="Root",
        parent_path="root",
        component_ids=list(components.keys()),
        components=components,
        current_depth=1,
        refinement_cfg=cfg,
        output_language="en",
        cluster_model="c",
        middleware=middleware,
        cache_manager=cache,
        cache_dir=cache_dir,
        used_files={},
    )
    assert children["AuthMega"]["module_id"] == "auth"
