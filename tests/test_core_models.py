from codewiki.src.be.dependency_analyzer.models.core import (
    CallRelationship, DataFlowEdge, ParamInfo, HLSPragma, Node,
)

def test_call_relationship_has_relationship_type():
    rel = CallRelationship(caller="a", callee="b", relationship_type="include")
    assert rel.relationship_type == "include"

def test_call_relationship_has_data_flow():
    edge = DataFlowEdge(param_name="buf", param_type="int*", direction="inout", ownership="borrow")
    rel = CallRelationship(caller="a", callee="b", data_flow=[edge])
    assert len(rel.data_flow) == 1
    assert rel.data_flow[0].ownership == "borrow"

def test_param_info():
    p = ParamInfo(name="data", type_str="const int*", is_pointer=True, is_reference=False, is_const=True)
    assert p.is_pointer
    assert p.is_const

def test_hls_pragma():
    pragma = HLSPragma(
        pragma_type="INTERFACE",
        params={"port": "mem", "bundle": "gmem"},
        target="mem",
        line=10,
        hardware_semantic="AXI Master memory interface, bundle 'gmem'"
    )
    assert pragma.pragma_type == "INTERFACE"
    assert pragma.params["bundle"] == "gmem"

def test_node_has_hls_fields():
    node = Node(
        id="test", name="mm2s", component_type="function",
        file_path="/tmp/test.cpp", relative_path="test.cpp",
        is_hls_kernel=True, hls_pragmas=[]
    )
    assert node.is_hls_kernel is True
    assert node.hls_pragmas == []

def test_node_hls_fields_default():
    node = Node(
        id="test", name="foo", component_type="function",
        file_path="/tmp/test.cpp", relative_path="test.cpp",
    )
    assert node.is_hls_kernel is False
    assert node.hls_pragmas is None
