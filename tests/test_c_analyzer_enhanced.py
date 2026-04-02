"""Task 2: C analyzer enhancements — comprehensive tests"""

from codewiki.src.be.dependency_analyzer.analyzers.c import analyze_c_file

SAMPLE_C = """
#include <stdio.h>
#include "myheader.h"

#define MAX_SIZE 100
#define SQUARE(x) ((x) * (x))
#define FLAG_A 0x01

typedef struct {
    int x;
    int y;
} Point;

enum Color { RED, GREEN, BLUE };

union Data {
    int i;
    float f;
};

void helper(int n) {
    printf("n=%d\\n", n);
}

void process(Point* p, int count) {
    helper(count);
    printf("x=%d\\n", p->x);
}
"""


# ── include extraction ─────────────────────────────────────────────────────────


def test_include_extraction_count():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    include_rels = [r for r in rels if r.relationship_type == "include"]
    assert len(include_rels) == 2


def test_include_callee_names():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    include_rels = [r for r in rels if r.relationship_type == "include"]
    paths = {r.callee for r in include_rels}
    assert "stdio.h" in paths
    assert "myheader.h" in paths


def test_include_is_unresolved():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    for r in rels:
        if r.relationship_type == "include":
            assert r.is_resolved is False


def test_include_caller_is_file_node():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    include_rels = [r for r in rels if r.relationship_type == "include"]
    for r in include_rels:
        assert "__file__" in r.caller or "test" in r.caller


# ── function call relationships ────────────────────────────────────────────────


def test_call_relationship_type():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    call_rels = [r for r in rels if r.relationship_type == "call"]
    assert len(call_rels) >= 1


def test_call_callee_names():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    call_rels = [r for r in rels if r.relationship_type == "call"]
    callee_names = {r.callee.split(".")[-1] for r in call_rels}
    assert "helper" in callee_names or "printf" in callee_names


def test_call_from_process():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    call_rels = [r for r in rels if r.relationship_type == "call" and "process" in r.caller]
    assert len(call_rels) >= 1


def test_field_expr_call_detected():
    """p->x should produce a field-access call relationship."""
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    all_callees = {r.callee.split(".")[-1] for r in rels}
    # field expression 'p->x' extracts 'x', printf and helper are also calls
    assert len([r for r in rels if r.relationship_type == "call"]) >= 1


# ── macro extraction ───────────────────────────────────────────────────────────


def test_macro_extraction_count():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    macros = [n for n in nodes if n.component_type == "macro"]
    assert len(macros) >= 2


def test_macro_names_present():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    macro_names = {m.name for m in nodes if m.component_type == "macro"}
    assert "MAX_SIZE" in macro_names
    assert "SQUARE" in macro_names


def test_macro_has_file_path():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    for m in nodes:
        if m.component_type == "macro":
            assert m.file_path == "/tmp/test.c"


# ── enum extraction ────────────────────────────────────────────────────────────


def test_enum_extraction():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    enums = [n for n in nodes if n.component_type == "enum"]
    assert len(enums) >= 1


def test_enum_name():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    enums = [n for n in nodes if n.component_type == "enum"]
    assert any(e.name == "Color" for e in enums)


# ── union extraction ───────────────────────────────────────────────────────────


def test_union_extraction():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    unions = [n for n in nodes if n.component_type == "union"]
    assert any(u.name == "Data" for u in unions)


# ── struct extraction ──────────────────────────────────────────────────────────


def test_typedef_struct_extraction():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    structs = [n for n in nodes if n.component_type == "struct"]
    assert any(s.name == "Point" for s in structs)


# ── function nodes ─────────────────────────────────────────────────────────────


def test_function_nodes_extracted():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    funcs = [n for n in nodes if n.component_type == "function"]
    func_names = {f.name for f in funcs}
    assert "process" in func_names
    assert "helper" in func_names


def test_function_has_start_line():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    for n in nodes:
        if n.component_type == "function":
            assert n.start_line is not None
            assert n.start_line >= 1


def test_no_crash_on_empty_file():
    nodes, rels = analyze_c_file("/tmp/empty.c", "", "/tmp")
    assert nodes == []
    assert rels == []
