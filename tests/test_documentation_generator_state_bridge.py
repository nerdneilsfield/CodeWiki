from codewiki.src.be.documentation_generator import DocumentationGenerator, cleanup_legacy_internal_files
from codewiki.src.config import Config
from codewiki.src.be.generation_state import GenerationState, DocTask


def _make_generator(tmp_path):
    return DocumentationGenerator(
        Config(
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
        )
    )


def test_build_generation_tasks_freezes_filenames_and_dependencies(tmp_path):
    gen = _make_generator(tmp_path)
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

    gen._freeze_doc_filenames(tree)
    tasks = gen._build_generation_tasks(tree)
    by_id = {task.doc_id: task for task in tasks}

    assert tree["CLI Transport"]["_doc_filename"] == "cli.md"
    assert tree["CLI Transport"]["children"]["io_abstractions"]["_doc_filename"] == "cli-io_abstractions.md"
    assert by_id["module:mod-cli-io"].output_file == "cli-io_abstractions.md"
    assert by_id["module:mod-cli"].depends_on == ["module:mod-cli-io"]
    assert by_id["overview:root"].depends_on == ["module:mod-cli"]
    assert by_id["module:mod-cli"].language == "zh"
    assert by_id["module:mod-cli"].input_hash
    assert by_id["module:mod-cli-io"].input_hash


def test_generation_tasks_can_mark_existing_state_stale(tmp_path):
    gen = _make_generator(tmp_path)
    tree = {
        "CLI Transport": {
            "path": "cli",
            "module_id": "mod-cli",
            "components": ["a", "b"],
            "children": {},
        }
    }
    gen._freeze_doc_filenames(tree)
    tasks = gen._build_generation_tasks(tree)
    module_task = next(task for task in tasks if task.doc_id == "module:mod-cli")

    state = GenerationState()
    state._add_task(
        DocTask(
            doc_id="module:mod-cli",
            kind="module",
            module_path=["CLI Transport"],
            output_file="cli.md",
            status="completed",
            input_hash="old",
        )
    )
    state._mark_stale_tasks({"module:mod-cli": module_task.input_hash})

    assert state.get_task("module:mod-cli").status == "stale"


def test_dedup_docs_directory_removes_similar_smaller_duplicates(tmp_path):
    from codewiki.src.be.documentation_generator import dedup_docs_directory

    winner = tmp_path / "media_and_data.md"
    loser = tmp_path / "Media-and-Data.md"
    winner.write_text("# Title\nLine 1\nLine 2\nLine 3\nLine 4\nLine 5\n", encoding="utf-8")
    loser.write_text("# Title\nLine 1\nLine 2\nLine 3\nLine 4\n", encoding="utf-8")

    result = dedup_docs_directory(str(tmp_path))

    assert winner.exists()
    assert not loser.exists()
    assert loser.name in result["removed"]


def test_dedup_docs_directory_keeps_conflicting_content(tmp_path):
    from codewiki.src.be.documentation_generator import dedup_docs_directory

    a = tmp_path / "query_context.md"
    b = tmp_path / "Query-Context.md"
    a.write_text("# 中文\n这里是中文内容\n", encoding="utf-8")
    b.write_text("# English\nThis is english content\nCompletely different\n", encoding="utf-8")

    result = dedup_docs_directory(str(tmp_path))

    assert a.exists()
    assert b.exists()
    assert result["skipped_conflicts"]


def test_cleanup_legacy_internal_files_removes_root_cache_files(tmp_path):
    for name in ("_parent_doc_hashes.json", "_tree_cache_meta.json", "_guide_cache.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    removed = cleanup_legacy_internal_files(str(tmp_path))

    assert set(removed) == {"_parent_doc_hashes.json", "_tree_cache_meta.json", "_guide_cache.json"}
    for name in removed:
        assert not (tmp_path / name).exists()
