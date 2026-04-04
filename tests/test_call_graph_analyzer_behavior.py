from codewiki.src.be.dependency_analyzer.analysis.call_graph_analyzer import CallGraphAnalyzer
from codewiki.src.be.dependency_analyzer.models.core import CallRelationship, Node


def _node(
    node_id: str,
    name: str,
    *,
    file_path: str,
    component_type: str = "function",
    class_name: str | None = None,
    component_id: str | None = None,
    node_type: str | None = None,
):
    return Node(
        id=node_id,
        name=name,
        component_type=component_type,
        file_path=file_path,
        relative_path=file_path,
        source_code="",
        component_id=component_id or node_id,
        class_name=class_name,
        node_type=node_type or component_type,
    )


def test_extract_code_files_detects_special_cases():
    analyzer = CallGraphAnalyzer()
    file_tree = {
        "type": "directory",
        "children": [
            {"type": "file", "path": "CMakeLists.txt", "name": "CMakeLists.txt", "extension": ""},
            {"type": "file", "path": "Makefile", "name": "Makefile", "extension": ""},
            {"type": "file", "path": "build.mk", "name": "build.mk", "extension": ".mk"},
            {"type": "file", "path": "kernel.cfg", "name": "kernel.cfg", "extension": ".cfg"},
            {"type": "file", "path": "main.py", "name": "main.py", "extension": ".py"},
            {"type": "file", "path": "notes.txt", "name": "notes.txt", "extension": ".txt"},
        ],
    }

    result = analyzer.extract_code_files(file_tree)
    langs = {entry["path"]: entry["language"] for entry in result}

    assert langs["CMakeLists.txt"] == "cmake"
    assert langs["Makefile"] == "makefile"
    assert langs["build.mk"] == "makefile"
    assert langs["kernel.cfg"] == "vitis_cfg"
    assert langs["main.py"] == "python"
    assert "notes.txt" not in langs


def test_is_vitis_cfg_detects_markers():
    analyzer = CallGraphAnalyzer()

    assert analyzer._is_vitis_cfg("[connectivity]\nstream_connect=a:b")
    assert analyzer._is_vitis_cfg("syn.top=top_kernel")
    assert not analyzer._is_vitis_cfg("[general]\nfoo=bar")


def test_resolve_call_relationships_prefers_same_file_and_class():
    analyzer = CallGraphAnalyzer()
    analyzer.functions = {
        "pkg.a.Alpha.process": _node(
            "pkg.a.Alpha.process",
            "process",
            file_path="/repo/a.py",
            class_name="Alpha",
        ),
        "pkg.b.Beta.process": _node(
            "pkg.b.Beta.process",
            "process",
            file_path="/repo/b.py",
            class_name="Beta",
        ),
        "pkg.a.Alpha.run": _node(
            "pkg.a.Alpha.run",
            "run",
            file_path="/repo/a.py",
            class_name="Alpha",
        ),
    }
    analyzer.call_relationships = [
        CallRelationship(caller="pkg.a.Alpha.run", callee="process", relationship_type="calls")
    ]

    analyzer._resolve_call_relationships()

    assert analyzer.call_relationships[0].callee == "pkg.a.Alpha.process"
    assert analyzer.call_relationships[0].is_resolved is True


def test_deduplicate_relationships_and_generate_visualization_and_llm_format():
    analyzer = CallGraphAnalyzer()
    analyzer.functions = {
        "pkg.mod.helper": _node("pkg.mod.helper", "helper", file_path="/repo/mod.py"),
        "pkg.mod.run": _node("pkg.mod.run", "run", file_path="/repo/mod.py"),
    }
    analyzer.call_relationships = [
        CallRelationship(
            caller="pkg.mod.run",
            callee="pkg.mod.helper",
            is_resolved=True,
            relationship_type="call",
        ),
        CallRelationship(
            caller="pkg.mod.run",
            callee="pkg.mod.helper",
            is_resolved=True,
            relationship_type="call",
        ),
        CallRelationship(
            caller="pkg.mod.run", callee="missing", is_resolved=False, relationship_type="call"
        ),
    ]

    analyzer._deduplicate_relationships()
    viz = analyzer._generate_visualization_data()
    llm = analyzer.generate_llm_format()

    assert len(analyzer.call_relationships) == 2
    assert viz["summary"]["total_nodes"] == 2
    assert viz["summary"]["total_edges"] == 1
    assert viz["summary"]["unresolved_calls"] == 1
    assert llm["relationships"]["run"]["calls"] == ["pkg.mod.helper"]


def test_select_most_connected_nodes_keeps_high_degree_nodes():
    analyzer = CallGraphAnalyzer()
    analyzer.functions = {
        "a": _node("a", "a", file_path="/repo/a.py"),
        "b": _node("b", "b", file_path="/repo/b.py"),
        "c": _node("c", "c", file_path="/repo/c.py"),
    }
    analyzer.call_relationships = [
        CallRelationship(caller="a", callee="b", is_resolved=True, relationship_type="call"),
        CallRelationship(caller="a", callee="c", is_resolved=True, relationship_type="call"),
    ]

    analyzer._select_most_connected_nodes(2)

    assert set(analyzer.functions) == {"a", "b"}
    assert all(
        rel.caller in analyzer.functions and rel.callee in analyzer.functions
        for rel in analyzer.call_relationships
    )
