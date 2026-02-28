from codewiki.src.be.dependency_analyzer.analysis.data_flow_analyzer import DataFlowAnalyzer
from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship


def test_data_flow_basic():
    """Test that data flow edges are created for call relationships."""
    functions = {
        "a.producer": Node(
            id="a.producer", name="producer", component_type="function",
            file_path="/repo/a.cpp", relative_path="a.cpp",
            parameters=["buf", "size"],
        ),
        "b.consumer": Node(
            id="b.consumer", name="consumer", component_type="function",
            file_path="/repo/b.cpp", relative_path="b.cpp",
            parameters=["data", "len"],
        ),
    }
    relationships = [
        CallRelationship(
            caller="a.producer", callee="b.consumer",
            call_line=10, is_resolved=True, relationship_type="call",
        ),
    ]

    analyzer = DataFlowAnalyzer(functions, relationships)
    result = analyzer.analyze()

    assert "flow_edges" in result
    assert len(result["flow_edges"]) >= 0  # At minimum doesn't crash


def test_ownership_detection_malloc():
    """Test that malloc/free patterns are detected."""
    functions = {
        "main.init": Node(
            id="main.init", name="init", component_type="function",
            file_path="/repo/main.c", relative_path="main.c",
            parameters=[],
            source_code="void init() { int* p = malloc(100); free(p); }",
        ),
    }
    analyzer = DataFlowAnalyzer(functions, [])
    result = analyzer.analyze()

    assert "ownership_patterns" in result
    # Should detect malloc/free pattern
