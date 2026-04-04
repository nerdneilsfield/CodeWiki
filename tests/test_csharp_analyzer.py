from codewiki.src.be.dependency_analyzer.analyzers.csharp import analyze_csharp_file


CSHARP_SAMPLE = """
interface IRunner {}
class Base {}

class Dep {}

class Child : Base, IRunner
{
    private Dep dep;
    public Dep Value { get; set; }

    public void Run(Dep other) { }
}
""".strip()


def test_csharp_analyzer_extracts_top_level_nodes(tmp_path):
    nodes, _ = analyze_csharp_file(str(tmp_path / "src" / "App.cs"), CSHARP_SAMPLE, str(tmp_path))

    names = {node.name for node in nodes}
    component_types = {node.name: node.component_type for node in nodes}

    assert {"IRunner", "Base", "Dep", "Child"} <= names
    assert component_types["IRunner"] == "interface"
    assert component_types["Base"] == "class"
    assert component_types["Child"] == "class"


def test_csharp_analyzer_extracts_base_field_property_and_param_relationships(tmp_path):
    _, rels = analyze_csharp_file(str(tmp_path / "src" / "App.cs"), CSHARP_SAMPLE, str(tmp_path))

    pairs = {(rel.caller, rel.callee) for rel in rels}
    assert ("src.App.Child", "src.App.Base") in pairs
    assert ("src.App.Child", "src.App.IRunner") in pairs
    assert ("src.App.Child", "Dep") in pairs


def test_csharp_analyzer_ignores_builtin_property_types(tmp_path):
    content = """
class Sample
{
    public string Name { get; set; }
}
""".strip()

    _, rels = analyze_csharp_file(str(tmp_path / "src" / "App.cs"), content, str(tmp_path))

    assert all(rel.callee != "string" for rel in rels)
