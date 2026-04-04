from codewiki.src.be.dependency_analyzer.analyzers.python import analyze_python_file


SAMPLE = """
class Base:
    pass

class Derived(Base):
    def method(self):
        helper()
        print("builtin")

def helper():
    return 1

def _test_internal():
    return 0
""".strip()


def test_python_analyzer_extracts_classes_functions_and_inheritance():
    nodes, rels = analyze_python_file("/repo/pkg/mod.py", SAMPLE, "/repo")

    names = {node.name for node in nodes}
    assert {"Base", "Derived", "helper"} <= names
    assert "_test_internal" not in names

    inheritance = {(rel.caller, rel.callee) for rel in rels if rel.is_resolved}
    assert ("pkg.mod.Derived", "pkg.mod.Base") in inheritance


def test_python_analyzer_records_function_calls_and_ignores_builtins():
    nodes, rels = analyze_python_file("/repo/pkg/mod.py", SAMPLE, "/repo")

    call_pairs = {(rel.caller, rel.callee) for rel in rels}
    assert ("pkg.mod.method", "pkg.mod.helper") not in call_pairs
    assert ("pkg.mod.Derived", "helper") in call_pairs
    assert all(rel.callee != "print" for rel in rels)


def test_python_analyzer_handles_syntax_error_without_crashing():
    nodes, rels = analyze_python_file("/repo/pkg/bad.py", "def broken(:\n", "/repo")

    assert nodes == []
    assert rels == []
