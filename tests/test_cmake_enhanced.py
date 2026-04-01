"""Task 4: CMake analyzer enhancements — comprehensive tests"""
import pytest

pytest.importorskip("tree_sitter_cmake", reason="tree-sitter-cmake not installed; pip install 'codewiki[cmake]'")

from codewiki.src.be.dependency_analyzer.analyzers.cmake import analyze_cmake_file

SAMPLE_CMAKE = '''
cmake_minimum_required(VERSION 3.10)
project(MyApp)

add_executable(myapp src/main.cpp src/utils.cpp src/parser.cpp)
add_library(mylib STATIC src/lib.cpp src/helper.cpp)
target_link_libraries(myapp mylib pthread)
'''

MULTI_TARGET_CMAKE = '''
cmake_minimum_required(VERSION 3.14)
project(MultiLib)

add_executable(server src/server.cpp src/network.cpp)
add_executable(client src/client.cpp)
add_library(core SHARED src/core.cpp src/io.cpp)
target_link_libraries(server core)
target_link_libraries(client core)
'''


# ── add_executable source extraction ──────────────────────────────────────────

def test_add_executable_source_count():
    nodes, rels = analyze_cmake_file("/tmp/CMakeLists.txt", SAMPLE_CMAKE, "/tmp")
    compile_rels = [r for r in rels if r.relationship_type == "compile_target"]
    myapp_sources = [r.callee for r in compile_rels if "myapp" in r.caller]
    assert len(myapp_sources) == 3


def test_add_executable_source_paths():
    nodes, rels = analyze_cmake_file("/tmp/CMakeLists.txt", SAMPLE_CMAKE, "/tmp")
    compile_rels = [r for r in rels if r.relationship_type == "compile_target"]
    myapp_sources = [r.callee for r in compile_rels if "myapp" in r.caller]
    assert "src/main.cpp" in myapp_sources
    assert "src/utils.cpp" in myapp_sources
    assert "src/parser.cpp" in myapp_sources


def test_add_executable_caller_contains_target():
    nodes, rels = analyze_cmake_file("/tmp/CMakeLists.txt", SAMPLE_CMAKE, "/tmp")
    compile_rels = [r for r in rels if r.relationship_type == "compile_target"]
    for r in compile_rels:
        assert r.relationship_type == "compile_target"
        assert r.caller is not None


# ── add_library source extraction ─────────────────────────────────────────────

def test_add_library_source_count():
    nodes, rels = analyze_cmake_file("/tmp/CMakeLists.txt", SAMPLE_CMAKE, "/tmp")
    compile_rels = [r for r in rels if r.relationship_type == "compile_target"]
    mylib_sources = [r.callee for r in compile_rels if "mylib" in r.caller]
    assert len(mylib_sources) == 2


def test_add_library_source_paths():
    nodes, rels = analyze_cmake_file("/tmp/CMakeLists.txt", SAMPLE_CMAKE, "/tmp")
    compile_rels = [r for r in rels if r.relationship_type == "compile_target"]
    mylib_sources = [r.callee for r in compile_rels if "mylib" in r.caller]
    assert "src/lib.cpp" in mylib_sources
    assert "src/helper.cpp" in mylib_sources


# ── target_link_libraries ──────────────────────────────────────────────────────

def test_target_link_libraries_extracted():
    nodes, rels = analyze_cmake_file("/tmp/CMakeLists.txt", SAMPLE_CMAKE, "/tmp")
    link_rels = [r for r in rels if r.relationship_type == "link_dependency"]
    assert len(link_rels) >= 1


def test_target_link_libraries_callee():
    nodes, rels = analyze_cmake_file("/tmp/CMakeLists.txt", SAMPLE_CMAKE, "/tmp")
    link_rels = [r for r in rels if r.relationship_type == "link_dependency"]
    callee_names = {r.callee for r in link_rels}
    assert "mylib" in callee_names


def test_system_lib_also_extracted():
    nodes, rels = analyze_cmake_file("/tmp/CMakeLists.txt", SAMPLE_CMAKE, "/tmp")
    link_rels = [r for r in rels if r.relationship_type == "link_dependency"]
    callee_names = {r.callee for r in link_rels}
    assert "pthread" in callee_names


# ── multi-target ───────────────────────────────────────────────────────────────

def test_multiple_executables():
    nodes, rels = analyze_cmake_file("/tmp/CMakeLists.txt", MULTI_TARGET_CMAKE, "/tmp")
    compile_rels = [r for r in rels if r.relationship_type == "compile_target"]
    callers = {r.caller for r in compile_rels}
    server_has_source = any("server" in c for c in callers)
    client_has_source = any("client" in c for c in callers)
    assert server_has_source
    assert client_has_source


def test_multiple_link_targets():
    nodes, rels = analyze_cmake_file("/tmp/CMakeLists.txt", MULTI_TARGET_CMAKE, "/tmp")
    link_rels = [r for r in rels if r.relationship_type == "link_dependency"]
    # Both server and client link to core
    callers = {r.caller for r in link_rels}
    assert len(callers) >= 2


# ── relationship types are always set ─────────────────────────────────────────

def test_all_rels_have_relationship_type():
    nodes, rels = analyze_cmake_file("/tmp/CMakeLists.txt", SAMPLE_CMAKE, "/tmp")
    for r in rels:
        assert r.relationship_type is not None
        assert r.relationship_type in ("compile_target", "link_dependency")


def test_no_crash_on_empty_file():
    nodes, rels = analyze_cmake_file("/tmp/CMakeLists.txt", "", "/tmp")
    assert nodes == []
    assert rels == []
