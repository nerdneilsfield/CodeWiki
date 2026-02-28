"""Task 11: HLS stream parameter and pragma detection — comprehensive tests"""
from codewiki.src.be.dependency_analyzer.analyzers.cpp import analyze_cpp_file

STREAM_CODE = '''
#include <hls_stream.h>

void producer(hls::stream<int>& out, int* mem, int n) {
#pragma HLS INTERFACE axis port=out
#pragma HLS INTERFACE m_axi port=mem bundle=gmem
#pragma HLS INTERFACE s_axilite port=n bundle=control
    for (int i = 0; i < n; i++) {
#pragma HLS PIPELINE II=1
        out.write(mem[i]);
    }
}

void consumer(hls::stream<int>& in, int* mem, int n) {
#pragma HLS INTERFACE axis port=in
#pragma HLS INTERFACE m_axi port=mem bundle=gmem
#pragma HLS INTERFACE s_axilite port=n bundle=control
    for (int i = 0; i < n; i++) {
#pragma HLS PIPELINE II=1
        mem[i] = in.read();
    }
}
'''

MULTI_STREAM_CODE = '''
#include <hls_stream.h>

void filter(hls::stream<int>& in_stream, hls::stream<int>& out_stream) {
#pragma HLS INTERFACE axis port=in_stream
#pragma HLS INTERFACE axis port=out_stream
#pragma HLS PIPELINE
    int val = in_stream.read();
    out_stream.write(val * 2);
}
'''


# ── producer function ──────────────────────────────────────────────────────────

def test_producer_function_extracted():
    nodes, rels = analyze_cpp_file("/tmp/stream.cpp", STREAM_CODE, "/tmp")
    producer = next((n for n in nodes if n.name == "producer"), None)
    assert producer is not None


def test_producer_has_pragmas():
    nodes, rels = analyze_cpp_file("/tmp/stream.cpp", STREAM_CODE, "/tmp")
    producer = next(n for n in nodes if n.name == "producer")
    assert producer.hls_pragmas is not None
    assert len(producer.hls_pragmas) >= 2


def test_producer_axis_pragma():
    nodes, rels = analyze_cpp_file("/tmp/stream.cpp", STREAM_CODE, "/tmp")
    producer = next(n for n in nodes if n.name == "producer")
    axis_pragma = next(
        (p for p in producer.hls_pragmas
         if p.pragma_type == "INTERFACE" and p.params.get("subtype") == "axis"),
        None,
    )
    assert axis_pragma is not None


def test_producer_axis_port_name():
    nodes, rels = analyze_cpp_file("/tmp/stream.cpp", STREAM_CODE, "/tmp")
    producer = next(n for n in nodes if n.name == "producer")
    axis_pragma = next(
        p for p in producer.hls_pragmas
        if p.pragma_type == "INTERFACE" and p.params.get("subtype") == "axis"
    )
    assert axis_pragma.params.get("port") == "out"


def test_producer_m_axi_pragma():
    nodes, rels = analyze_cpp_file("/tmp/stream.cpp", STREAM_CODE, "/tmp")
    producer = next(n for n in nodes if n.name == "producer")
    m_axi = next(
        (p for p in producer.hls_pragmas
         if p.params.get("subtype") == "m_axi"),
        None,
    )
    assert m_axi is not None
    assert m_axi.params.get("bundle") == "gmem"


def test_producer_pipeline_pragma():
    nodes, rels = analyze_cpp_file("/tmp/stream.cpp", STREAM_CODE, "/tmp")
    producer = next(n for n in nodes if n.name == "producer")
    pipeline = next(
        (p for p in producer.hls_pragmas if p.pragma_type == "PIPELINE"), None
    )
    assert pipeline is not None


# ── consumer function ──────────────────────────────────────────────────────────

def test_consumer_function_extracted():
    nodes, rels = analyze_cpp_file("/tmp/stream.cpp", STREAM_CODE, "/tmp")
    consumer = next((n for n in nodes if n.name == "consumer"), None)
    assert consumer is not None


def test_consumer_has_pragmas():
    nodes, rels = analyze_cpp_file("/tmp/stream.cpp", STREAM_CODE, "/tmp")
    consumer = next(n for n in nodes if n.name == "consumer")
    assert consumer.hls_pragmas is not None
    assert len(consumer.hls_pragmas) >= 2


def test_consumer_axis_pragma():
    nodes, rels = analyze_cpp_file("/tmp/stream.cpp", STREAM_CODE, "/tmp")
    consumer = next(n for n in nodes if n.name == "consumer")
    axis_pragma = next(
        (p for p in consumer.hls_pragmas
         if p.pragma_type == "INTERFACE" and p.params.get("subtype") == "axis"),
        None,
    )
    assert axis_pragma is not None


def test_consumer_axis_port_name():
    nodes, rels = analyze_cpp_file("/tmp/stream.cpp", STREAM_CODE, "/tmp")
    consumer = next(n for n in nodes if n.name == "consumer")
    axis_pragma = next(
        p for p in consumer.hls_pragmas
        if p.pragma_type == "INTERFACE" and p.params.get("subtype") == "axis"
    )
    assert axis_pragma.params.get("port") == "in"


def test_consumer_has_params():
    nodes, rels = analyze_cpp_file("/tmp/stream.cpp", STREAM_CODE, "/tmp")
    consumer = next(n for n in nodes if n.name == "consumer")
    assert consumer.parameters is not None
    assert len(consumer.parameters) >= 1


# ── axis hardware semantic ─────────────────────────────────────────────────────

def test_axis_hardware_semantic():
    nodes, rels = analyze_cpp_file("/tmp/stream.cpp", STREAM_CODE, "/tmp")
    producer = next(n for n in nodes if n.name == "producer")
    axis_pragma = next(
        p for p in producer.hls_pragmas
        if p.pragma_type == "INTERFACE" and p.params.get("subtype") == "axis"
    )
    assert len(axis_pragma.hardware_semantic) > 0
    assert "AXI-Stream" in axis_pragma.hardware_semantic or "stream" in axis_pragma.hardware_semantic.lower()


# ── multi-stream filter ────────────────────────────────────────────────────────

def test_multi_stream_both_axis_extracted():
    nodes, rels = analyze_cpp_file("/tmp/multi.cpp", MULTI_STREAM_CODE, "/tmp")
    filt = next((n for n in nodes if n.name == "filter"), None)
    assert filt is not None
    axis_pragmas = [
        p for p in filt.hls_pragmas
        if p.pragma_type == "INTERFACE" and p.params.get("subtype") == "axis"
    ]
    assert len(axis_pragmas) == 2


def test_multi_stream_port_names():
    nodes, rels = analyze_cpp_file("/tmp/multi.cpp", MULTI_STREAM_CODE, "/tmp")
    filt = next(n for n in nodes if n.name == "filter")
    axis_ports = {
        p.params.get("port")
        for p in filt.hls_pragmas
        if p.pragma_type == "INTERFACE" and p.params.get("subtype") == "axis"
    }
    assert "in_stream" in axis_ports
    assert "out_stream" in axis_ports
