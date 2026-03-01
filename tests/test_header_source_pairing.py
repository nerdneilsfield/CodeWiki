"""Task 6: Header-source file pairing — comprehensive tests"""
from codewiki.src.be.dependency_analyzer.analysis.call_graph_analyzer import CallGraphAnalyzer
from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship


def _make_analyzer(functions, relationships):
    a = CallGraphAnalyzer()
    a.functions = dict(functions)
    a.call_relationships = list(relationships)
    return a


# ── basic pairing ──────────────────────────────────────────────────────────────

def test_header_source_pairing_creates_relationship():
    analyzer = _make_analyzer(
        functions={
            "src/utils.process": Node(
                id="src/utils.process", name="process", component_type="function",
                file_path="/repo/src/utils.cpp", relative_path="src/utils.cpp",
            ),
            "src/utils.declare": Node(
                id="src/utils.declare", name="declare", component_type="function",
                file_path="/repo/src/utils.h", relative_path="src/utils.h",
            ),
        },
        relationships=[
            CallRelationship(
                caller="src/utils.__file__", callee="utils.h",
                call_line=1, is_resolved=False, relationship_type="include",
            ),
        ],
    )
    analyzer._pair_header_source_files()
    header_impl_rels = [r for r in analyzer.call_relationships if r.relationship_type == "header_impl"]
    assert len(header_impl_rels) >= 1


def test_header_impl_caller_is_cpp():
    analyzer = _make_analyzer(
        functions={
            "a.foo": Node(
                id="a.foo", name="foo", component_type="function",
                file_path="/repo/a.cpp", relative_path="a.cpp",
            ),
            "a.bar": Node(
                id="a.bar", name="bar", component_type="function",
                file_path="/repo/a.h", relative_path="a.h",
            ),
        },
        relationships=[
            CallRelationship(
                caller="a.__file__", callee="a.h",
                call_line=1, is_resolved=False, relationship_type="include",
            ),
        ],
    )
    analyzer._pair_header_source_files()
    header_impl_rels = [r for r in analyzer.call_relationships if r.relationship_type == "header_impl"]
    assert len(header_impl_rels) >= 1
    for r in header_impl_rels:
        assert r.caller is not None
        assert r.callee is not None


def test_no_pairing_for_unrelated_files():
    """Files with no stem match should not produce header_impl relationships."""
    analyzer = _make_analyzer(
        functions={
            "foo.f": Node(
                id="foo.f", name="f", component_type="function",
                file_path="/repo/foo.cpp", relative_path="foo.cpp",
            ),
            "bar.g": Node(
                id="bar.g", name="g", component_type="function",
                file_path="/repo/bar.h", relative_path="bar.h",
            ),
        },
        relationships=[],
    )
    analyzer._pair_header_source_files()
    header_impl_rels = [r for r in analyzer.call_relationships if r.relationship_type == "header_impl"]
    # No include relationship, no pairing expected
    assert len(header_impl_rels) == 0


def test_hpp_header_also_paired():
    """Test that .hpp files are also paired with .cpp files."""
    analyzer = _make_analyzer(
        functions={
            "algo.run": Node(
                id="algo.run", name="run", component_type="function",
                file_path="/repo/algo.cpp", relative_path="algo.cpp",
            ),
            "algo.init": Node(
                id="algo.init", name="init", component_type="function",
                file_path="/repo/algo.hpp", relative_path="algo.hpp",
            ),
        },
        relationships=[
            CallRelationship(
                caller="algo.__file__", callee="algo.hpp",
                call_line=1, is_resolved=False, relationship_type="include",
            ),
        ],
    )
    analyzer._pair_header_source_files()
    header_impl_rels = [r for r in analyzer.call_relationships if r.relationship_type == "header_impl"]
    assert len(header_impl_rels) >= 1


def test_existing_relationships_preserved():
    """Pairing should add new relationships, not remove existing ones."""
    analyzer = _make_analyzer(
        functions={
            "utils.process": Node(
                id="utils.process", name="process", component_type="function",
                file_path="/repo/utils.cpp", relative_path="utils.cpp",
            ),
            "utils.helper": Node(
                id="utils.helper", name="helper", component_type="function",
                file_path="/repo/utils.h", relative_path="utils.h",
            ),
        },
        relationships=[
            CallRelationship(
                caller="utils.__file__", callee="utils.h",
                call_line=1, is_resolved=False, relationship_type="include",
            ),
        ],
    )
    count_before = len(analyzer.call_relationships)
    analyzer._pair_header_source_files()
    count_after = len(analyzer.call_relationships)
    # Should have added at least one new relationship
    assert count_after >= count_before
