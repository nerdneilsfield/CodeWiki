"""Task 1: core.py data model extensions — comprehensive tests"""
from codewiki.src.be.dependency_analyzer.models.core import (
    CallRelationship, DataFlowEdge, ParamInfo, HLSPragma, Node,
)


# ── CallRelationship ──────────────────────────────────────────────────────────

def test_call_relationship_has_relationship_type():
    rel = CallRelationship(caller="a", callee="b", relationship_type="include")
    assert rel.relationship_type == "include"


def test_call_relationship_default_relationship_type_is_none():
    rel = CallRelationship(caller="a", callee="b")
    assert rel.relationship_type is None


def test_call_relationship_has_data_flow():
    edge = DataFlowEdge(param_name="buf", param_type="int*", direction="inout", ownership="borrow")
    rel = CallRelationship(caller="a", callee="b", data_flow=[edge])
    assert len(rel.data_flow) == 1
    assert rel.data_flow[0].param_name == "buf"
    assert rel.data_flow[0].direction == "inout"
    assert rel.data_flow[0].ownership == "borrow"


def test_call_relationship_accepts_all_expected_types():
    for rtype in (
        "include", "call", "calls", "inherits", "creates", "uses",
        "header_impl", "hls_source", "hls_compile", "stream_connect",
        "memory_map", "target_dep", "compile_dep", "header_dep",
        "hls_synth", "hls_export",
    ):
        rel = CallRelationship(caller="x", callee="y", relationship_type=rtype)
        assert rel.relationship_type == rtype


# ── DataFlowEdge ──────────────────────────────────────────────────────────────

def test_data_flow_edge_defaults():
    edge = DataFlowEdge(param_name="x")
    assert edge.direction == "in"
    assert edge.param_type is None
    assert edge.ownership is None
    assert edge.lifetime_hint is None


def test_data_flow_edge_all_fields():
    edge = DataFlowEdge(
        param_name="buf",
        param_type="int*",
        direction="inout",
        ownership="transfer",
        lifetime_hint="heap",
    )
    assert edge.param_name == "buf"
    assert edge.param_type == "int*"
    assert edge.direction == "inout"
    assert edge.ownership == "transfer"
    assert edge.lifetime_hint == "heap"


# ── ParamInfo ─────────────────────────────────────────────────────────────────

def test_param_info_pointer():
    p = ParamInfo(name="data", type_str="const int*", is_pointer=True, is_const=True)
    assert p.name == "data"
    assert p.type_str == "const int*"
    assert p.is_pointer is True
    assert p.is_const is True
    assert p.is_reference is False


def test_param_info_reference():
    p = ParamInfo(name="ref", type_str="const T&", is_reference=True, is_const=True)
    assert p.is_reference is True
    assert p.is_pointer is False


def test_param_info_defaults():
    p = ParamInfo(name="n")
    assert p.type_str is None
    assert p.is_pointer is False
    assert p.is_reference is False
    assert p.is_const is False


# ── HLSPragma ─────────────────────────────────────────────────────────────────

def test_hls_pragma_interface():
    pragma = HLSPragma(
        pragma_type="INTERFACE",
        params={"subtype": "m_axi", "port": "mem", "bundle": "gmem"},
        target="mem",
        line=5,
        hardware_semantic="AXI Master memory interface, bundle gmem",
    )
    assert pragma.pragma_type == "INTERFACE"
    assert pragma.params["port"] == "mem"
    assert pragma.params["bundle"] == "gmem"
    assert pragma.target == "mem"
    assert pragma.line == 5
    assert "AXI" in pragma.hardware_semantic


def test_hls_pragma_pipeline():
    pragma = HLSPragma(pragma_type="PIPELINE", params={"ii": "1"}, line=10)
    assert pragma.pragma_type == "PIPELINE"
    assert pragma.params["ii"] == "1"
    assert pragma.hardware_semantic == ""  # default


def test_hls_pragma_defaults():
    pragma = HLSPragma(pragma_type="DATAFLOW", line=3)
    assert pragma.params == {}
    assert pragma.target is None
    assert pragma.hardware_semantic == ""


# ── Node HLS fields ───────────────────────────────────────────────────────────

def test_node_has_hls_fields():
    node = Node(
        id="a.mm2s", name="mm2s", component_type="function",
        file_path="/tmp/mm2s.cpp", relative_path="mm2s.cpp",
        is_hls_kernel=True,
        hls_pragmas=[
            HLSPragma(pragma_type="INTERFACE", line=3, params={"port": "mem"}),
            HLSPragma(pragma_type="PIPELINE", line=7, params={"ii": "1"}),
        ],
    )
    assert node.is_hls_kernel is True
    assert node.hls_pragmas is not None
    assert len(node.hls_pragmas) == 2
    assert node.hls_pragmas[0].pragma_type == "INTERFACE"
    assert node.hls_pragmas[1].pragma_type == "PIPELINE"


def test_node_hls_fields_default():
    node = Node(
        id="a.f", name="f", component_type="function",
        file_path="/tmp/f.c", relative_path="f.c",
    )
    assert node.is_hls_kernel is False
    assert node.hls_pragmas is None
