from codewiki.src.be.documentation_tree_utils import build_generation_tasks
from codewiki.src.codewiki_config import CodeWikiConfig


def _cfg(tmp_path):
    return CodeWikiConfig(
        repo_path=str(tmp_path / "repo"),
        docs_dir=str(tmp_path / "docs"),
        llm_base_url="http://localhost",
        llm_api_key="x",
        main_model="m",
        cluster_model="c",
        output_language="en",
    )


def test_leaves_get_kind_module(tmp_path):
    tree = {
        "Leaf": {
            "module_id": "leaf",
            "path": "leaf",
            "_doc_filename": "leaf.md",
            "components": ["a.py::A"],
            "children": {},
        }
    }
    tasks = build_generation_tasks(tree, _cfg(tmp_path))
    leaf_tasks = [t for t in tasks if t.doc_id != "overview:root"]
    assert len(leaf_tasks) == 1
    assert leaf_tasks[0].kind == "module"


def test_internal_parents_get_kind_module_not_overview(tmp_path):
    tree = {
        "Top": {
            "module_id": "top",
            "path": "top",
            "_doc_filename": "top.md",
            "components": [],
            "children": {
                "Left": {
                    "module_id": "left",
                    "path": "left",
                    "_doc_filename": "top-left.md",
                    "components": ["a.py::A"],
                    "children": {},
                }
            },
        }
    }
    tasks = build_generation_tasks(tree, _cfg(tmp_path))
    by_doc_id = {t.doc_id: t for t in tasks if t.doc_id != "overview:root"}
    top = next(
        t
        for doc_id, t in by_doc_id.items()
        if "top" in doc_id.lower() and "left" not in doc_id.lower()
    )
    assert top.kind == "module"


def test_only_the_synthetic_root_task_is_kind_overview(tmp_path):
    tree = {
        "Top": {
            "module_id": "top",
            "path": "top",
            "_doc_filename": "top.md",
            "components": [],
            "children": {
                "Leaf": {
                    "module_id": "leaf",
                    "path": "leaf",
                    "_doc_filename": "top-leaf.md",
                    "components": ["a.py::A"],
                    "children": {},
                }
            },
        }
    }
    tasks = build_generation_tasks(tree, _cfg(tmp_path))
    overview_tasks = [t for t in tasks if t.kind == "overview"]
    assert len(overview_tasks) == 1
    assert overview_tasks[0].doc_id == "overview:root"
