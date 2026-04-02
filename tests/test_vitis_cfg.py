"""Task 10: Vitis .cfg parser — comprehensive tests"""

from codewiki.src.be.dependency_analyzer.analyzers.vitis_cfg import analyze_vitis_cfg

HLS_CFG = """
[hls]
flow_target=vitis
syn.file=./mm2s.cpp
syn.file_cflags=./mm2s.cpp,-I./include
syn.top=mm2s
syn.output.format=xo
"""

SYSTEM_CFG = """
[connectivity]
nk=mm2s:1:mm2s_1
nk=s2mm:1:s2mm_1
stream_connect=mm2s_1.s:s2mm_1.s
sp=mm2s_1.mem:DDR[0]
sp=s2mm_1.mem:DDR[1]
"""

MULTI_STREAM_CFG = """
[connectivity]
nk=producer:1:prod_1
nk=filter:1:filt_1
nk=consumer:1:cons_1
stream_connect=prod_1.out:filt_1.in
stream_connect=filt_1.out:cons_1.in
sp=prod_1.mem:DDR[0]
sp=cons_1.mem:DDR[1]
sp=cons_1.out_mem:DDR[2]
"""

NON_VITIS_CFG = """
[database]
host=localhost
port=5432
name=mydb
password=secret
"""

PLAIN_APP_CFG = """
[server]
host=0.0.0.0
port=8080

[logging]
level=INFO
file=/var/log/app.log
"""


# ── hls section: top function ──────────────────────────────────────────────────


def test_hls_top_function_extracted():
    nodes, rels = analyze_vitis_cfg("/tmp/hls.cfg", HLS_CFG, "/tmp")
    top_funcs = [n for n in nodes if n.component_type == "hls_top"]
    assert len(top_funcs) >= 1


def test_hls_top_function_name():
    nodes, rels = analyze_vitis_cfg("/tmp/hls.cfg", HLS_CFG, "/tmp")
    top_funcs = [n for n in nodes if n.component_type == "hls_top"]
    assert any(n.name == "mm2s" for n in top_funcs)


def test_hls_top_is_hls_kernel():
    nodes, rels = analyze_vitis_cfg("/tmp/hls.cfg", HLS_CFG, "/tmp")
    top_node = next(n for n in nodes if n.component_type == "hls_top")
    assert top_node.is_hls_kernel is True


# ── hls section: source files ─────────────────────────────────────────────────


def test_hls_source_file_relationship():
    nodes, rels = analyze_vitis_cfg("/tmp/hls.cfg", HLS_CFG, "/tmp")
    src_rels = [r for r in rels if r.relationship_type == "hls_source"]
    assert len(src_rels) >= 1


def test_hls_source_callee_name():
    nodes, rels = analyze_vitis_cfg("/tmp/hls.cfg", HLS_CFG, "/tmp")
    src_rels = [r for r in rels if r.relationship_type == "hls_source"]
    callees = {r.callee for r in src_rels}
    assert any("mm2s.cpp" in c for c in callees)


# ── connectivity section: kernel instances ────────────────────────────────────


def test_kernel_instance_nodes():
    nodes, rels = analyze_vitis_cfg("/tmp/system.cfg", SYSTEM_CFG, "/tmp")
    kernel_instances = [n for n in nodes if n.component_type == "kernel_instance"]
    assert len(kernel_instances) >= 2


def test_kernel_instance_names():
    nodes, rels = analyze_vitis_cfg("/tmp/system.cfg", SYSTEM_CFG, "/tmp")
    kernel_instances = [n for n in nodes if n.component_type == "kernel_instance"]
    names = {n.name for n in kernel_instances}
    assert "mm2s_1" in names
    assert "s2mm_1" in names


# ── connectivity section: stream connections ──────────────────────────────────


def test_stream_connect_relationship():
    nodes, rels = analyze_vitis_cfg("/tmp/system.cfg", SYSTEM_CFG, "/tmp")
    stream_rels = [r for r in rels if r.relationship_type == "stream_connect"]
    assert len(stream_rels) >= 1


def test_stream_connect_direction():
    nodes, rels = analyze_vitis_cfg("/tmp/system.cfg", SYSTEM_CFG, "/tmp")
    stream_rels = [r for r in rels if r.relationship_type == "stream_connect"]
    assert any("mm2s" in r.caller and "s2mm" in r.callee for r in stream_rels)


def test_multiple_stream_connections():
    nodes, rels = analyze_vitis_cfg("/tmp/multi.cfg", MULTI_STREAM_CFG, "/tmp")
    stream_rels = [r for r in rels if r.relationship_type == "stream_connect"]
    assert len(stream_rels) == 2


# ── connectivity section: memory mappings ─────────────────────────────────────


def test_memory_mapping_relationships():
    nodes, rels = analyze_vitis_cfg("/tmp/system.cfg", SYSTEM_CFG, "/tmp")
    mem_rels = [r for r in rels if r.relationship_type == "memory_map"]
    assert len(mem_rels) >= 2


def test_memory_mapping_callees():
    nodes, rels = analyze_vitis_cfg("/tmp/system.cfg", SYSTEM_CFG, "/tmp")
    mem_rels = [r for r in rels if r.relationship_type == "memory_map"]
    callees = {r.callee for r in mem_rels}
    assert any("DDR" in c for c in callees)


def test_multiple_memory_mappings():
    nodes, rels = analyze_vitis_cfg("/tmp/multi.cfg", MULTI_STREAM_CFG, "/tmp")
    mem_rels = [r for r in rels if r.relationship_type == "memory_map"]
    assert len(mem_rels) == 3


# ── content sniffing: non-Vitis cfg returns nothing ───────────────────────────


def test_non_vitis_cfg_returns_empty():
    """A non-Vitis .cfg file should produce no nodes or relationships."""
    nodes, rels = analyze_vitis_cfg("/tmp/app.cfg", NON_VITIS_CFG, "/tmp")
    assert len(nodes) == 0
    assert len(rels) == 0


def test_plain_app_cfg_returns_empty():
    """A plain application config should not be misidentified as Vitis config."""
    nodes, rels = analyze_vitis_cfg("/tmp/server.cfg", PLAIN_APP_CFG, "/tmp")
    assert len(nodes) == 0
    assert len(rels) == 0


def test_vitis_cfg_does_produce_output():
    """Sanity check: real Vitis config should produce nodes."""
    nodes, rels = analyze_vitis_cfg("/tmp/hls.cfg", HLS_CFG, "/tmp")
    assert len(nodes) > 0 or len(rels) > 0
