from codewiki.src.be.dependency_analyzer.analysis.call_graph_analyzer import CallGraphAnalyzer

def test_header_source_pairing():
    analyzer = CallGraphAnalyzer()
    from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship

    # Create nodes from two files
    analyzer.functions = {
        "utils.process": Node(
            id="utils.process", name="process", component_type="function",
            file_path="/repo/src/utils.cpp", relative_path="src/utils.cpp",
        ),
        "utils.helper": Node(
            id="utils.helper", name="helper", component_type="function",
            file_path="/repo/src/utils.h", relative_path="src/utils.h",
        ),
    }
    # Simulate an include relationship
    analyzer.call_relationships = [
        CallRelationship(
            caller="utils.__file__", callee="utils.h",
            call_line=1, is_resolved=False, relationship_type="include",
        ),
    ]

    analyzer._pair_header_source_files()

    header_impl_rels = [r for r in analyzer.call_relationships if r.relationship_type == "header_impl"]
    assert len(header_impl_rels) >= 1
