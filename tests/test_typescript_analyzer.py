from codewiki.src.be.dependency_analyzer.analyzers.typescript import (
    analyze_typescript_file_treesitter,
)


TS_SAMPLE = """
interface Service {}

class Dep {}

function helper() {
  return 1;
}

const arrow = () => helper();

class App implements Service {
  constructor(private dep: Dep) {}

  run(): Dep {
    helper();
    return new Dep();
  }
}
""".strip()


def test_typescript_analyzer_extracts_top_level_entities(tmp_path):
    nodes, _ = analyze_typescript_file_treesitter(
        str(tmp_path / "src" / "app.ts"),
        TS_SAMPLE,
        str(tmp_path),
    )

    names = {node.name for node in nodes}
    component_types = {node.name: node.component_type for node in nodes}

    assert {"Service", "Dep", "helper", "arrow", "App"} <= names
    assert component_types["Service"] == "interface"
    assert component_types["App"] == "class"
    assert component_types["helper"] == "function"


def test_typescript_analyzer_extracts_call_type_and_constructor_relationships(tmp_path):
    _, rels = analyze_typescript_file_treesitter(
        str(tmp_path / "src" / "app.ts"),
        TS_SAMPLE,
        str(tmp_path),
    )

    pairs = {(rel.caller, rel.callee) for rel in rels}
    assert ("src.app.arrow", "src.app.helper") in pairs
    assert ("src.app.App", "src.app.Service") in pairs
    assert ("src.app.App", "src.app.Dep") in pairs
