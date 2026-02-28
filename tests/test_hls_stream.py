from codewiki.src.be.dependency_analyzer.analyzers.cpp import analyze_cpp_file

STREAM_CODE = '''
#include <hls_stream.h>

void producer(hls::stream<int>& out, int* mem, int n) {
#pragma HLS INTERFACE axis port=out
    for (int i = 0; i < n; i++) {
#pragma HLS PIPELINE II=1
        out.write(mem[i]);
    }
}

void consumer(hls::stream<int>& in, int* mem, int n) {
#pragma HLS INTERFACE axis port=in
    for (int i = 0; i < n; i++) {
#pragma HLS PIPELINE II=1
        mem[i] = in.read();
    }
}
'''


def test_stream_parameter_detection():
    nodes, rels = analyze_cpp_file("/tmp/stream.cpp", STREAM_CODE, "/tmp")
    producer = next(n for n in nodes if n.name == "producer")
    # Should detect hls::stream parameter
    assert producer.hls_pragmas is not None
    # INTERFACE axis on 'out' port
    axis_pragma = next((p for p in producer.hls_pragmas if p.pragma_type == "INTERFACE" and p.params.get("subtype") == "axis"), None)
    assert axis_pragma is not None
