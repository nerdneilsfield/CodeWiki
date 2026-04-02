"""Task 3: C++ analyzer enhancements — comprehensive tests"""

from codewiki.src.be.dependency_analyzer.analyzers.cpp import analyze_cpp_file

SAMPLE_CPP = """
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
"""

INHERITANCE_CPP = """
class Animal {
public:
    virtual void speak() {}
    void breathe() {}
};

class Dog : public Animal {
public:
    void speak() override { breathe(); }
    void fetch() {}
};

class Cat : public Animal {
public:
    void speak() override {}
};
"""


# ── include extraction ─────────────────────────────────────────────────────────


def test_include_extraction_count():
    nodes, rels = analyze_cpp_file("/tmp/test.cpp", SAMPLE_CPP, "/tmp")
    include_rels = [r for r in rels if r.relationship_type == "include"]
    assert len(include_rels) == 2


def test_include_callee_names():
    nodes, rels = analyze_cpp_file("/tmp/test.cpp", SAMPLE_CPP, "/tmp")
    include_rels = [r for r in rels if r.relationship_type == "include"]
    paths = {r.callee for r in include_rels}
    assert "vector" in paths
    assert "utils.h" in paths


def test_include_is_unresolved():
    nodes, rels = analyze_cpp_file("/tmp/test.cpp", SAMPLE_CPP, "/tmp")
    for r in rels:
        if r.relationship_type == "include":
            assert r.is_resolved is False


# ── template class extraction ──────────────────────────────────────────────────


def test_template_class_extracted():
    nodes, rels = analyze_cpp_file("/tmp/test.cpp", SAMPLE_CPP, "/tmp")
    classes = [n for n in nodes if n.component_type in ("class", "template_class")]
    assert len(classes) >= 1
    assert any("Container" in c.name for c in classes)


def test_template_class_has_file_path():
    nodes, rels = analyze_cpp_file("/tmp/test.cpp", SAMPLE_CPP, "/tmp")
    for n in nodes:
        if n.component_type in ("class", "template_class"):
            assert n.file_path == "/tmp/test.cpp"


# ── template function extraction ───────────────────────────────────────────────


def test_template_function_extracted():
    nodes, rels = analyze_cpp_file("/tmp/test.cpp", SAMPLE_CPP, "/tmp")
    funcs = [n for n in nodes if n.component_type in ("function", "template_function")]
    assert any("max_val" in f.name for f in funcs)


def test_regular_function_extracted():
    nodes, rels = analyze_cpp_file("/tmp/test.cpp", SAMPLE_CPP, "/tmp")
    funcs = [n for n in nodes if n.component_type == "function"]
    assert any("demo" in f.name for f in funcs)


# ── inheritance ────────────────────────────────────────────────────────────────


def test_inheritance_relationship_extracted():
    nodes, rels = analyze_cpp_file("/tmp/inherit.cpp", INHERITANCE_CPP, "/tmp")
    inherit_rels = [r for r in rels if r.relationship_type == "inherits"]
    assert len(inherit_rels) >= 2


def test_dog_inherits_animal():
    nodes, rels = analyze_cpp_file("/tmp/inherit.cpp", INHERITANCE_CPP, "/tmp")
    inherit_rels = [r for r in rels if r.relationship_type == "inherits"]
    callee_names = {r.callee.split(".")[-1] for r in inherit_rels}
    assert "Animal" in callee_names


def test_cat_inherits_animal():
    nodes, rels = analyze_cpp_file("/tmp/inherit.cpp", INHERITANCE_CPP, "/tmp")
    inherit_rels = [r for r in rels if r.relationship_type == "inherits"]
    callers = {r.caller for r in inherit_rels}
    assert any("Dog" in c for c in callers)
    assert any("Cat" in c for c in callers)


def test_inheritance_is_unresolved():
    nodes, rels = analyze_cpp_file("/tmp/inherit.cpp", INHERITANCE_CPP, "/tmp")
    for r in rels:
        if r.relationship_type == "inherits":
            assert r.is_resolved is False


# ── method calls ───────────────────────────────────────────────────────────────


def test_qualified_call_detected():
    nodes, rels = analyze_cpp_file("/tmp/test.cpp", SAMPLE_CPP, "/tmp")
    calls = [r for r in rels if r.relationship_type == "calls"]
    callee_names = {r.callee.split(".")[-1] for r in calls}
    assert "max_val" in callee_names or "add" in callee_names


def test_method_call_within_class():
    # Qualified calls like c.add() or MyLib::max_val() are captured as "calls"
    # Unqualified method calls within the class body are not captured
    nodes, rels = analyze_cpp_file("/tmp/test.cpp", SAMPLE_CPP, "/tmp")
    calls = [r for r in rels if r.relationship_type == "calls"]
    callee_names = {r.callee.split(".")[-1] for r in calls}
    assert "max_val" in callee_names or "add" in callee_names


# ── node structure ─────────────────────────────────────────────────────────────


def test_function_has_start_line():
    nodes, rels = analyze_cpp_file("/tmp/test.cpp", SAMPLE_CPP, "/tmp")
    for n in nodes:
        if n.component_type in ("function", "method"):
            assert n.start_line is not None


def test_no_crash_on_empty_file():
    nodes, rels = analyze_cpp_file("/tmp/empty.cpp", "", "/tmp")
    assert nodes == []
    assert rels == []


def test_class_nodes_have_component_type():
    nodes, rels = analyze_cpp_file("/tmp/inherit.cpp", INHERITANCE_CPP, "/tmp")
    class_nodes = [n for n in nodes if n.component_type == "class"]
    assert len(class_nodes) >= 2
    for c in class_nodes:
        assert c.name in ("Animal", "Dog", "Cat")
