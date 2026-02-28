from codewiki.src.be.dependency_analyzer.analyzers.cpp import analyze_cpp_file

SAMPLE_CPP = '''
#include <vector>
#include "utils.h"

namespace MyLib {

template<typename T>
class Container {
public:
    void add(const T& item);
    T get(int index);
};

template<typename T>
T max_val(T a, T b) {
    return (a > b) ? a : b;
}

}  // namespace MyLib

void demo() {
    MyLib::Container<int> c;
    c.add(42);
    int m = MyLib::max_val<int>(1, 2);
}
'''

def test_include_extraction():
    nodes, rels = analyze_cpp_file("/tmp/test.cpp", SAMPLE_CPP, "/tmp")
    include_rels = [r for r in rels if r.relationship_type == "include"]
    assert len(include_rels) == 2
    paths = {r.callee for r in include_rels}
    assert "vector" in paths
    assert "utils.h" in paths

def test_template_class_extraction():
    nodes, rels = analyze_cpp_file("/tmp/test.cpp", SAMPLE_CPP, "/tmp")
    classes = [n for n in nodes if n.component_type in ("class", "template_class")]
    assert any("Container" in c.name for c in classes)

def test_template_function_extraction():
    nodes, rels = analyze_cpp_file("/tmp/test.cpp", SAMPLE_CPP, "/tmp")
    funcs = [n for n in nodes if n.component_type in ("function", "template_function")]
    assert any("max_val" in f.name for f in funcs)

def test_qualified_call():
    nodes, rels = analyze_cpp_file("/tmp/test.cpp", SAMPLE_CPP, "/tmp")
    calls = [r for r in rels if r.relationship_type == "calls"]
    callee_names = {r.callee.split(".")[-1] for r in calls}
    # Should detect calls like MyLib::max_val or c.add
    assert "max_val" in callee_names or "add" in callee_names
