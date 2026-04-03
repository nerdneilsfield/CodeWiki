from codewiki.src.be.documentation_generator import DocumentationGenerator
from codewiki.src.be.generation_state import DocTask, GenerationState
from codewiki.src.codewiki_config import CodeWikiConfig


def _make_generator(tmp_path):
    return DocumentationGenerator(
        CodeWikiConfig(
            repo_path=str(tmp_path / "repo"),
            output_dir=str(tmp_path / "out"),
            dependency_graph_dir=str(tmp_path / "graphs"),
            docs_dir=str(tmp_path),
            max_depth=2,
            llm_base_url="http://localhost",
            llm_api_key="x",
            main_model="test/main",
            cluster_model="test/cluster",
        )
    )


def test_build_overview_structure_falls_back_to_existing_file_when_ledger_target_missing(tmp_path):
    gen = _make_generator(tmp_path)
    tree = {
        "CLI Transport": {
            "module_id": "mod-cli",
            "children": {},
        }
    }
    (tmp_path / "cli_transport.md").write_text("# CLI Transport\nActual docs", encoding="utf-8")

    state = GenerationState()
    state._add_task(
        DocTask(
            doc_id="module:mod-cli",
            kind="module",
            module_path=["CLI Transport"],
            output_file="missing.md",
            status="completed",
        )
    )
    gen._gen_state = state

    result = gen.build_overview_structure(tree, [], str(tmp_path))

    assert result["CLI Transport"]["docs"] == "# CLI Transport\nActual docs"
