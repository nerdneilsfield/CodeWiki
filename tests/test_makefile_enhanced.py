"""Task 5: Makefile analyzer enhancements — comprehensive tests"""

import pytest

pytest.importorskip(
    "tree_sitter_make", reason="tree-sitter-make not installed; pip install 'codewiki[make]'"
)

from codewiki.src.be.dependency_analyzer.analyzers.makefile import analyze_makefile_file

SAMPLE_MAKE = """
CC = gcc

main.o: main.c utils.h config.h
\t$(CC) -c main.c

utils.o: utils.c utils.h
\t$(CC) -c utils.c

all: main.o utils.o
\t$(CC) -o app main.o utils.o
"""

VPP_MAKE = """
PLATFORM = xilinx_u200_xdma_201830_2

all: kernel.xo host.exe

kernel.xo: kernel.cpp
\tv++ -c -k mm2s --platform $(PLATFORM) -o kernel.xo kernel.cpp

host.exe: host.cpp
\tg++ -O2 -o host.exe host.cpp

main.o: main.c utils.h
\tgcc -c main.c
"""


# ── target extraction ──────────────────────────────────────────────────────────


def test_target_extraction():
    nodes, rels = analyze_makefile_file("/tmp/Makefile", SAMPLE_MAKE, "/tmp")
    target_names = {n.name for n in nodes}
    assert "all" in target_names or "main.o" in target_names


def test_multiple_targets_extracted():
    nodes, rels = analyze_makefile_file("/tmp/Makefile", SAMPLE_MAKE, "/tmp")
    targets = [n for n in nodes if n.component_type == "target"]
    assert len(targets) >= 2


def test_target_has_file_path():
    nodes, rels = analyze_makefile_file("/tmp/Makefile", SAMPLE_MAKE, "/tmp")
    for n in nodes:
        assert n.file_path == "/tmp/Makefile"


# ── dependency classification ──────────────────────────────────────────────────


def test_header_dep_classification():
    nodes, rels = analyze_makefile_file("/tmp/Makefile", SAMPLE_MAKE, "/tmp")
    header_deps = [r for r in rels if r.relationship_type == "header_dep"]
    header_callees = {r.callee for r in header_deps}
    assert "utils.h" in header_callees or "config.h" in header_callees


def test_compile_dep_classification():
    nodes, rels = analyze_makefile_file("/tmp/Makefile", SAMPLE_MAKE, "/tmp")
    compile_deps = [r for r in rels if r.relationship_type == "compile_dep"]
    compile_callees = {r.callee for r in compile_deps}
    assert "main.c" in compile_callees or "utils.c" in compile_callees


def test_target_dep_classification():
    nodes, rels = analyze_makefile_file("/tmp/Makefile", SAMPLE_MAKE, "/tmp")
    target_deps = [r for r in rels if r.relationship_type == "target_dep"]
    assert len(target_deps) >= 1


def test_relationship_type_always_set():
    nodes, rels = analyze_makefile_file("/tmp/Makefile", SAMPLE_MAKE, "/tmp")
    for r in rels:
        assert r.relationship_type is not None
        assert r.relationship_type in ("header_dep", "compile_dep", "target_dep", "hls_compile")


# ── v++/HLS detection ─────────────────────────────────────────────────────────


def test_vpp_hls_compile_detected():
    nodes, rels = analyze_makefile_file("/tmp/Makefile", VPP_MAKE, "/tmp")
    hls_rels = [r for r in rels if r.relationship_type == "hls_compile"]
    assert len(hls_rels) >= 1


def test_vpp_hls_compile_callee():
    nodes, rels = analyze_makefile_file("/tmp/Makefile", VPP_MAKE, "/tmp")
    hls_rels = [r for r in rels if r.relationship_type == "hls_compile"]
    callee_names = {r.callee for r in hls_rels}
    assert "v++" in callee_names


def test_vpp_kernel_xo_target_exists():
    nodes, rels = analyze_makefile_file("/tmp/Makefile", VPP_MAKE, "/tmp")
    target_names = {n.name for n in nodes}
    assert "kernel.xo" in target_names


def test_vpp_source_dep_for_kernel():
    nodes, rels = analyze_makefile_file("/tmp/Makefile", VPP_MAKE, "/tmp")
    deps = [r for r in rels if "kernel.xo" in r.caller]
    callee_names = {r.callee for r in deps}
    assert "kernel.cpp" in callee_names


# ── mixed targets ──────────────────────────────────────────────────────────────


def test_mixed_vpp_gcc_targets():
    nodes, rels = analyze_makefile_file("/tmp/Makefile", VPP_MAKE, "/tmp")
    target_names = {n.name for n in nodes}
    assert "kernel.xo" in target_names
    assert "host.exe" in target_names


def test_no_crash_on_empty_file():
    nodes, rels = analyze_makefile_file("/tmp/Makefile", "", "/tmp")
    assert nodes == []
    assert rels == []
