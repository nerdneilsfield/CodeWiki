"""Task 8: Cross-file data flow analyzer — comprehensive tests"""

from codewiki.src.be.dependency_analyzer.analysis.data_flow_analyzer import DataFlowAnalyzer
from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship


def _node(nid, name, params=None, source_code=""):
    return Node(
        id=nid,
        name=name,
        component_type="function",
        file_path=f"/repo/{nid.split('.')[0]}.cpp",
        relative_path=f"{nid.split('.')[0]}.cpp",
        parameters=params or [],
        source_code=source_code,
    )


def _call(caller, callee, line=10):
    return CallRelationship(
        caller=caller,
        callee=callee,
        call_line=line,
        is_resolved=True,
        relationship_type="call",
    )


# ── basic flow edge creation ───────────────────────────────────────────────────


def test_flow_edges_key_present():
    analyzer = DataFlowAnalyzer({_node("a.f", "f").__dict__["id"]: _node("a.f", "f")}, [])
    result = analyzer.analyze()
    assert "flow_edges" in result


def test_ownership_patterns_key_present():
    analyzer = DataFlowAnalyzer({}, [])
    result = analyzer.analyze()
    assert "ownership_patterns" in result


def test_no_edges_no_relationships():
    analyzer = DataFlowAnalyzer({}, [])
    result = analyzer.analyze()
    assert result["flow_edges"] == []


def test_flow_edges_for_call():
    functions = {
        "a.producer": _node("a.producer", "producer", params=["buf", "size"]),
        "b.consumer": _node("b.consumer", "consumer", params=["data", "len"]),
    }
    relationships = [_call("a.producer", "b.consumer")]
    analyzer = DataFlowAnalyzer(functions, relationships)
    result = analyzer.analyze()
    assert len(result["flow_edges"]) >= 1


def test_flow_edge_structure():
    functions = {
        "a.src": _node("a.src", "src", params=["x", "y"]),
        "b.dst": _node("b.dst", "dst", params=["a", "b"]),
    }
    relationships = [_call("a.src", "b.dst")]
    analyzer = DataFlowAnalyzer(functions, relationships)
    result = analyzer.analyze()
    edges = result["flow_edges"]
    assert len(edges) >= 1
    edge = edges[0]
    assert "caller" in edge
    assert "callee" in edge
    assert "edge" in edge
    assert edge["caller"] == "a.src"
    assert edge["callee"] == "b.dst"


def test_flow_edge_param_name():
    functions = {
        "a.f": _node("a.f", "f", params=["x"]),
        "b.g": _node("b.g", "g", params=["val"]),
    }
    relationships = [_call("a.f", "b.g")]
    analyzer = DataFlowAnalyzer(functions, relationships)
    result = analyzer.analyze()
    edge_data = result["flow_edges"][0]["edge"]
    assert edge_data["param_name"] == "val"


def test_multiple_params_produce_multiple_edges():
    functions = {
        "a.caller": _node("a.caller", "caller", params=[]),
        "b.callee": _node("b.callee", "callee", params=["p1", "p2", "p3"]),
    }
    relationships = [_call("a.caller", "b.callee")]
    analyzer = DataFlowAnalyzer(functions, relationships)
    result = analyzer.analyze()
    assert len(result["flow_edges"]) == 3


def test_multiple_relationships_produce_edges():
    functions = {
        "a.f": _node("a.f", "f", params=["x"]),
        "b.g": _node("b.g", "g", params=["val"]),
        "c.h": _node("c.h", "h", params=["data"]),
    }
    relationships = [
        _call("a.f", "b.g"),
        _call("a.f", "c.h"),
    ]
    analyzer = DataFlowAnalyzer(functions, relationships)
    result = analyzer.analyze()
    assert len(result["flow_edges"]) >= 2


# ── ownership pattern detection ────────────────────────────────────────────────


def test_malloc_ownership_detected():
    functions = {
        "main.init": _node(
            "main.init", "init", source_code="void init() { int* p = malloc(100); }"
        ),
    }
    analyzer = DataFlowAnalyzer(functions, [])
    result = analyzer.analyze()
    patterns = result["ownership_patterns"]
    assert len(patterns) >= 1
    assert any(p["allocates"] for p in patterns)


def test_malloc_free_pair_detected():
    functions = {
        "main.alloc_and_free": _node(
            "main.alloc_and_free",
            "alloc_and_free",
            source_code="void f() { int* p = malloc(100); free(p); }",
        ),
    }
    analyzer = DataFlowAnalyzer(functions, [])
    result = analyzer.analyze()
    patterns = result["ownership_patterns"]
    assert len(patterns) >= 1
    pat = patterns[0]
    assert pat["allocates"] is True
    assert pat["deallocates"] is True


def test_smart_ptr_detected():
    functions = {
        "a.use_ptr": _node(
            "a.use_ptr", "use_ptr", source_code="void f() { std::unique_ptr<int> p; }"
        ),
    }
    analyzer = DataFlowAnalyzer(functions, [])
    result = analyzer.analyze()
    patterns = result["ownership_patterns"]
    assert len(patterns) >= 1
    assert any(p["uses_smart_ptr"] for p in patterns)


def test_no_ownership_for_clean_function():
    functions = {
        "a.clean": _node("a.clean", "clean", source_code="int add(int a, int b) { return a + b; }"),
    }
    analyzer = DataFlowAnalyzer(functions, [])
    result = analyzer.analyze()
    patterns = result["ownership_patterns"]
    # Clean function should have no concerning ownership patterns
    concerning = [p for p in patterns if p.get("allocates") or p.get("deallocates")]
    assert len(concerning) == 0


def test_ownership_pattern_structure():
    functions = {
        "main.leak": _node("main.leak", "leak", source_code="void leak() { int* p = malloc(10); }"),
    }
    analyzer = DataFlowAnalyzer(functions, [])
    result = analyzer.analyze()
    patterns = result["ownership_patterns"]
    assert len(patterns) >= 1
    pat = patterns[0]
    assert "function" in pat
    assert "file" in pat
    assert "allocates" in pat
    assert "deallocates" in pat
    assert "uses_smart_ptr" in pat
    assert "leak" in pat["function"]
