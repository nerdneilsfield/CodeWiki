from codewiki.src.be.dependency_analyzer.analyzers.c import analyze_c_file


C_SAMPLE = """
#include "dep.h"

int shared = 1;

struct Data {
    int value;
};

struct Obj {
    int (*run)(void);
};

void helper(void) {}

void caller(struct Obj* obj) {
    obj->run();
    helper();
    printf("ignored");
    shared = 2;
}
"""


def test_c_analyzer_extracts_nodes_and_parameters(tmp_path):
    nodes, _ = analyze_c_file(str(tmp_path / "src" / "sample.c"), C_SAMPLE, str(tmp_path))

    names = {node.name for node in nodes}
    node_types = {node.name: node.component_type for node in nodes}
    params = {node.name: node.parameters for node in nodes}

    assert {"Data", "Obj", "helper", "caller"} <= names
    assert node_types["Data"] == "struct"
    assert node_types["helper"] == "function"
    assert params["caller"] == ["obj"]


def test_c_analyzer_extracts_include_call_and_global_variable_relationships(tmp_path):
    _, rels = analyze_c_file(str(tmp_path / "src" / "sample.c"), C_SAMPLE, str(tmp_path))

    pairs = {(rel.caller, rel.callee, rel.relationship_type, rel.is_resolved) for rel in rels}

    assert ("src.sample.__file__", "dep.h", "include", False) in pairs
    assert ("src.sample.caller", "run", "call", False) in pairs
    assert ("src.sample.caller", "helper", "call", False) in pairs
    assert ("src.sample.caller", "src.sample.shared", None, True) in pairs
    assert not any(rel.callee == "printf" for rel in rels)
