from codewiki.src.be.dependency_analyzer.analyzers.cpp import analyze_cpp_file

HLS_KERNEL = '''
extern "C" {
void mm2s(int* mem, int size) {
#pragma HLS INTERFACE m_axi port=mem offset=slave bundle=gmem
#pragma HLS INTERFACE s_axilite port=size bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
    for(int i = 0; i < size; i++) {
#pragma HLS PIPELINE II=1
        // process
    }
}
}
'''


def test_pragma_extraction():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", HLS_KERNEL, "/tmp")
    func = next((n for n in nodes if n.name == "mm2s"), None)
    assert func is not None
    assert func.hls_pragmas is not None
    assert len(func.hls_pragmas) >= 3
    # Check INTERFACE pragma
    iface_pragmas = [p for p in func.hls_pragmas if p.pragma_type == "INTERFACE"]
    assert len(iface_pragmas) >= 2
    # Check PIPELINE pragma
    pipeline_pragmas = [p for p in func.hls_pragmas if p.pragma_type == "PIPELINE"]
    assert len(pipeline_pragmas) >= 1


def test_pragma_params():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", HLS_KERNEL, "/tmp")
    func = next(n for n in nodes if n.name == "mm2s")
    m_axi = next(p for p in func.hls_pragmas if p.params.get("port") == "mem")
    assert m_axi.pragma_type == "INTERFACE"
    assert m_axi.params["bundle"] == "gmem"
    assert "AXI" in m_axi.hardware_semantic or "axi" in m_axi.hardware_semantic.lower()


def test_extern_c_kernel_detection():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", HLS_KERNEL, "/tmp")
    func = next(n for n in nodes if n.name == "mm2s")
    assert func.is_hls_kernel is True
