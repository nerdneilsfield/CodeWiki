from codewiki.src.be.dependency_analyzer.analyzers.c import analyze_c_file
from codewiki.src.be.dependency_analyzer.analyzers.cpp import analyze_cpp_file

def test_c_param_types():
    code = '''
void process(int* data, const char* name, int count) {
    // body
}
'''
    nodes, rels = analyze_c_file("/tmp/test.c", code, "/tmp")
    func = next(n for n in nodes if n.name == "process")
    assert func.parameters is not None
    assert len(func.parameters) == 3
    param_names = [p if isinstance(p, str) else p.get("name", p) for p in func.parameters]
    assert "data" in param_names
    assert "name" in param_names
    assert "count" in param_names

def test_cpp_ownership_hints():
    code = '''
#include <memory>
void transfer(std::unique_ptr<int> ptr) {}
void share(std::shared_ptr<int> ptr) {}
void borrow(const int& ref) {}
void mutate(int* ptr) {}
'''
    nodes, rels = analyze_cpp_file("/tmp/test.cpp", code, "/tmp")
    funcs = {n.name: n for n in nodes if n.component_type in ("function", "method")}

    assert funcs["transfer"].parameters is not None
    assert funcs["borrow"].parameters is not None
