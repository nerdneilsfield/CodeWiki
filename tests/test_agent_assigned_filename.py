from codewiki.src.be.agent_orchestrator import AgentOrchestrator
from codewiki.src.config import Config
from codewiki.src.utils import doc_id_for_path


def _make_config(tmp_path):
    return Config(
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


def test_assigned_doc_filename_reads_frozen_tree_node(tmp_path):
    orchestrator = AgentOrchestrator(_make_config(tmp_path))
    tree = {
        "CLI Transport": {
            "_doc_filename": "cli.md",
            "children": {
                "io_abstractions": {
                    "_doc_filename": "cli-io_abstractions.md",
                    "children": {},
                }
            },
        }
    }

    assert orchestrator._assigned_doc_filename(tree, ["CLI Transport"]) == "cli.md"
    assert (
        orchestrator._assigned_doc_filename(tree, ["CLI Transport", "io_abstractions"])
        == "cli-io_abstractions.md"
    )


def test_doc_id_for_path_prefers_module_id_then_filename(tmp_path):
    tree = {
        "CLI Transport": {
            "module_id": "mod-cli",
            "_doc_filename": "cli.md",
            "children": {
                "io_abstractions": {
                    "_doc_filename": "cli-io_abstractions.md",
                    "children": {},
                }
            },
        }
    }

    assert doc_id_for_path(tree, ["CLI Transport"]) == "module:mod-cli"
    assert (
        doc_id_for_path(tree, ["CLI Transport", "io_abstractions"])
        == "module:cli-io_abstractions"
    )
