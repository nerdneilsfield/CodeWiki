from codewiki.src.be.generation_state import DocTask, GenerationState
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


def test_module_doc_exists_prefers_ledger_completed_output_file(tmp_path):
    from codewiki.src.be.documentation_tree_utils import module_doc_exists

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "cli.md").write_text("# CLI\n" + "x" * 200, encoding="utf-8")

    tree = {"CLI Transport": {"module_id": "mod-cli", "children": {}}}
    state = GenerationState()
    state._add_task(
        DocTask(
            doc_id="module:mod-cli",
            kind="module",
            module_path=["CLI Transport"],
            output_file="cli.md",
            status="completed",
        )
    )

    assert module_doc_exists(str(docs_dir), ["CLI Transport"], tree, state) is True


def test_cleanup_legacy_internal_files_removes_root_cache_files(tmp_path):
    from codewiki.src.be.documentation_tree_utils import cleanup_legacy_internal_files

    for name in ("_parent_doc_hashes.json", "_tree_cache_meta.json", "_guide_cache.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    removed = cleanup_legacy_internal_files(str(tmp_path))

    assert set(removed) == {"_parent_doc_hashes.json", "_tree_cache_meta.json", "_guide_cache.json"}
