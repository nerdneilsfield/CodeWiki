from codewiki.src.codewiki_config import CodeWikiConfig


def _make_config(tmp_path):
    return CodeWikiConfig(
        repo_path=str(tmp_path / "repo"),
        output_dir=str(tmp_path / "out"),
        dependency_graph_dir=str(tmp_path / "graphs"),
        docs_dir=str(tmp_path / "docs"),
        max_depth=2,
        llm_base_url="http://localhost",
        llm_api_key="x",
        main_model="test/main",
        cluster_model="test/cluster",
        output_language="zh",
        max_concurrent=3,
    )


def test_freeze_doc_filenames_preserves_existing_frozen_name():
    from codewiki.src.be.documentation_tree_utils import freeze_doc_filenames

    tree = {
        "CLI Transport": {
            "path": "cli",
            "_doc_filename": "already_frozen.md",
            "children": {},
        }
    }

    freeze_doc_filenames(tree)

    assert tree["CLI Transport"]["_doc_filename"] == "already_frozen.md"


def test_freeze_doc_filenames_disambiguates_colliding_top_level_paths():
    from codewiki.src.be.documentation_tree_utils import freeze_doc_filenames

    tree = {
        "Utils A": {"path": "utils", "children": {}},
        "Utils B": {"path": "utils", "children": {}},
    }

    freeze_doc_filenames(tree)

    assert tree["Utils A"]["_doc_filename"] != tree["Utils B"]["_doc_filename"]


def test_build_generation_tasks_includes_child_dependencies(tmp_path):
    from codewiki.src.be.documentation_tree_utils import (
        build_generation_tasks,
        freeze_doc_filenames,
    )

    tree = {
        "CLI Transport": {
            "path": "cli",
            "module_id": "mod-cli",
            "components": ["a", "b"],
            "children": {
                "io_abstractions": {
                    "path": "",
                    "module_id": "mod-cli-io",
                    "components": ["c"],
                    "children": {},
                }
            },
        }
    }

    freeze_doc_filenames(tree)
    tasks = build_generation_tasks(tree, _make_config(tmp_path))
    by_id = {task.doc_id: task for task in tasks}

    assert by_id["module:mod-cli-io"].output_file == "cli-io_abstractions.md"
    assert by_id["module:mod-cli"].depends_on == ["module:mod-cli-io"]
    assert by_id["overview:root"].depends_on == ["module:mod-cli"]
    assert by_id["module:mod-cli"].language == "zh"


def test_cleanup_legacy_internal_files_removes_root_cache_files(tmp_path):
    from codewiki.src.be.documentation_tree_utils import cleanup_legacy_internal_files

    for name in ("_parent_doc_hashes.json", "_tree_cache_meta.json", "_guide_cache.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    removed = cleanup_legacy_internal_files(str(tmp_path))

    assert set(removed) == {"_parent_doc_hashes.json", "_tree_cache_meta.json", "_guide_cache.json"}


def test_select_effective_component_ids_samples_children_and_boundary_nodes():
    from types import SimpleNamespace

    from codewiki.src.be.documentation_tree_utils import select_effective_component_ids

    module_info = {
        "components": [
            "a1",
            "a2",
            "a3",
            "a4",
            "a5",
            "a6",
            "b1",
            "b2",
            "b3",
            "b4",
            "b5",
            "b6",
        ],
        "children": {
            "ChildA": {"components": ["a1", "a2", "a3", "a4", "a5", "a6"], "children": {}},
            "ChildB": {"components": ["b1", "b2", "b3", "b4", "b5", "b6"], "children": {}},
        },
    }
    components = {
        "a1": SimpleNamespace(depends_on={"b1"}, source_code="a1"),
        "a2": SimpleNamespace(depends_on=set(), source_code="a2"),
        "a3": SimpleNamespace(depends_on=set(), source_code="a3"),
        "a4": SimpleNamespace(depends_on=set(), source_code="a4"),
        "a5": SimpleNamespace(depends_on=set(), source_code="a5"),
        "a6": SimpleNamespace(depends_on=set(), source_code="a6"),
        "b1": SimpleNamespace(depends_on={"a1"}, source_code="b1"),
        "b2": SimpleNamespace(depends_on=set(), source_code="b2"),
        "b3": SimpleNamespace(depends_on=set(), source_code="b3"),
        "b4": SimpleNamespace(depends_on=set(), source_code="b4"),
        "b5": SimpleNamespace(depends_on=set(), source_code="b5"),
        "b6": SimpleNamespace(depends_on=set(), source_code="b6"),
    }

    selected = select_effective_component_ids(module_info, components)

    assert "a1" in selected
    assert "b1" in selected
    assert len(selected) < len(module_info["components"])
    assert any(cid.startswith("a") for cid in selected)
    assert any(cid.startswith("b") for cid in selected)
