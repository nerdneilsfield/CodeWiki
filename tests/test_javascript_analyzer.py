from unittest.mock import patch


def test_javascript_analyzer_extracts_top_level_functions_and_calls(tmp_path):
    from codewiki.src.be.dependency_analyzer.analyzers.javascript import (
        analyze_javascript_file_treesitter,
    )

    content = """
function helper() {
  return 1;
}

function main() {
  helper();
}

const arrow = () => helper();
""".strip()

    nodes, rels = analyze_javascript_file_treesitter(
        str(tmp_path / "src" / "app.js"),
        content,
        str(tmp_path),
    )

    names = {node.name for node in nodes}
    assert {"helper", "main", "arrow"} <= names

    resolved = {(rel.caller, rel.callee) for rel in rels if rel.is_resolved}
    assert ("src.app.main", "src.app.helper") in resolved
    assert ("src.app.arrow", "src.app.helper") in resolved


def test_javascript_analyzer_extracts_class_and_method_nodes(tmp_path):
    from codewiki.src.be.dependency_analyzer.analyzers.javascript import (
        analyze_javascript_file_treesitter,
    )

    content = """
class Service {
  run() {
    return 1;
  }
}
""".strip()

    nodes, _ = analyze_javascript_file_treesitter(
        str(tmp_path / "src" / "service.js"),
        content,
        str(tmp_path),
    )

    names = {node.name for node in nodes}
    component_types = {node.name: node.component_type for node in nodes}

    assert "Service" in names
    assert "run" in names
    assert component_types["Service"] == "class"
    assert component_types["run"] == "method"


def test_javascript_analyzer_returns_empty_when_parser_init_fails(tmp_path):
    from codewiki.src.be.dependency_analyzer.analyzers import javascript as js_mod

    with patch.object(js_mod, "_get_js_parser", return_value=None):
        nodes, rels = js_mod.analyze_javascript_file_treesitter(
            str(tmp_path / "src" / "broken.js"),
            "function x() {}",
            str(tmp_path),
        )

    assert nodes == []
    assert rels == []
