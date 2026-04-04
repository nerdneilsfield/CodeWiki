from codewiki.src.be.dependency_analyzer.analyzers.java import analyze_java_file


JAVA_SAMPLE = """
interface Runner {}

class Base {}

class Dep {
    void work() {}
}

class Child extends Base implements Runner {
    Dep dep;

    void run() {
        Dep local = new Dep();
        local.work();
    }
}
""".strip()


def test_java_analyzer_extracts_top_level_nodes(tmp_path):
    nodes, _ = analyze_java_file(str(tmp_path / "src" / "App.java"), JAVA_SAMPLE, str(tmp_path))

    names = {node.name for node in nodes}
    component_types = {node.name: node.component_type for node in nodes}

    assert {"Runner", "Base", "Dep", "Child", "Dep.work", "Child.run"} <= names
    assert component_types["Runner"] == "interface"
    assert component_types["Base"] == "class"
    assert component_types["Dep.work"] == "method"


def test_java_analyzer_extracts_inheritance_and_type_usage_relationships(tmp_path):
    _, rels = analyze_java_file(str(tmp_path / "src" / "App.java"), JAVA_SAMPLE, str(tmp_path))

    pairs = {(rel.caller, rel.callee) for rel in rels}
    assert ("src.App.Child", "src.App.Base") in pairs
    assert ("src.App.Child", "src.App.Runner") in pairs
    assert ("src.App.Child", "Dep") in pairs
