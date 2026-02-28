from codewiki.src.be.dependency_analyzer.analyzers.vitis_cfg import analyze_vitis_cfg

HLS_CFG = '''
[hls]
flow_target=vitis
syn.file=./mm2s.cpp
syn.file_cflags=./mm2s.cpp,-I./include
syn.top=mm2s
syn.output.format=xo
'''

SYSTEM_CFG = '''
[connectivity]
nk=mm2s:1:mm2s_1
nk=s2mm:1:s2mm_1
stream_connect=mm2s_1.s:s2mm_1.s
sp=mm2s_1.mem:DDR[0]
sp=s2mm_1.mem:DDR[1]
'''


def test_hls_top_function():
    nodes, rels = analyze_vitis_cfg("/tmp/hls.cfg", HLS_CFG, "/tmp")
    top_funcs = [n for n in nodes if n.component_type == "hls_top"]
    assert any(n.name == "mm2s" for n in top_funcs)


def test_hls_source_file():
    nodes, rels = analyze_vitis_cfg("/tmp/hls.cfg", HLS_CFG, "/tmp")
    src_rels = [r for r in rels if r.relationship_type == "hls_source"]
    assert any("mm2s.cpp" in r.callee for r in src_rels)


def test_stream_connect():
    nodes, rels = analyze_vitis_cfg("/tmp/system.cfg", SYSTEM_CFG, "/tmp")
    stream_rels = [r for r in rels if r.relationship_type == "stream_connect"]
    assert len(stream_rels) >= 1
    # mm2s_1.s -> s2mm_1.s
    assert any("mm2s" in r.caller and "s2mm" in r.callee for r in stream_rels)


def test_memory_mapping():
    nodes, rels = analyze_vitis_cfg("/tmp/system.cfg", SYSTEM_CFG, "/tmp")
    mem_rels = [r for r in rels if r.relationship_type == "memory_map"]
    assert len(mem_rels) >= 2
