from codewiki.src.be.dependency_analyzer.analyzers.cpp import analyze_cpp_file


CPP_SAMPLE = """
#include "dep.hpp"
#include <iostream>

class Base {};

class Service {
public:
    void ping() {}
};

class Child : public Base {
public:
    void run(int& ref, Service* ptr) {
        std::cout << ref;
    }
};

void helper() {}
"""

CPP_CALL_SAMPLE = """
class Service { public: void ping() {} };
void helper() {}
int build(int value) { helper(); return value; }
"""


def test_cpp_analyzer_extracts_classes_functions_methods_and_parameters(tmp_path):
    nodes, _ = analyze_cpp_file(str(tmp_path / "src" / "sample.cpp"), CPP_SAMPLE, str(tmp_path))

    names = {node.name for node in nodes}
    types = {node.name: node.component_type for node in nodes}
    params = {node.name: node.parameters for node in nodes}

    assert {"Base", "Service", "Child", "run", "helper"} <= names
    assert types["Child"] == "class"
    assert types["run"] == "method"
    assert params["run"] == ["ref", "ptr"]


def test_cpp_analyzer_extracts_include_and_inheritance_relationships(tmp_path):
    _, rels = analyze_cpp_file(str(tmp_path / "src" / "sample.cpp"), CPP_SAMPLE, str(tmp_path))

    pairs = {(rel.caller, rel.callee, rel.relationship_type) for rel in rels}

    assert ("src.sample.__file__", "dep.hpp", "include") in pairs
    assert ("src.sample.Child", "Base", "inherits") in pairs
    assert not any(rel.callee == "cout" for rel in rels)


def test_cpp_analyzer_extracts_simple_function_call_relationships(tmp_path):
    nodes, rels = analyze_cpp_file(
        str(tmp_path / "src" / "simple.cpp"), CPP_CALL_SAMPLE, str(tmp_path)
    )

    names = {node.name for node in nodes}
    pairs = {(rel.caller, rel.callee, rel.relationship_type) for rel in rels}

    assert {"Service", "ping", "helper", "build"} <= names
    assert ("src.simple.build", "src.simple.helper", "calls") in pairs
