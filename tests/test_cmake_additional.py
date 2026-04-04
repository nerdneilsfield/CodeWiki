import pytest

pytest.importorskip(
    "tree_sitter_cmake", reason="tree-sitter-cmake not installed; pip install 'codewiki[cmake]'"
)

from codewiki.src.be.dependency_analyzer.analyzers.cmake import analyze_cmake_file


def test_cmake_extracts_user_defined_function_and_macro_calls(tmp_path):
    content = """
function(make_app target)
  add_executable(${target} main.cpp)
endfunction()

macro(use_lib target)
  target_link_libraries(${target} pthread)
endmacro()

make_app(app)
use_lib(app)
""".strip()

    nodes, rels = analyze_cmake_file(str(tmp_path / "CMakeLists.txt"), content, str(tmp_path))

    names = {node.name for node in nodes}
    assert {"make_app", "use_lib"} <= names

    resolved = {(rel.caller, rel.callee) for rel in rels if rel.is_resolved}
    assert ("CMakeLists.__script__", "CMakeLists.make_app") in resolved
    assert ("CMakeLists.__script__", "CMakeLists.use_lib") in resolved


def test_cmake_structural_command_from_function_uses_function_as_caller(tmp_path):
    content = """
function(configure target)
  include(my-config)
endfunction()
""".strip()

    _, rels = analyze_cmake_file(str(tmp_path / "CMakeLists.txt"), content, str(tmp_path))

    assert any(
        rel.caller == "CMakeLists.configure" and rel.callee == "include:my-config" for rel in rels
    )
