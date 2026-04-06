from codewiki.src.be.documentation_generator import DocumentationGenerator
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


def test_build_overview_structure_finds_existing_file(tmp_path):
    gen = _make_generator(tmp_path)
    tree = {
        "CLI Transport": {
            "module_id": "mod-cli",
            "children": {},
        }
    }
    (tmp_path / "cli_transport.md").write_text("# CLI Transport\nActual docs", encoding="utf-8")

    result = gen.build_overview_structure(tree, [], str(tmp_path))

    assert result["CLI Transport"]["docs"] == "# CLI Transport\nActual docs"
