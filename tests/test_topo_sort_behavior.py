from codewiki.src.be.dependency_analyzer.models.core import Node
from codewiki.src.be.dependency_analyzer.topo_sort import (
    build_graph_from_components,
    dependency_first_dfs,
    detect_cycles,
    get_leaf_nodes,
    resolve_cycles,
    topological_sort,
)


def _component(name: str, component_type: str, depends_on: set[str] | None = None) -> Node:
    return Node(
        id=name,
        name=name.split(".")[-1],
        component_type=component_type,
        file_path=f"/repo/{name}.py",
        relative_path=f"{name}.py",
        depends_on=depends_on or set(),
    )


def test_detect_cycles_and_resolve_cycles():
    graph = {"a": {"b"}, "b": {"a"}, "d": set()}

    cycles = detect_cycles(graph)
    resolved = resolve_cycles(graph)

    assert any(set(cycle) == {"a", "b"} for cycle in cycles)
    assert detect_cycles(resolved) == []


def test_topological_sort_and_dependency_first_dfs_put_dependencies_first():
    graph = {"app": {"lib"}, "lib": {"core"}, "core": set()}

    topo = topological_sort(graph)
    dfs = dependency_first_dfs(graph)

    assert topo == ["app", "lib", "core"]
    assert dfs.index("core") < dfs.index("lib") < dfs.index("app")


def test_topological_sort_falls_back_when_cycles_remain(monkeypatch):
    graph = {"a": {"b"}, "b": {"a"}}
    monkeypatch.setattr(
        "codewiki.src.be.dependency_analyzer.topo_sort.resolve_cycles",
        lambda value: value,
    )

    result = topological_sort(graph)

    assert result == ["a", "b"] or result == ["b", "a"]


def test_build_graph_from_components_and_get_leaf_nodes_filters_invalid_entries():
    components = {
        "pkg.ClassA": _component("pkg.ClassA", "class", {"pkg.Helper"}),
        "pkg.Helper": _component("pkg.Helper", "function"),
        "pkg.Init.__init__": _component("pkg.Init.__init__", "function"),
        "error-node": _component("error-node", "class"),
    }

    graph = build_graph_from_components(components)
    leaves = get_leaf_nodes(graph, components)

    assert graph["pkg.ClassA"] == {"pkg.Helper"}
    assert "pkg.ClassA" in leaves
    assert "pkg.Init" not in leaves
    assert "error-node" not in leaves


def test_get_leaf_nodes_includes_functions_when_no_oop_types_exist():
    components = {
        "pkg.func": _component("pkg.func", "function"),
        "pkg.helper": _component("pkg.helper", "function"),
    }
    graph = build_graph_from_components(components)

    leaves = get_leaf_nodes(graph, components)

    assert set(leaves) == {"pkg.func", "pkg.helper"}
