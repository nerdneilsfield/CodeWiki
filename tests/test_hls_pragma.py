"""Task 9: HLS pragma extraction — comprehensive tests"""
from codewiki.src.be.dependency_analyzer.analyzers.cpp import analyze_cpp_file

HLS_KERNEL = '''
extern "C" {
void mm2s(int* mem, int size) {
#pragma HLS INTERFACE m_axi port=mem offset=slave bundle=gmem
#pragma HLS INTERFACE s_axilite port=size bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
    for(int i = 0; i < size; i++) {
#pragma HLS PIPELINE II=1
    }
}
}
'''

COMPLEX_KERNEL = '''
extern "C" {
void kernel(int* a, int* b, hls::stream<int>& fifo) {
#pragma HLS INTERFACE m_axi port=a bundle=gmem0
#pragma HLS INTERFACE m_axi port=b bundle=gmem1
#pragma HLS INTERFACE axis port=fifo
#pragma HLS INTERFACE s_axilite port=return
#pragma HLS DATAFLOW
    int arr[16];
#pragma HLS ARRAY_PARTITION variable=arr complete dim=1
    for(int i=0;i<100;i++){
#pragma HLS PIPELINE II=1
#pragma HLS UNROLL factor=4
    }
}
}
'''

NO_EXTERN_KERNEL = '''
void regular_func(int* mem, int size) {
#pragma HLS INTERFACE m_axi port=mem bundle=gmem
#pragma HLS PIPELINE
}
'''


# ── basic pragma extraction ────────────────────────────────────────────────────

def test_pragma_count():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", HLS_KERNEL, "/tmp")
    func = next(n for n in nodes if n.name == "mm2s")
    assert func.hls_pragmas is not None
    assert len(func.hls_pragmas) >= 3


def test_interface_pragmas_extracted():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", HLS_KERNEL, "/tmp")
    func = next(n for n in nodes if n.name == "mm2s")
    iface_pragmas = [p for p in func.hls_pragmas if p.pragma_type == "INTERFACE"]
    assert len(iface_pragmas) >= 2


def test_pipeline_pragma_extracted():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", HLS_KERNEL, "/tmp")
    func = next(n for n in nodes if n.name == "mm2s")
    pipeline_pragmas = [p for p in func.hls_pragmas if p.pragma_type == "PIPELINE"]
    assert len(pipeline_pragmas) >= 1


# ── pragma parameters ─────────────────────────────────────────────────────────

def test_m_axi_port_param():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", HLS_KERNEL, "/tmp")
    func = next(n for n in nodes if n.name == "mm2s")
    m_axi = next(p for p in func.hls_pragmas if p.params.get("port") == "mem")
    assert m_axi.pragma_type == "INTERFACE"
    assert m_axi.params["bundle"] == "gmem"


def test_s_axilite_params():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", HLS_KERNEL, "/tmp")
    func = next(n for n in nodes if n.name == "mm2s")
    s_axilite = [p for p in func.hls_pragmas
                 if p.params.get("subtype") == "s_axilite"]
    assert len(s_axilite) >= 1
    assert any(p.params.get("bundle") == "control" for p in s_axilite)


def test_pipeline_ii_param():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", HLS_KERNEL, "/tmp")
    func = next(n for n in nodes if n.name == "mm2s")
    pipeline = next(p for p in func.hls_pragmas if p.pragma_type == "PIPELINE")
    assert pipeline.params.get("ii") == "1"


# ── hardware semantics ─────────────────────────────────────────────────────────

def test_m_axi_hardware_semantic():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", HLS_KERNEL, "/tmp")
    func = next(n for n in nodes if n.name == "mm2s")
    m_axi = next(p for p in func.hls_pragmas if p.params.get("subtype") == "m_axi")
    assert "AXI" in m_axi.hardware_semantic


def test_s_axilite_hardware_semantic():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", HLS_KERNEL, "/tmp")
    func = next(n for n in nodes if n.name == "mm2s")
    s_axilite = next(p for p in func.hls_pragmas if p.params.get("subtype") == "s_axilite")
    assert "AXI-Lite" in s_axilite.hardware_semantic or "control" in s_axilite.hardware_semantic.lower()


def test_pipeline_hardware_semantic():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", HLS_KERNEL, "/tmp")
    func = next(n for n in nodes if n.name == "mm2s")
    pipeline = next(p for p in func.hls_pragmas if p.pragma_type == "PIPELINE")
    assert len(pipeline.hardware_semantic) > 0


# ── extern C kernel detection ──────────────────────────────────────────────────

def test_extern_c_is_hls_kernel():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", HLS_KERNEL, "/tmp")
    func = next(n for n in nodes if n.name == "mm2s")
    assert func.is_hls_kernel is True


def test_non_extern_c_not_hls_kernel():
    nodes, rels = analyze_cpp_file("/tmp/nok.cpp", NO_EXTERN_KERNEL, "/tmp")
    func = next((n for n in nodes if n.name == "regular_func"), None)
    if func is not None:
        assert func.is_hls_kernel is False


# ── complex pragma types ───────────────────────────────────────────────────────

def test_dataflow_pragma_extracted():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", COMPLEX_KERNEL, "/tmp")
    func = next(n for n in nodes if n.name == "kernel")
    dataflow_pragmas = [p for p in func.hls_pragmas if p.pragma_type == "DATAFLOW"]
    assert len(dataflow_pragmas) >= 1


def test_dataflow_hardware_semantic():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", COMPLEX_KERNEL, "/tmp")
    func = next(n for n in nodes if n.name == "kernel")
    dataflow = next(p for p in func.hls_pragmas if p.pragma_type == "DATAFLOW")
    assert len(dataflow.hardware_semantic) > 0


def test_unroll_pragma_extracted():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", COMPLEX_KERNEL, "/tmp")
    func = next(n for n in nodes if n.name == "kernel")
    unroll_pragmas = [p for p in func.hls_pragmas if p.pragma_type == "UNROLL"]
    assert len(unroll_pragmas) >= 1
    assert unroll_pragmas[0].params.get("factor") == "4"


def test_array_partition_pragma_extracted():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", COMPLEX_KERNEL, "/tmp")
    func = next(n for n in nodes if n.name == "kernel")
    arr_part = [p for p in func.hls_pragmas if p.pragma_type == "ARRAY_PARTITION"]
    assert len(arr_part) >= 1
    assert arr_part[0].params.get("variable") == "arr"


def test_axis_interface_extracted():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", COMPLEX_KERNEL, "/tmp")
    func = next(n for n in nodes if n.name == "kernel")
    axis_pragmas = [p for p in func.hls_pragmas
                    if p.pragma_type == "INTERFACE" and p.params.get("subtype") == "axis"]
    assert len(axis_pragmas) >= 1
