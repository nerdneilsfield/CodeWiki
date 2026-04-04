from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _make_config(tmp_path):
    from codewiki.src.codewiki_config import CodeWikiConfig

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
    )


def test_build_dependency_graph_keeps_secondary_leaf_types_when_no_oop_primary(tmp_path):
    from codewiki.src.be.dependency_analyzer.dependency_graphs_builder import DependencyGraphBuilder

    config = _make_config(tmp_path)
    components = {
        "pkg.run": SimpleNamespace(component_type="function"),
        "pkg.config": SimpleNamespace(component_type="table"),
    }

    parser = MagicMock()
    parser.parse_repository.return_value = components

    with (
        patch(
            "codewiki.src.be.dependency_analyzer.dependency_graphs_builder.DependencyParser",
            return_value=parser,
        ),
        patch(
            "codewiki.src.be.dependency_analyzer.dependency_graphs_builder.build_graph_from_components",
            return_value={},
        ),
        patch(
            "codewiki.src.be.dependency_analyzer.dependency_graphs_builder.get_leaf_nodes",
            return_value=["pkg.run", "pkg.config"],
        ),
    ):
        out_components, leaf_nodes = DependencyGraphBuilder(config).build_dependency_graph()

    assert out_components is components
    assert leaf_nodes == ["pkg.run", "pkg.config"]


def test_build_dependency_graph_filters_invalid_and_unknown_leaf_nodes(tmp_path):
    from codewiki.src.be.dependency_analyzer.dependency_graphs_builder import DependencyGraphBuilder

    config = _make_config(tmp_path)
    components = {
        "pkg.ClassA": SimpleNamespace(component_type="class"),
        "pkg.helper": SimpleNamespace(component_type="function"),
    }
    parser = MagicMock()
    parser.parse_repository.return_value = components

    with (
        patch(
            "codewiki.src.be.dependency_analyzer.dependency_graphs_builder.DependencyParser",
            return_value=parser,
        ),
        patch(
            "codewiki.src.be.dependency_analyzer.dependency_graphs_builder.build_graph_from_components",
            return_value={},
        ),
        patch(
            "codewiki.src.be.dependency_analyzer.dependency_graphs_builder.get_leaf_nodes",
            return_value=["pkg.ClassA", "error: failed", "", "pkg.unknown"],
        ),
    ):
        _, leaf_nodes = DependencyGraphBuilder(config).build_dependency_graph()

    assert leaf_nodes == ["pkg.ClassA"]
