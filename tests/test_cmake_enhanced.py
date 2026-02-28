from codewiki.src.be.dependency_analyzer.analyzers.cmake import analyze_cmake_file

SAMPLE_CMAKE = '''
cmake_minimum_required(VERSION 3.10)
project(MyApp)

add_executable(myapp src/main.cpp src/utils.cpp src/parser.cpp)
add_library(mylib STATIC src/lib.cpp src/helper.cpp)
target_link_libraries(myapp mylib pthread)
'''

def test_add_executable_source_extraction():
    nodes, rels = analyze_cmake_file("/tmp/CMakeLists.txt", SAMPLE_CMAKE, "/tmp")
    compile_rels = [r for r in rels if r.relationship_type == "compile_target"]
    myapp_sources = [r.callee for r in compile_rels if "myapp" in r.caller]
    assert "src/main.cpp" in myapp_sources
    assert "src/utils.cpp" in myapp_sources
    assert "src/parser.cpp" in myapp_sources

def test_add_library_source_extraction():
    nodes, rels = analyze_cmake_file("/tmp/CMakeLists.txt", SAMPLE_CMAKE, "/tmp")
    compile_rels = [r for r in rels if r.relationship_type == "compile_target"]
    mylib_sources = [r.callee for r in compile_rels if "mylib" in r.caller]
    assert "src/lib.cpp" in mylib_sources
    assert "src/helper.cpp" in mylib_sources

def test_target_link_libraries():
    nodes, rels = analyze_cmake_file("/tmp/CMakeLists.txt", SAMPLE_CMAKE, "/tmp")
    link_rels = [r for r in rels if r.relationship_type == "link_dependency"]
    assert any("mylib" in r.callee for r in link_rels)
