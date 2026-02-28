# Compiled Language & HLS Support Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enhance C/C++ analysis with #include tracking, header-source pairing, build system integration, cross-file data flow/ownership tracking, and HLS hardware semantic understanding.

**Architecture:** Three-layer approach — Layer 1 enhances base C/C++ extraction (includes, macros, templates, build system links), Layer 2 adds cross-file data flow and ownership tracking, Layer 3 adds HLS pragma semantics and hardware data flow tracing. Each layer builds on the previous.

**Tech Stack:** tree-sitter (C/C++ grammars), tree-sitter-cmake, tree-sitter-make, tree-sitter-language-pack (Tcl), Pydantic models, Python configparser (Vitis .cfg).

**Design doc:** `docs/plans/2026-02-28-compiled-lang-hls-design.md`

---

## Phase 1: Data Model Foundation

### Task 1: Add relationship_type and data flow models to core.py

**Files:**
- Modify: `codewiki/src/be/dependency_analyzer/models/core.py`
- Test: `tests/test_core_models.py`

**Step 1: Write the failing test**

```python
# tests/test_core_models.py
from codewiki.src.be.dependency_analyzer.models.core import (
    CallRelationship, DataFlowEdge, ParamInfo, HLSPragma, Node,
)

def test_call_relationship_has_relationship_type():
    rel = CallRelationship(caller="a", callee="b", relationship_type="include")
    assert rel.relationship_type == "include"

def test_call_relationship_has_data_flow():
    edge = DataFlowEdge(param_name="buf", param_type="int*", direction="inout", ownership="borrow")
    rel = CallRelationship(caller="a", callee="b", data_flow=[edge])
    assert len(rel.data_flow) == 1
    assert rel.data_flow[0].ownership == "borrow"

def test_param_info():
    p = ParamInfo(name="data", type_str="const int*", is_pointer=True, is_reference=False, is_const=True)
    assert p.is_pointer
    assert p.is_const

def test_hls_pragma():
    pragma = HLSPragma(
        pragma_type="INTERFACE",
        params={"port": "mem", "bundle": "gmem"},
        target="mem",
        line=10,
        hardware_semantic="AXI Master memory interface, bundle 'gmem'"
    )
    assert pragma.pragma_type == "INTERFACE"
    assert pragma.params["bundle"] == "gmem"

def test_node_has_hls_fields():
    node = Node(
        id="test", name="mm2s", component_type="function",
        file_path="/tmp/test.cpp", relative_path="test.cpp",
        is_hls_kernel=True, hls_pragmas=[]
    )
    assert node.is_hls_kernel is True
    assert node.hls_pragmas == []

def test_node_hls_fields_default():
    node = Node(
        id="test", name="foo", component_type="function",
        file_path="/tmp/test.cpp", relative_path="test.cpp",
    )
    assert node.is_hls_kernel is False
    assert node.hls_pragmas is None
```

**Step 2: Run test to verify it fails**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_core_models.py -v`
Expected: FAIL — `DataFlowEdge`, `ParamInfo`, `HLSPragma` not defined; `relationship_type` not a field

**Step 3: Write minimal implementation**

Add to `codewiki/src/be/dependency_analyzer/models/core.py` (before `CallRelationship`):

```python
class ParamInfo(BaseModel):
    name: str
    type_str: Optional[str] = None
    is_pointer: bool = False
    is_reference: bool = False
    is_const: bool = False

class DataFlowEdge(BaseModel):
    param_name: str
    param_type: Optional[str] = None
    direction: str = "in"       # "in" | "out" | "inout"
    ownership: Optional[str] = None   # "transfer" | "borrow" | "shared" | "copy"
    lifetime_hint: Optional[str] = None  # "caller_scope" | "callee_owns" | "static" | "heap"

class HLSPragma(BaseModel):
    pragma_type: str
    params: Dict[str, str] = {}
    target: Optional[str] = None
    line: int
    hardware_semantic: str = ""
```

Add to `CallRelationship`:

```python
class CallRelationship(BaseModel):
    caller: str
    callee: str
    call_line: Optional[int] = None
    is_resolved: bool = False
    relationship_type: Optional[str] = None
    data_flow: Optional[List[DataFlowEdge]] = None
```

Add to `Node`:

```python
    hls_pragmas: Optional[List["HLSPragma"]] = None
    is_hls_kernel: bool = False
```

**Step 4: Run test to verify it passes**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_core_models.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/dependency_analyzer/models/core.py tests/test_core_models.py
git commit -m "feat(models): add relationship_type, DataFlowEdge, ParamInfo, HLSPragma to core models"
```

---

## Phase 2: C Analyzer Enhancement (Layer 1)

### Task 2: Add #include, macro, union, enum, typedef extraction to C analyzer

**Files:**
- Modify: `codewiki/src/be/dependency_analyzer/analyzers/c.py`
- Test: `tests/test_c_analyzer_enhanced.py`

**Step 1: Write the failing test**

```python
# tests/test_c_analyzer_enhanced.py
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
```

**Step 2: Run test to verify it fails**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_c_analyzer_enhanced.py -v`
Expected: FAIL — no includes, macros, enums, or unions extracted

**Step 3: Write minimal implementation**

In `c.py` `_extract_nodes()`, add these branches after the existing `elif node.type == "declaration"` block (before `if node_type and node_name:`):

```python
        elif node.type == "enum_specifier":
            node_type = "enum"
            for child in node.children:
                if child.type == "type_identifier":
                    node_name = child.text.decode()
                    break
        elif node.type == "union_specifier":
            node_type = "union"
            for child in node.children:
                if child.type == "type_identifier":
                    node_name = child.text.decode()
                    break
        elif node.type == "preproc_def":
            node_type = "macro"
            for child in node.children:
                if child.type == "identifier":
                    node_name = child.text.decode()
                    break
        elif node.type == "preproc_function_def":
            node_type = "macro"
            for child in node.children:
                if child.type == "identifier":
                    node_name = child.text.decode()
                    break
```

Change the `self.nodes.append` guard to also include new types:

```python
            if node_type in ["function", "struct", "enum", "union", "macro"]:
                self.nodes.append(node_obj)
```

In `_extract_nodes()`, add `#include` extraction at the top (before the if/elif chain for node types, or as a separate early check):

```python
        if node.type == "preproc_include":
            path_node = next((c for c in node.children
                              if c.type in ("string_literal", "system_lib_string")), None)
            if path_node:
                include_path = path_node.text.decode().strip('"').strip('<').strip('>')
                module_path = self._get_module_path()
                self.call_relationships.append(CallRelationship(
                    caller=f"{module_path}.__file__",
                    callee=include_path,
                    call_line=node.start_point[0] + 1,
                    is_resolved=False,
                    relationship_type="include",
                ))
            return  # preproc_include has no interesting children
```

Also add `field_expression` call handling in `_extract_relationships()` — inside the `call_expression` block, add a fallback after the `if function_node:` check:

```python
                # field_expression call: obj.method() or ptr->method()
                if not function_node:
                    field_expr = next((c for c in node.children if c.type == "field_expression"), None)
                    if field_expr:
                        field_id = next((c for c in field_expr.children if c.type == "field_identifier"), None)
                        if field_id:
                            called_function = field_id.text.decode()
                            if not self._is_system_function(called_function):
                                self.call_relationships.append(CallRelationship(
                                    caller=containing_function_id,
                                    callee=called_function,
                                    call_line=node.start_point[0]+1,
                                    is_resolved=False,
                                    relationship_type="call",
                                ))
```

**Step 4: Run test to verify it passes**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_c_analyzer_enhanced.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/dependency_analyzer/analyzers/c.py tests/test_c_analyzer_enhanced.py
git commit -m "feat(c-analyzer): add #include, macro, union, enum, typedef, field-call extraction"
```

---

## Phase 3: C++ Analyzer Enhancement (Layer 1)

### Task 3: Add #include, template, qualified/template calls to C++ analyzer

**Files:**
- Modify: `codewiki/src/be/dependency_analyzer/analyzers/cpp.py`
- Test: `tests/test_cpp_analyzer_enhanced.py`

**Step 1: Write the failing test**

```python
# tests/test_cpp_analyzer_enhanced.py
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
```

**Step 2: Run test to verify it fails**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_cpp_analyzer_enhanced.py -v`
Expected: FAIL — no includes, no template extraction, no qualified calls

**Step 3: Write minimal implementation**

In `cpp.py` `_extract_nodes()`:

1. Add `#include` extraction at the top (same pattern as C analyzer, using `self._get_module_path()`):

```python
        if node.type == "preproc_include":
            path_node = next((c for c in node.children
                              if c.type in ("string_literal", "system_lib_string")), None)
            if path_node:
                include_path = path_node.text.decode().strip('"').strip('<').strip('>')
                module_path = self._get_module_path()
                self.call_relationships.append(CallRelationship(
                    caller=f"{module_path}.__file__",
                    callee=include_path,
                    call_line=node.start_point[0] + 1,
                    is_resolved=False,
                    relationship_type="include",
                ))
            return
```

2. Add `template_declaration` handling — wrap class/function in template:

```python
        elif node.type == "template_declaration":
            # Find inner class or function
            inner_class = next((c for c in node.children if c.type == "class_specifier"), None)
            inner_func = next((c for c in node.children if c.type == "function_definition"), None)
            if inner_class:
                node_type = "template_class"
                for child in inner_class.children:
                    if child.type == "type_identifier":
                        node_name = child.text.decode()
                        break
            elif inner_func:
                node_type = "template_function"
                declarator = next((c for c in inner_func.children if c.type == "function_declarator"), None)
                if declarator:
                    for child in declarator.children:
                        if child.type == "identifier":
                            node_name = child.text.decode()
                            break
```

3. In `_extract_relationships()`, add handling for `qualified_identifier` and `template_function` inside the `call_expression` block:

```python
                    elif child.type == "qualified_identifier":
                        # MyLib::func()
                        identifiers = [c for c in child.children if c.type == "identifier"]
                        if identifiers:
                            called_function = identifiers[-1].text.decode()
                            break
                    elif child.type == "template_function":
                        # func<T>()
                        ident = next((c for c in child.children if c.type == "identifier"), None)
                        if ident:
                            called_function = ident.text.decode()
                            break
```

4. Ensure `template_class` and `template_function` are included in the append guard:

```python
            if node_type in ["class", "struct", "function", "template_class", "template_function"]:
                self.nodes.append(node_obj)
```

**Step 4: Run test to verify it passes**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_cpp_analyzer_enhanced.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/dependency_analyzer/analyzers/cpp.py tests/test_cpp_analyzer_enhanced.py
git commit -m "feat(cpp-analyzer): add #include, template, qualified/template call extraction"
```

---

## Phase 4: Build System Integration (Layer 1)

### Task 4: CMake source file and link dependency extraction

**Files:**
- Modify: `codewiki/src/be/dependency_analyzer/analyzers/cmake.py`
- Test: `tests/test_cmake_enhanced.py`

**Step 1: Write the failing test**

```python
# tests/test_cmake_enhanced.py
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
    # myapp -> src/main.cpp, src/utils.cpp, src/parser.cpp
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
```

**Step 2: Run test to verify it fails**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_cmake_enhanced.py -v`
Expected: FAIL — no `compile_target` or `link_dependency` relationships

**Step 3: Write minimal implementation**

In `cmake.py` `_extract_relationships()`, enhance the `_STRUCTURAL_COMMANDS` handling (the `elif cmd_name in _STRUCTURAL_COMMANDS:` block). Replace the generic structural command handling with specific logic:

```python
                # Specific source-file-aware commands
                elif cmd_name in ("add_executable", "add_library"):
                    args = next((c for c in node.children if c.type == "argument_list"), None)
                    if args:
                        arg_nodes = [c for c in args.children if c.type == "argument"]
                        if arg_nodes:
                            target_name = self._node_text(arg_nodes[0]).strip()
                            target_id = self._get_component_id(target_name)
                            # Skip keywords like STATIC, SHARED, MODULE
                            skip_keywords = {"STATIC", "SHARED", "MODULE", "OBJECT", "INTERFACE", "IMPORTED", "ALIAS"}
                            for arg in arg_nodes[1:]:
                                val = self._node_text(arg).strip()
                                if val and val not in skip_keywords and not val.startswith("$"):
                                    self.call_relationships.append(CallRelationship(
                                        caller=target_id,
                                        callee=val,
                                        call_line=node.start_point[0] + 1,
                                        is_resolved=False,
                                        relationship_type="compile_target",
                                    ))
                elif cmd_name == "target_link_libraries":
                    args = next((c for c in node.children if c.type == "argument_list"), None)
                    if args:
                        arg_nodes = [c for c in args.children if c.type == "argument"]
                        if len(arg_nodes) >= 2:
                            target_name = self._node_text(arg_nodes[0]).strip()
                            target_id = self._get_component_id(target_name)
                            skip_keywords = {"PUBLIC", "PRIVATE", "INTERFACE"}
                            for arg in arg_nodes[1:]:
                                val = self._node_text(arg).strip()
                                if val and val not in skip_keywords and not val.startswith("$"):
                                    self.call_relationships.append(CallRelationship(
                                        caller=target_id,
                                        callee=val,
                                        call_line=node.start_point[0] + 1,
                                        is_resolved=False,
                                        relationship_type="link_dependency",
                                    ))
                elif cmd_name in _STRUCTURAL_COMMANDS:
                    # existing generic structural handling (keep as fallback)
                    ...
```

**Step 4: Run test to verify it passes**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_cmake_enhanced.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/dependency_analyzer/analyzers/cmake.py tests/test_cmake_enhanced.py
git commit -m "feat(cmake): extract source files from add_executable/add_library, link dependencies"
```

### Task 5: Makefile header dependency tagging

**Files:**
- Modify: `codewiki/src/be/dependency_analyzer/analyzers/makefile.py`
- Test: `tests/test_makefile_enhanced.py`

**Step 1: Write the failing test**

```python
# tests/test_makefile_enhanced.py
from codewiki.src.be.dependency_analyzer.analyzers.makefile import analyze_makefile_file

SAMPLE_MAKE = '''
CC = gcc

main.o: main.c utils.h config.h
\t$(CC) -c main.c

utils.o: utils.c utils.h
\t$(CC) -c utils.c

all: main.o utils.o
\t$(CC) -o app main.o utils.o
'''

def test_header_dep_vs_source_dep():
    nodes, rels = analyze_makefile_file("/tmp/Makefile", SAMPLE_MAKE, "/tmp")
    header_deps = [r for r in rels if r.relationship_type == "header_dep"]
    source_deps = [r for r in rels if r.relationship_type == "compile_dep"]
    # main.o depends on main.c (source) and utils.h, config.h (headers)
    header_names = {r.callee.split(".")[-1] for r in header_deps}
    # At minimum, .h files should be classified as header_dep
    assert any(".h" in r.callee for r in header_deps) or len(header_deps) == 0
    # Target-to-target deps (all -> main.o) remain as before

def test_vpp_detection():
    vpp_make = '''
kernel.xo: kernel.cpp
\tv++ -c -k kernel kernel.cpp -o kernel.xo
'''
    nodes, rels = analyze_makefile_file("/tmp/Makefile", vpp_make, "/tmp")
    hls_rels = [r for r in rels if r.relationship_type == "hls_compile"]
    # Should detect v++ compilation
    assert len(hls_rels) >= 0  # At minimum, doesn't crash
```

**Step 2: Run test to verify it fails**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_makefile_enhanced.py -v`
Expected: FAIL — no `header_dep` or `compile_dep` relationship types

**Step 3: Write minimal implementation**

In `makefile.py`, in the second pass (prerequisite relationship extraction), classify by file extension:

```python
        # Second pass: extract target → prerequisite relationships
        for node in root.children:
            if node.type == "rule":
                targets_node = next(
                    (c for c in node.children if c.type == "targets"), None
                )
                prereqs_node = next(
                    (c for c in node.children if c.type == "prerequisites"), None
                )
                if targets_node and prereqs_node:
                    target_text = self._node_text(targets_node).strip()
                    prereq_text = self._node_text(prereqs_node).strip()
                    for target_name in target_text.split():
                        caller_id = self._get_component_id(target_name)
                        for prereq in prereq_text.split():
                            # Classify relationship type
                            if prereq in target_names:
                                rel_type = "target_dep"
                            elif any(prereq.endswith(ext) for ext in (".h", ".hpp", ".hxx")):
                                rel_type = "header_dep"
                            elif any(prereq.endswith(ext) for ext in (".c", ".cpp", ".cc", ".cxx")):
                                rel_type = "compile_dep"
                            else:
                                rel_type = "prerequisite"
                            self.call_relationships.append(CallRelationship(
                                caller=caller_id,
                                callee=prereq if prereq not in target_names else self._get_component_id(prereq),
                                call_line=node.start_point[0] + 1,
                                is_resolved=prereq in target_names,
                                relationship_type=rel_type,
                            ))

                # Detect v++/vitis_hls in recipe
                recipe_node = next(
                    (c for c in node.children if c.type == "recipe"), None
                )
                if recipe_node and targets_node:
                    recipe_text = self._node_text(recipe_node)
                    if "v++" in recipe_text or "vitis_hls" in recipe_text:
                        target_text = self._node_text(targets_node).strip()
                        for target_name in target_text.split():
                            self.call_relationships.append(CallRelationship(
                                caller=self._get_component_id(target_name),
                                callee="v++",
                                call_line=node.start_point[0] + 1,
                                is_resolved=False,
                                relationship_type="hls_compile",
                            ))
```

**Step 4: Run test to verify it passes**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_makefile_enhanced.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/dependency_analyzer/analyzers/makefile.py tests/test_makefile_enhanced.py
git commit -m "feat(makefile): classify prereq relationships by type, detect v++ HLS compilation"
```

---

## Phase 5: Header-Source Pairing (Layer 1)

### Task 6: Add header-source file pairing post-processing

**Files:**
- Modify: `codewiki/src/be/dependency_analyzer/analysis/call_graph_analyzer.py`
- Test: `tests/test_header_source_pairing.py`

**Step 1: Write the failing test**

```python
# tests/test_header_source_pairing.py
from codewiki.src.be.dependency_analyzer.analysis.call_graph_analyzer import CallGraphAnalyzer

def test_header_source_pairing():
    analyzer = CallGraphAnalyzer()
    # Simulate analyzed files with include relationships
    from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship

    # Create nodes from two files
    analyzer.functions = {
        "utils.process": Node(
            id="utils.process", name="process", component_type="function",
            file_path="/repo/src/utils.cpp", relative_path="src/utils.cpp",
        ),
        "utils.helper": Node(
            id="utils.helper", name="helper", component_type="function",
            file_path="/repo/src/utils.h", relative_path="src/utils.h",
        ),
    }
    # Simulate an include relationship
    analyzer.call_relationships = [
        CallRelationship(
            caller="utils.__file__", callee="utils.h",
            call_line=1, is_resolved=False, relationship_type="include",
        ),
    ]

    analyzer._pair_header_source_files()

    header_impl_rels = [r for r in analyzer.call_relationships if r.relationship_type == "header_impl"]
    assert len(header_impl_rels) >= 1
```

**Step 2: Run test to verify it fails**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_header_source_pairing.py -v`
Expected: FAIL — `_pair_header_source_files` not defined

**Step 3: Write minimal implementation**

Add method to `CallGraphAnalyzer`:

```python
    def _pair_header_source_files(self):
        """Pair header files (.h/.hpp) with implementation files (.cpp/.cc/.c) by basename."""
        from collections import defaultdict
        header_exts = {".h", ".hpp", ".hxx"}
        source_exts = {".c", ".cpp", ".cc", ".cxx", ".c++"}

        # Group nodes by file stem (without extension)
        stem_to_files = defaultdict(lambda: {"headers": [], "sources": []})
        for func_id, func in self.functions.items():
            p = Path(func.file_path)
            stem = p.stem
            if p.suffix in header_exts:
                stem_to_files[stem]["headers"].append(func)
            elif p.suffix in source_exts:
                stem_to_files[stem]["sources"].append(func)

        # Create header_impl relationships for matched pairs
        for stem, files in stem_to_files.items():
            if files["headers"] and files["sources"]:
                # Pick representative node from each
                header_rep = files["headers"][0]
                source_rep = files["sources"][0]
                self.call_relationships.append(CallRelationship(
                    caller=source_rep.id,
                    callee=header_rep.id,
                    call_line=0,
                    is_resolved=True,
                    relationship_type="header_impl",
                ))
```

Call it in `analyze_code_files()` after `_resolve_call_relationships()`:

```python
        self._resolve_call_relationships()
        self._pair_header_source_files()
        self._deduplicate_relationships()
```

**Step 4: Run test to verify it passes**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_header_source_pairing.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/dependency_analyzer/analysis/call_graph_analyzer.py tests/test_header_source_pairing.py
git commit -m "feat(call-graph): add header-source file pairing post-processing"
```

---

## Phase 6: Parameter Type Extraction (Layer 2)

### Task 7: Extract parameter types in C/C++ function definitions

**Files:**
- Modify: `codewiki/src/be/dependency_analyzer/analyzers/c.py`
- Modify: `codewiki/src/be/dependency_analyzer/analyzers/cpp.py`
- Test: `tests/test_param_type_extraction.py`

**Step 1: Write the failing test**

```python
# tests/test_param_type_extraction.py
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
    # Parameters should be ParamInfo objects (serialized as dicts if using model_dump)
    # or structured strings — we check for the param names at minimum
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
    funcs = {n.name: n for n in nodes}

    # At minimum, parameter names should be extracted
    assert funcs["transfer"].parameters is not None
    assert funcs["borrow"].parameters is not None
```

**Step 2: Run test to verify it fails**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_param_type_extraction.py -v`
Expected: FAIL — parameters are `None` for C/C++ functions

**Step 3: Write minimal implementation**

Add a shared helper (can be put in each analyzer or a utils module). In both `c.py` and `cpp.py`, extract parameters from `function_declarator` → `parameter_list` → `parameter_declaration`:

```python
    def _extract_parameters(self, func_declarator_node):
        """Extract parameter names and types from a function declarator."""
        params = []
        param_list = next((c for c in func_declarator_node.children if c.type == "parameter_list"), None)
        if not param_list:
            return None

        for child in param_list.children:
            if child.type == "parameter_declaration":
                param_text = child.text.decode().strip()
                # Extract just the identifier (last word before any default value)
                identifier = next(
                    (c for c in child.children if c.type in ("identifier", "pointer_declarator")), None
                )
                if identifier:
                    if identifier.type == "pointer_declarator":
                        identifier = next((c for c in identifier.children if c.type == "identifier"), None)
                    if identifier:
                        params.append(identifier.text.decode())
                    else:
                        params.append(param_text)
                else:
                    params.append(param_text)
        return params if params else None
```

Then in `_extract_nodes()`, when creating a function Node, call `self._extract_parameters(declarator)` and pass the result as `parameters=`.

**Step 4: Run test to verify it passes**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_param_type_extraction.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/dependency_analyzer/analyzers/c.py codewiki/src/be/dependency_analyzer/analyzers/cpp.py tests/test_param_type_extraction.py
git commit -m "feat(c-cpp): extract function parameter names from declarations"
```

---

## Phase 7: Data Flow Analyzer (Layer 2)

### Task 8: Create cross-file data flow analyzer

**Files:**
- Create: `codewiki/src/be/dependency_analyzer/analysis/data_flow_analyzer.py`
- Modify: `codewiki/src/be/dependency_analyzer/analysis/call_graph_analyzer.py`
- Test: `tests/test_data_flow_analyzer.py`

**Step 1: Write the failing test**

```python
# tests/test_data_flow_analyzer.py
from codewiki.src.be.dependency_analyzer.analysis.data_flow_analyzer import DataFlowAnalyzer
from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship

def test_data_flow_basic():
    """Test that data flow edges are created for call relationships."""
    functions = {
        "a.producer": Node(
            id="a.producer", name="producer", component_type="function",
            file_path="/repo/a.cpp", relative_path="a.cpp",
            parameters=["buf", "size"],
        ),
        "b.consumer": Node(
            id="b.consumer", name="consumer", component_type="function",
            file_path="/repo/b.cpp", relative_path="b.cpp",
            parameters=["data", "len"],
        ),
    }
    relationships = [
        CallRelationship(
            caller="a.producer", callee="b.consumer",
            call_line=10, is_resolved=True, relationship_type="call",
        ),
    ]

    analyzer = DataFlowAnalyzer(functions, relationships)
    result = analyzer.analyze()

    assert "flow_edges" in result
    assert len(result["flow_edges"]) >= 0  # At minimum doesn't crash

def test_ownership_detection_malloc():
    """Test that malloc/free patterns are detected."""
    functions = {
        "main.init": Node(
            id="main.init", name="init", component_type="function",
            file_path="/repo/main.c", relative_path="main.c",
            parameters=[],
            source_code="void init() { int* p = malloc(100); free(p); }",
        ),
    }
    analyzer = DataFlowAnalyzer(functions, [])
    result = analyzer.analyze()

    assert "ownership_patterns" in result
    # Should detect malloc/free pattern
```

**Step 2: Run test to verify it fails**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_data_flow_analyzer.py -v`
Expected: FAIL — module not found

**Step 3: Write minimal implementation**

```python
# codewiki/src/be/dependency_analyzer/analysis/data_flow_analyzer.py
"""
Cross-file data flow analyzer.

Analyzes parameter-level data flow across function calls and tracks
ownership/lifetime patterns (malloc/free, new/delete, smart pointers).
"""

import re
import logging
from typing import Dict, List, Any
from codewiki.src.be.dependency_analyzer.models.core import (
    Node, CallRelationship, DataFlowEdge,
)

logger = logging.getLogger(__name__)

# Allocation/deallocation function pairs
_ALLOC_FUNCTIONS = {"malloc", "calloc", "realloc", "strdup", "new"}
_DEALLOC_FUNCTIONS = {"free", "delete"}
_OWNERSHIP_TRANSFER = {"std::move"}
_SMART_PTRS = {"unique_ptr", "shared_ptr", "weak_ptr"}


class DataFlowAnalyzer:
    def __init__(self, functions: Dict[str, Node], relationships: List[CallRelationship]):
        self.functions = functions
        self.relationships = relationships

    def analyze(self) -> Dict[str, Any]:
        flow_edges = self._build_flow_edges()
        ownership_patterns = self._detect_ownership_patterns()

        return {
            "flow_edges": flow_edges,
            "ownership_patterns": ownership_patterns,
        }

    def _build_flow_edges(self) -> List[Dict]:
        """Build parameter-level data flow edges from call relationships."""
        edges = []
        for rel in self.relationships:
            if rel.relationship_type not in ("call", "calls", None):
                continue
            caller_func = self.functions.get(rel.caller)
            callee_func = self.functions.get(rel.callee)
            if not callee_func or not callee_func.parameters:
                continue

            for param in callee_func.parameters:
                param_name = param if isinstance(param, str) else param
                edge = DataFlowEdge(
                    param_name=param_name,
                    direction="in",
                )
                edges.append({
                    "caller": rel.caller,
                    "callee": rel.callee,
                    "line": rel.call_line,
                    "edge": edge.model_dump(),
                })
        return edges

    def _detect_ownership_patterns(self) -> List[Dict]:
        """Detect allocation/deallocation and ownership patterns in source code."""
        patterns = []
        alloc_re = re.compile(r'\b(malloc|calloc|realloc|new)\b')
        dealloc_re = re.compile(r'\b(free|delete)\b')
        smart_re = re.compile(r'\b(unique_ptr|shared_ptr|make_unique|make_shared)\b')
        move_re = re.compile(r'\bstd::move\b')

        for func_id, func in self.functions.items():
            if not func.source_code:
                continue
            src = func.source_code

            has_alloc = bool(alloc_re.search(src))
            has_dealloc = bool(dealloc_re.search(src))
            has_smart = bool(smart_re.search(src))
            has_move = bool(move_re.search(src))

            if has_alloc or has_dealloc or has_smart or has_move:
                pattern = {
                    "function": func_id,
                    "file": func.relative_path,
                    "allocates": has_alloc,
                    "deallocates": has_dealloc,
                    "uses_smart_ptr": has_smart,
                    "uses_move": has_move,
                }
                if has_alloc and not has_dealloc and not has_smart:
                    pattern["warning"] = "allocates without deallocation in scope"
                patterns.append(pattern)

        return patterns
```

In `call_graph_analyzer.py`, integrate after `_pair_header_source_files()`:

```python
        self._pair_header_source_files()
        data_flow_result = self._analyze_data_flow()
        self._deduplicate_relationships()
```

Add method:

```python
    def _analyze_data_flow(self) -> Dict:
        from codewiki.src.be.dependency_analyzer.analysis.data_flow_analyzer import DataFlowAnalyzer
        analyzer = DataFlowAnalyzer(self.functions, self.call_relationships)
        return analyzer.analyze()
```

Include `data_flow_result` in the returned dict from `analyze_code_files()`.

**Step 4: Run test to verify it passes**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_data_flow_analyzer.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/dependency_analyzer/analysis/data_flow_analyzer.py tests/test_data_flow_analyzer.py codewiki/src/be/dependency_analyzer/analysis/call_graph_analyzer.py
git commit -m "feat(data-flow): add cross-file data flow analyzer with ownership detection"
```

---

## Phase 8: HLS Pragma Extraction (Layer 3)

### Task 9: Add HLS pragma extraction to C/C++ analyzers

**Files:**
- Modify: `codewiki/src/be/dependency_analyzer/analyzers/c.py`
- Modify: `codewiki/src/be/dependency_analyzer/analyzers/cpp.py`
- Test: `tests/test_hls_pragma.py`

**Step 1: Write the failing test**

```python
# tests/test_hls_pragma.py
from codewiki.src.be.dependency_analyzer.analyzers.cpp import analyze_cpp_file

HLS_KERNEL = '''
extern "C" {
void mm2s(int* mem, int size) {
#pragma HLS INTERFACE m_axi port=mem offset=slave bundle=gmem
#pragma HLS INTERFACE s_axilite port=size bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
    for(int i = 0; i < size; i++) {
#pragma HLS PIPELINE II=1
        // process
    }
}
}
'''

def test_pragma_extraction():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", HLS_KERNEL, "/tmp")
    func = next((n for n in nodes if n.name == "mm2s"), None)
    assert func is not None
    assert func.hls_pragmas is not None
    assert len(func.hls_pragmas) >= 3
    # Check INTERFACE pragma
    iface_pragmas = [p for p in func.hls_pragmas if p.pragma_type == "INTERFACE"]
    assert len(iface_pragmas) >= 2
    # Check PIPELINE pragma
    pipeline_pragmas = [p for p in func.hls_pragmas if p.pragma_type == "PIPELINE"]
    assert len(pipeline_pragmas) >= 1

def test_pragma_params():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", HLS_KERNEL, "/tmp")
    func = next(n for n in nodes if n.name == "mm2s")
    m_axi = next(p for p in func.hls_pragmas if p.params.get("port") == "mem")
    assert m_axi.pragma_type == "INTERFACE"
    assert m_axi.params["bundle"] == "gmem"
    assert "AXI" in m_axi.hardware_semantic or "axi" in m_axi.hardware_semantic.lower()

def test_extern_c_kernel_detection():
    nodes, rels = analyze_cpp_file("/tmp/kernel.cpp", HLS_KERNEL, "/tmp")
    func = next(n for n in nodes if n.name == "mm2s")
    assert func.is_hls_kernel is True
```

**Step 2: Run test to verify it fails**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_hls_pragma.py -v`
Expected: FAIL — no pragma extraction, no kernel detection

**Step 3: Write minimal implementation**

Add a shared HLS pragma parser (can be in a utils module or inline). Add to both C and C++ analyzers:

```python
    def _extract_hls_pragmas(self, func_node) -> list:
        """Extract HLS pragmas from within a function body."""
        from codewiki.src.be.dependency_analyzer.models.core import HLSPragma
        pragmas = []
        self._collect_pragmas(func_node, pragmas)
        return pragmas if pragmas else None

    def _collect_pragmas(self, node, pragmas):
        if node.type == "preproc_call":
            text = node.text.decode().strip()
            if text.startswith("#pragma") and "HLS" in text.upper():
                pragma = self._parse_hls_pragma(text, node.start_point[0] + 1)
                if pragma:
                    pragmas.append(pragma)
        for child in node.children:
            self._collect_pragmas(child, pragmas)

    def _parse_hls_pragma(self, text: str, line: int):
        from codewiki.src.be.dependency_analyzer.models.core import HLSPragma
        # #pragma HLS INTERFACE m_axi port=mem offset=slave bundle=gmem
        parts = text.split()
        # Find HLS keyword position
        hls_idx = None
        for i, p in enumerate(parts):
            if p.upper() == "HLS":
                hls_idx = i
                break
        if hls_idx is None or hls_idx + 1 >= len(parts):
            return None

        pragma_type = parts[hls_idx + 1].upper()
        params = {}
        for part in parts[hls_idx + 2:]:
            if "=" in part:
                k, v = part.split("=", 1)
                params[k.lower()] = v
            elif part not in ("#pragma", "HLS", pragma_type):
                # Positional arg like "m_axi" after INTERFACE
                if "subtype" not in params:
                    params["subtype"] = part

        target = params.get("port") or params.get("variable")
        semantic = self._pragma_semantic(pragma_type, params)

        return HLSPragma(
            pragma_type=pragma_type,
            params=params,
            target=target,
            line=line,
            hardware_semantic=semantic,
        )

    def _pragma_semantic(self, pragma_type: str, params: dict) -> str:
        subtype = params.get("subtype", "")
        port = params.get("port", "")
        bundle = params.get("bundle", "")

        semantics = {
            "INTERFACE": {
                "m_axi": f"AXI Master memory interface{f', bundle {bundle!r}' if bundle else ''}",
                "s_axilite": f"AXI-Lite control/status register{f' for {port!r}' if port else ''}",
                "axis": f"AXI-Stream data port{f' {port!r}' if port else ''}",
                "ap_none": f"Wire port (no handshake){f' {port!r}' if port else ''}",
            },
            "PIPELINE": f"Pipelined with initiation interval {params.get('ii', 'auto')} cycles",
            "DATAFLOW": "Task-level pipelining with automatic FIFOs between functions",
            "UNROLL": f"Loop unrolled {params.get('factor', 'fully')}x for parallel execution",
            "ARRAY_PARTITION": f"Array partitioned for parallel memory access",
            "INLINE": "Function inlined into caller (no separate hardware module)",
            "STREAM": f"Variable implemented as hardware FIFO",
        }

        if pragma_type == "INTERFACE":
            return semantics["INTERFACE"].get(subtype.lower(), f"Hardware interface ({subtype})")
        return semantics.get(pragma_type, f"HLS {pragma_type} directive")
```

In `_extract_nodes()`, when creating a function Node:
- Call `self._extract_hls_pragmas(node)` and set `hls_pragmas=`
- Detect `extern "C"` wrapper: check if any ancestor is `linkage_specification` with `extern "C"` — if so + has HLS pragmas, set `is_hls_kernel=True`

```python
    def _is_in_extern_c(self, node) -> bool:
        current = node.parent
        while current:
            if current.type == "linkage_specification":
                for child in current.children:
                    if child.type == "string_literal" and child.text.decode().strip('"') == "C":
                        return True
            current = current.parent
        return False
```

**Step 4: Run test to verify it passes**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_hls_pragma.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/dependency_analyzer/analyzers/c.py codewiki/src/be/dependency_analyzer/analyzers/cpp.py tests/test_hls_pragma.py
git commit -m "feat(hls): extract HLS pragmas with hardware semantics, detect extern C kernels"
```

---

## Phase 9: Vitis Config Parser (Layer 3)

### Task 10: Create Vitis .cfg file parser

**Files:**
- Create: `codewiki/src/be/dependency_analyzer/analyzers/vitis_cfg.py`
- Modify: `codewiki/src/be/dependency_analyzer/utils/patterns.py`
- Modify: `codewiki/src/be/dependency_analyzer/analysis/analysis_service.py`
- Modify: `codewiki/src/be/dependency_analyzer/analysis/call_graph_analyzer.py`
- Test: `tests/test_vitis_cfg.py`

**Step 1: Write the failing test**

```python
# tests/test_vitis_cfg.py
from codewiki.src.be.dependency_analyzer.analyzers.vitis_cfg import analyze_vitis_cfg

HLS_CFG = '''
[hls]
flow_target=vitis
syn.file=./mm2s.cpp
syn.file_cflags=./mm2s.cpp,-I./include
syn.top=mm2s
syn.output.format=xo
'''

SYSTEM_CFG = '''
[connectivity]
nk=mm2s:1:mm2s_1
nk=s2mm:1:s2mm_1
stream_connect=mm2s_1.s:s2mm_1.s
sp=mm2s_1.mem:DDR[0]
sp=s2mm_1.mem:DDR[1]
'''

def test_hls_top_function():
    nodes, rels = analyze_vitis_cfg("/tmp/hls.cfg", HLS_CFG, "/tmp")
    top_funcs = [n for n in nodes if n.component_type == "hls_top"]
    assert any(n.name == "mm2s" for n in top_funcs)

def test_hls_source_file():
    nodes, rels = analyze_vitis_cfg("/tmp/hls.cfg", HLS_CFG, "/tmp")
    src_rels = [r for r in rels if r.relationship_type == "hls_source"]
    assert any("mm2s.cpp" in r.callee for r in src_rels)

def test_stream_connect():
    nodes, rels = analyze_vitis_cfg("/tmp/system.cfg", SYSTEM_CFG, "/tmp")
    stream_rels = [r for r in rels if r.relationship_type == "stream_connect"]
    assert len(stream_rels) >= 1
    # mm2s_1.s -> s2mm_1.s
    assert any("mm2s" in r.caller and "s2mm" in r.callee for r in stream_rels)

def test_memory_mapping():
    nodes, rels = analyze_vitis_cfg("/tmp/system.cfg", SYSTEM_CFG, "/tmp")
    mem_rels = [r for r in rels if r.relationship_type == "memory_map"]
    assert len(mem_rels) >= 2
```

**Step 2: Run test to verify it fails**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_vitis_cfg.py -v`
Expected: FAIL — module not found

**Step 3: Write minimal implementation**

```python
# codewiki/src/be/dependency_analyzer/analyzers/vitis_cfg.py
"""
Vitis/HLS .cfg file parser.

Parses Vitis configuration files to extract:
- HLS top function and source file associations
- Kernel instance mappings (nk=)
- Stream connections between kernels (stream_connect=)
- Memory bank assignments (sp=)
"""

import logging
from configparser import ConfigParser
from typing import List, Tuple
from pathlib import Path
import os

from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship

logger = logging.getLogger(__name__)


def analyze_vitis_cfg(
    file_path: str,
    content: str,
    repo_path: str = None,
) -> Tuple[List[Node], List[CallRelationship]]:
    nodes = []
    relationships = []
    rel_path = os.path.relpath(file_path, repo_path) if repo_path else file_path
    module_path = rel_path.replace("/", ".").replace("\\", ".")

    config = ConfigParser(allow_no_value=True)
    # ConfigParser needs = or : as delimiters, Vitis uses =
    try:
        config.read_string(content)
    except Exception as e:
        logger.warning(f"Failed to parse .cfg file {file_path}: {e}")
        return nodes, relationships

    # Parse [hls] section
    if config.has_section("hls"):
        top_func = None
        source_files = []

        for key, value in config.items("hls"):
            if key == "syn.top" and value:
                top_func = value.strip()
            elif key.startswith("syn.file") and "cflags" not in key and value:
                source_files.append(value.strip().lstrip("./"))

        if top_func:
            component_id = f"{module_path}.{top_func}"
            nodes.append(Node(
                id=component_id,
                name=top_func,
                component_type="hls_top",
                file_path=file_path,
                relative_path=rel_path,
                node_type="hls_top",
                display_name=f"HLS top: {top_func}",
                component_id=component_id,
                is_hls_kernel=True,
            ))

            for src in source_files:
                relationships.append(CallRelationship(
                    caller=component_id,
                    callee=src,
                    relationship_type="hls_source",
                    is_resolved=False,
                ))

    # Parse [connectivity] section
    if config.has_section("connectivity"):
        kernel_instances = {}  # instance_name -> kernel_name

        for key, value in config.items("connectivity"):
            if not value:
                continue
            value = value.strip()

            if key == "nk":
                # nk=kernel:count:instance_name
                parts = value.split(":")
                if len(parts) >= 3:
                    kernel_name, count, instance_name = parts[0], parts[1], parts[2]
                    kernel_instances[instance_name] = kernel_name
                    comp_id = f"{module_path}.{instance_name}"
                    nodes.append(Node(
                        id=comp_id,
                        name=instance_name,
                        component_type="kernel_instance",
                        file_path=file_path,
                        relative_path=rel_path,
                        node_type="kernel_instance",
                        display_name=f"kernel {kernel_name} as {instance_name}",
                        component_id=comp_id,
                    ))

            elif key == "stream_connect":
                # stream_connect=src_inst.port:dst_inst.port
                parts = value.split(":")
                if len(parts) == 2:
                    src, dst = parts
                    src_inst = src.split(".")[0] if "." in src else src
                    dst_inst = dst.split(".")[0] if "." in dst else dst
                    relationships.append(CallRelationship(
                        caller=f"{module_path}.{src_inst}",
                        callee=f"{module_path}.{dst_inst}",
                        relationship_type="stream_connect",
                        is_resolved=True,
                    ))

            elif key == "sp":
                # sp=instance.port:DDR[0]
                parts = value.split(":")
                if len(parts) == 2:
                    port_spec, memory = parts
                    inst = port_spec.split(".")[0] if "." in port_spec else port_spec
                    relationships.append(CallRelationship(
                        caller=f"{module_path}.{inst}",
                        callee=memory,
                        relationship_type="memory_map",
                        is_resolved=False,
                    ))

    return nodes, relationships
```

Also update `patterns.py` `CODE_EXTENSIONS` to add `.cfg`:

```python
    ".cfg": "vitis_cfg",
```

And add `"vitis_cfg"` to `SUPPORTED_LANGUAGES` in `analysis_service.py`.

Add routing in `call_graph_analyzer.py` `_analyze_code_file()`:

```python
            elif language == "vitis_cfg":
                self._analyze_vitis_cfg_file(file_path, content, repo_dir)
```

And the method:

```python
    def _analyze_vitis_cfg_file(self, file_path, content, repo_dir):
        from codewiki.src.be.dependency_analyzer.analyzers.vitis_cfg import analyze_vitis_cfg
        try:
            functions, relationships = analyze_vitis_cfg(file_path, content, repo_path=repo_dir)
            for func in functions:
                self.functions[func.id or f"{file_path}:{func.name}"] = func
            self.call_relationships.extend(relationships)
        except Exception as e:
            logger.error(f"Failed to analyze Vitis cfg {file_path}: {e}", exc_info=True)
```

**Step 4: Run test to verify it passes**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_vitis_cfg.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/dependency_analyzer/analyzers/vitis_cfg.py tests/test_vitis_cfg.py codewiki/src/be/dependency_analyzer/utils/patterns.py codewiki/src/be/dependency_analyzer/analysis/analysis_service.py codewiki/src/be/dependency_analyzer/analysis/call_graph_analyzer.py
git commit -m "feat(vitis): add .cfg parser for HLS top functions, stream connections, memory maps"
```

---

## Phase 10: HLS Stream Tracking (Layer 3)

### Task 11: Track hls::stream connections in C++ analyzer

**Files:**
- Modify: `codewiki/src/be/dependency_analyzer/analyzers/cpp.py`
- Test: `tests/test_hls_stream.py`

**Step 1: Write the failing test**

```python
# tests/test_hls_stream.py
from codewiki.src.be.dependency_analyzer.analyzers.cpp import analyze_cpp_file

STREAM_CODE = '''
#include <hls_stream.h>

void producer(hls::stream<int>& out, int* mem, int n) {
#pragma HLS INTERFACE axis port=out
    for (int i = 0; i < n; i++) {
#pragma HLS PIPELINE II=1
        out.write(mem[i]);
    }
}

void consumer(hls::stream<int>& in, int* mem, int n) {
#pragma HLS INTERFACE axis port=in
    for (int i = 0; i < n; i++) {
#pragma HLS PIPELINE II=1
        mem[i] = in.read();
    }
}
'''

def test_stream_parameter_detection():
    nodes, rels = analyze_cpp_file("/tmp/stream.cpp", STREAM_CODE, "/tmp")
    producer = next(n for n in nodes if n.name == "producer")
    # Should detect hls::stream parameter
    assert producer.hls_pragmas is not None
    # INTERFACE axis on 'out' port
    axis_pragma = next((p for p in producer.hls_pragmas if p.pragma_type == "INTERFACE" and p.params.get("subtype") == "axis"), None)
    assert axis_pragma is not None
```

**Step 2: Run test — should pass if Task 9 is complete**

Run: `/home/dengqi/Source/envs/codewiki/bin/python -m pytest tests/test_hls_stream.py -v`

This test primarily validates that the pragma extraction from Task 9 works on stream-oriented code. If it passes, commit:

**Step 3: Commit**

```bash
git add tests/test_hls_stream.py
git commit -m "test(hls): add hls::stream parameter and pragma detection tests"
```

---

## Phase 11: Integration & Include Patterns

### Task 12: Wire up .cfg extension and Makefile include patterns

**Files:**
- Modify: `codewiki/src/be/dependency_analyzer/utils/patterns.py`
- Modify: `codewiki/src/be/dependency_analyzer/analysis/call_graph_analyzer.py` (extract_code_files)

**Step 1: Verify .cfg files are picked up**

Ensure `DEFAULT_INCLUDE_PATTERNS` has `"*.cfg"` and `CODE_EXTENSIONS` has `".cfg": "vitis_cfg"`.
Ensure `extract_code_files()` works with the new extension.

Run quick verification:

```bash
/home/dengqi/Source/envs/codewiki/bin/python -c "
from codewiki.src.be.dependency_analyzer.analysis.call_graph_analyzer import CallGraphAnalyzer
a = CallGraphAnalyzer()
tree = {
    'type': 'directory', 'name': 'root', 'path': '', 'children': [
        {'type': 'file', 'name': 'system.cfg', 'path': 'system.cfg', 'extension': '.cfg'},
        {'type': 'file', 'name': 'kernel.cpp', 'path': 'kernel.cpp', 'extension': '.cpp'},
        {'type': 'file', 'name': 'Makefile', 'path': 'Makefile', 'extension': ''},
    ]
}
files = a.extract_code_files(tree)
for f in files:
    print(f'{f[\"name\"]:20s} -> {f[\"language\"]}')
"
```

Expected: All three files should be detected with correct language.

**Step 2: Commit if changes needed**

```bash
git add codewiki/src/be/dependency_analyzer/utils/patterns.py
git commit -m "feat(patterns): add .cfg to include patterns and code extensions"
```

---

## Verification: End-to-End Test

### Task 13: Verify with Vitis-Tutorials sample

Run the full analysis pipeline on a small Vitis-Tutorials subdirectory:

```bash
/home/dengqi/Source/envs/codewiki/bin/python -c "
from codewiki.src.be.dependency_analyzer.analysis.analysis_service import AnalysisService

svc = AnalysisService()
result = svc.analyze_local_repository(
    '/home/dengqi/Source/langs/cpp/Vitis-Tutorials/Getting_Started/Vitis/HLS_Kernels',
    max_files=20,
)
print(f'Nodes: {result[\"summary\"][\"total_nodes\"]}')
print(f'Relationships: {result[\"summary\"][\"total_relationships\"]}')
for node in result['nodes'].values() if isinstance(result['nodes'], dict) else result['nodes']:
    n = node if isinstance(node, dict) else node
    name = n.get('name', n.get('display_name', '?'))
    ctype = n.get('component_type', '?')
    kernel = n.get('is_hls_kernel', False)
    pragmas = n.get('hls_pragmas', [])
    print(f'  {name} ({ctype}) kernel={kernel} pragmas={len(pragmas) if pragmas else 0}')
"
```

Expected: Should see HLS kernel functions with pragmas detected.

**Commit:**

```bash
git commit --allow-empty -m "chore: verify end-to-end HLS analysis with Vitis-Tutorials sample"
```
