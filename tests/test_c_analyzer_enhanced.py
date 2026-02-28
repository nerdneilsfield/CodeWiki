from codewiki.src.be.dependency_analyzer.analyzers.c import analyze_c_file

SAMPLE_C = '''
#include <stdio.h>
#include "myheader.h"

#define MAX_SIZE 100
#define SQUARE(x) ((x) * (x))

typedef struct {
    int x;
    int y;
} Point;

enum Color { RED, GREEN, BLUE };

union Data {
    int i;
    float f;
};

void process(Point* p) {
    printf("x=%d\\n", p->x);
}
'''

def test_include_extraction():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    include_rels = [r for r in rels if r.relationship_type == "include"]
    assert len(include_rels) == 2
    paths = {r.callee for r in include_rels}
    assert "stdio.h" in paths
    assert "myheader.h" in paths

def test_macro_extraction():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    macros = [n for n in nodes if n.component_type == "macro"]
    names = {m.name for m in macros}
    assert "MAX_SIZE" in names
    assert "SQUARE" in names

def test_enum_extraction():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    enums = [n for n in nodes if n.component_type == "enum"]
    assert any(e.name == "Color" for e in enums)

def test_union_extraction():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    unions = [n for n in nodes if n.component_type == "union"]
    assert any(u.name == "Data" for u in unions)

def test_typedef_struct_still_works():
    nodes, rels = analyze_c_file("/tmp/test.c", SAMPLE_C, "/tmp")
    structs = [n for n in nodes if n.component_type == "struct"]
    assert any(s.name == "Point" for s in structs)
