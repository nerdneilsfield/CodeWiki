from codewiki.src.be.documentation_generator import DocumentationGenerator
from codewiki.src.config import Config


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
        )
    )


def test_freeze_doc_filenames_preserves_existing_frozen_name(tmp_path):
    gen = _make_generator(tmp_path)
    tree = {
        "CLI Transport": {
            "path": "cli",
            "_doc_filename": "already_frozen.md",
            "children": {},
        }
    }

    gen._freeze_doc_filenames(tree)

    assert tree["CLI Transport"]["_doc_filename"] == "already_frozen.md"


def test_freeze_doc_filenames_disambiguates_colliding_top_level_paths(tmp_path):
    gen = _make_generator(tmp_path)
    tree = {
        "Utils A": {"path": "utils", "children": {}},
        "Utils B": {"path": "utils", "children": {}},
    }

    gen._freeze_doc_filenames(tree)

    assert tree["Utils A"]["_doc_filename"] != tree["Utils B"]["_doc_filename"]
    assert tree["Utils A"]["_doc_filename"].endswith(".md")
    assert tree["Utils B"]["_doc_filename"].endswith(".md")


def test_freeze_doc_filenames_uses_parent_stem_for_empty_child_path(tmp_path):
    gen = _make_generator(tmp_path)
    tree = {
        "CLI Transport": {
            "path": "cli",
            "children": {
                "io_abstractions": {
                    "path": "",
                    "children": {},
                }
            },
        }
    }

    gen._freeze_doc_filenames(tree)

    assert (
        tree["CLI Transport"]["children"]["io_abstractions"]["_doc_filename"]
        == "cli-io_abstractions.md"
    )
