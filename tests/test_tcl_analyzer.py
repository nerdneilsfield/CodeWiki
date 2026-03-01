"""Task 15: Vitis HLS TCL script analyzer — comprehensive tests"""
from codewiki.src.be.dependency_analyzer.analyzers.tcl import analyze_tcl_file

FULL_SCRIPT = '''
open_project -reset hls_project
set_top mm2s
add_files ./mm2s.cpp
add_files -tb ./mm2s_tb.cpp
add_files ./utils.cpp -cflags "-I./include"
csynth_design
export_design -format ip_catalog -output ./mm2s_export
'''

MULTI_FILE_SCRIPT = '''
open_project my_kernel_proj
set_top compute_kernel
add_files kernel.cpp
add_files helper.cpp
add_files utils/math.cpp
add_files -tb tb/testbench.cpp
csynth_design
export_design -format xo -output ./build/kernel.xo
'''

NO_TOP_SCRIPT = '''
open_project orphan_project
add_files orphan.cpp
csynth_design
'''

EMPTY_SCRIPT = ''


# ── open_project ──────────────────────────────────────────────────────────────

def test_open_project_node_extracted():
    nodes, rels = analyze_tcl_file("/tmp/run_hls.tcl", FULL_SCRIPT, "/tmp")
    project_nodes = [n for n in nodes if n.component_type == "hls_project"]
    assert len(project_nodes) >= 1


def test_open_project_node_name():
    nodes, rels = analyze_tcl_file("/tmp/run_hls.tcl", FULL_SCRIPT, "/tmp")
    project_nodes = [n for n in nodes if n.component_type == "hls_project"]
    assert any(n.name == "hls_project" for n in project_nodes)


def test_open_project_node_has_file_path():
    nodes, rels = analyze_tcl_file("/tmp/run_hls.tcl", FULL_SCRIPT, "/tmp")
    project = next(n for n in nodes if n.component_type == "hls_project")
    assert project.file_path == "/tmp/run_hls.tcl"


# ── set_top ────────────────────────────────────────────────────────────────────

def test_set_top_node_extracted():
    nodes, rels = analyze_tcl_file("/tmp/run_hls.tcl", FULL_SCRIPT, "/tmp")
    top_nodes = [n for n in nodes if n.component_type == "hls_top"]
    assert len(top_nodes) >= 1


def test_set_top_name():
    nodes, rels = analyze_tcl_file("/tmp/run_hls.tcl", FULL_SCRIPT, "/tmp")
    top_nodes = [n for n in nodes if n.component_type == "hls_top"]
    assert any(n.name == "mm2s" for n in top_nodes)


def test_set_top_is_hls_kernel():
    nodes, rels = analyze_tcl_file("/tmp/run_hls.tcl", FULL_SCRIPT, "/tmp")
    top = next(n for n in nodes if n.component_type == "hls_top")
    assert top.is_hls_kernel is True


def test_set_top_different_kernel():
    nodes, rels = analyze_tcl_file("/tmp/run2.tcl", MULTI_FILE_SCRIPT, "/tmp")
    top = next(n for n in nodes if n.component_type == "hls_top")
    assert top.name == "compute_kernel"


# ── add_files ─────────────────────────────────────────────────────────────────

def test_add_files_produces_hls_source_rels():
    nodes, rels = analyze_tcl_file("/tmp/run_hls.tcl", FULL_SCRIPT, "/tmp")
    src_rels = [r for r in rels if r.relationship_type == "hls_source"]
    assert len(src_rels) >= 1


def test_add_files_source_callee():
    nodes, rels = analyze_tcl_file("/tmp/run_hls.tcl", FULL_SCRIPT, "/tmp")
    src_rels = [r for r in rels if r.relationship_type == "hls_source"]
    callees = {r.callee for r in src_rels}
    assert any("mm2s.cpp" in c for c in callees)


def test_add_files_testbench_excluded():
    """add_files -tb should NOT create hls_source relationships."""
    nodes, rels = analyze_tcl_file("/tmp/run_hls.tcl", FULL_SCRIPT, "/tmp")
    src_rels = [r for r in rels if r.relationship_type == "hls_source"]
    callees = {r.callee for r in src_rels}
    assert not any("tb" in c.lower() or "testbench" in c.lower() for c in callees)


def test_add_files_cflags_not_included_as_source():
    """The -cflags flag value should not be included as a source file."""
    nodes, rels = analyze_tcl_file("/tmp/run_hls.tcl", FULL_SCRIPT, "/tmp")
    src_rels = [r for r in rels if r.relationship_type == "hls_source"]
    callees = {r.callee for r in src_rels}
    # "-I./include" should not appear as a source file
    assert not any(c.startswith("-") for c in callees)
    assert not any("include" == c.strip("./") for c in callees)


def test_add_files_utils_included():
    """utils.cpp (non-testbench, has -cflags) should still be included."""
    nodes, rels = analyze_tcl_file("/tmp/run_hls.tcl", FULL_SCRIPT, "/tmp")
    src_rels = [r for r in rels if r.relationship_type == "hls_source"]
    callees = {r.callee for r in src_rels}
    assert any("utils.cpp" in c for c in callees)


def test_add_files_multiple_sources():
    nodes, rels = analyze_tcl_file("/tmp/run2.tcl", MULTI_FILE_SCRIPT, "/tmp")
    src_rels = [r for r in rels if r.relationship_type == "hls_source"]
    assert len(src_rels) >= 3
    callees = {r.callee for r in src_rels}
    assert any("kernel.cpp" in c for c in callees)
    assert any("helper.cpp" in c for c in callees)


def test_add_files_caller_is_top():
    nodes, rels = analyze_tcl_file("/tmp/run_hls.tcl", FULL_SCRIPT, "/tmp")
    src_rels = [r for r in rels if r.relationship_type == "hls_source"]
    for r in src_rels:
        # Caller should reference the top function set by set_top
        assert "mm2s" in r.caller or "tcl" in r.caller.lower()


# ── csynth_design ─────────────────────────────────────────────────────────────

def test_csynth_design_relationship():
    nodes, rels = analyze_tcl_file("/tmp/run_hls.tcl", FULL_SCRIPT, "/tmp")
    synth_rels = [r for r in rels if r.relationship_type == "hls_synth"]
    assert len(synth_rels) >= 1


def test_csynth_design_callee():
    nodes, rels = analyze_tcl_file("/tmp/run_hls.tcl", FULL_SCRIPT, "/tmp")
    synth_rels = [r for r in rels if r.relationship_type == "hls_synth"]
    assert any(r.callee == "csynth_design" for r in synth_rels)


def test_csynth_design_caller_is_top():
    nodes, rels = analyze_tcl_file("/tmp/run_hls.tcl", FULL_SCRIPT, "/tmp")
    synth_rels = [r for r in rels if r.relationship_type == "hls_synth"]
    for r in synth_rels:
        assert "mm2s" in r.caller


# ── export_design ─────────────────────────────────────────────────────────────

def test_export_design_relationship():
    nodes, rels = analyze_tcl_file("/tmp/run_hls.tcl", FULL_SCRIPT, "/tmp")
    export_rels = [r for r in rels if r.relationship_type == "hls_export"]
    assert len(export_rels) >= 1


def test_export_design_callee_is_output():
    nodes, rels = analyze_tcl_file("/tmp/run_hls.tcl", FULL_SCRIPT, "/tmp")
    export_rels = [r for r in rels if r.relationship_type == "hls_export"]
    assert any("export" in r.callee or "xo" in r.callee or "ip" in r.callee.lower()
               for r in export_rels)


def test_export_design_xo_format():
    nodes, rels = analyze_tcl_file("/tmp/run2.tcl", MULTI_FILE_SCRIPT, "/tmp")
    export_rels = [r for r in rels if r.relationship_type == "hls_export"]
    assert len(export_rels) >= 1
    assert any("kernel.xo" in r.callee for r in export_rels)


# ── no set_top fallback ────────────────────────────────────────────────────────

def test_no_set_top_still_produces_source_rels():
    """When set_top is absent, add_files should still produce relationships."""
    nodes, rels = analyze_tcl_file("/tmp/orphan.tcl", NO_TOP_SCRIPT, "/tmp")
    src_rels = [r for r in rels if r.relationship_type == "hls_source"]
    assert len(src_rels) >= 1


def test_no_crash_on_empty_script():
    nodes, rels = analyze_tcl_file("/tmp/empty.tcl", EMPTY_SCRIPT, "/tmp")
    assert nodes == []
    assert rels == []
