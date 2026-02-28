# Design: Compiled Language & HLS Support Enhancement

**Date:** 2026-02-28
**Status:** Approved

## Problem

CodeWiki's C/C++ analysis is limited to intra-file function/struct/class extraction. Critical gaps:

1. No `#include` tracking — header-source relationships invisible
2. CMake/Makefile targets not linked to source files
3. No cross-file data flow or ownership/lifetime tracking
4. No HLS pragma recognition or hardware semantic understanding
5. No cross-file hardware data flow tracing (host → kernel → streaming)

Reference projects: Vitis-Tutorials (1685 C/C++ files, 425 Makefiles, 207 Tcl scripts, 1200+ HLS pragmas), GitNexus (tree-sitter query patterns for C/C++).

## Architecture: Three Layers

```
Layer 3: HLS Hardware Semantics + Hardware Data Flow
    ├── HLS pragma extraction & semantic classification
    ├── Kernel top-function identification (.cfg, extern "C", v++ commands)
    ├── Cross-file hardware flow (system.cfg streaming, hls::stream, m_axi topology)
    └── Hardware architecture view output

Layer 2: Cross-File Data Flow + Ownership Tracking
    ├── DataFlowEdge model (param type, direction, ownership, lifetime)
    ├── Parameter-level transfer chains across files
    ├── Ownership pattern recognition (malloc/free, new/delete, smart pointers, RAII)
    └── DataFlowGraph post-processing step

Layer 1: C/C++ Analyzer Enhancement
    ├── Enhanced tree-sitter extraction (GitNexus query patterns)
    ├── Header-source file pairing
    ├── CMake → source file association
    └── Makefile → header dependency association
```

## Layer 1: C/C++ Analyzer Enhancement

### 1.1 Enhanced C Analyzer (`analyzers/c.py`)

Reference: GitNexus `C_QUERIES` pattern. Add extraction for:

| Node Type | tree-sitter Query | Output |
|-----------|------------------|--------|
| `#include` | `preproc_include` → `path` child | `CallRelationship(type="include")` |
| `#define` macro | `preproc_def`, `preproc_function_def` | `Node(component_type="macro")` |
| `union` | `union_specifier` → `type_identifier` | `Node(component_type="union")` |
| `enum` | `enum_specifier` → `type_identifier` | `Node(component_type="enum")` |
| `typedef` | `type_definition` → `type_identifier` | `Node(component_type="typedef")` |
| field call | `call_expression` → `field_expression` → `field_identifier` | `CallRelationship` |

### 1.2 Enhanced C++ Analyzer (`analyzers/cpp.py`)

Reference: GitNexus `CPP_QUERIES` pattern. Add extraction for:

| Node Type | tree-sitter Query | Output |
|-----------|------------------|--------|
| `namespace` | `namespace_definition` → `namespace_identifier` | `Node(component_type="namespace")` |
| template class | `template_declaration` → `class_specifier` | `Node(component_type="template_class")` |
| template function | `template_declaration` → `function_definition` | `Node(component_type="template_function")` |
| qualified call | `call_expression` → `qualified_identifier` → `identifier` | `CallRelationship` |
| template call | `call_expression` → `template_function` → `identifier` | `CallRelationship` |
| `#include` | `preproc_include` (same as C) | `CallRelationship(type="include")` |

### 1.3 Header-Source File Pairing

Post-processing in `CallGraphAnalyzer.analyze_code_files()` after all files analyzed:

1. Build basename index: strip extensions, group files by stem (`foo.h`, `foo.cpp`, `foo.cc`)
2. Confirm pairing via `#include` relationships (if `foo.cpp` includes `foo.h`, pair is confirmed)
3. Inject synthetic `CallRelationship(type="header_impl")` between paired files
4. In clustering (`cluster_modules.py`), paired files get high co-location weight (1.0 instead of 0.3)

### 1.4 CMake Source File Association

Enhance `analyzers/cmake.py`:

For `add_executable` and `add_library` commands:
- Extract the target name (first argument)
- Extract source file list (remaining arguments)
- Create `CallRelationship(type="compile_target", caller=target_id, callee=source_path)` for each source
- Resolve source paths relative to CMakeLists.txt directory

For `target_link_libraries`:
- Extract target and library names
- Create `CallRelationship(type="link_dependency")` between targets

### 1.5 Makefile Header Dependency Association

Enhance `analyzers/makefile.py`:

For rules like `main.o: main.c header.h`:
- Already extracts target → prerequisite relationships
- Enhance to distinguish `.c`/`.cpp` sources from `.h` headers in prerequisites
- Tag relationships: `type="compile_dep"` for source, `type="header_dep"` for headers

### Files Modified (Layer 1)

- `codewiki/src/be/dependency_analyzer/analyzers/c.py` — add #include, macro, union, enum, typedef extraction
- `codewiki/src/be/dependency_analyzer/analyzers/cpp.py` — add namespace, template, qualified/template calls, #include
- `codewiki/src/be/dependency_analyzer/analyzers/cmake.py` — add source file and link dependency extraction
- `codewiki/src/be/dependency_analyzer/analyzers/makefile.py` — add header dependency tagging
- `codewiki/src/be/dependency_analyzer/analysis/call_graph_analyzer.py` — add header-source pairing post-processing
- `codewiki/src/be/dependency_analyzer/models/core.py` — add `relationship_type` field to `CallRelationship`

---

## Layer 2: Cross-File Data Flow + Ownership Tracking

### 2.1 Data Model Extensions

In `models/core.py`:

```python
class DataFlowEdge(BaseModel):
    param_name: str                    # Parameter name at call site
    param_type: Optional[str] = None   # Type string (e.g., "int*", "std::vector<int>&")
    direction: str = "in"              # "in" | "out" | "inout"
    ownership: Optional[str] = None    # "transfer" | "borrow" | "shared" | "copy"
    lifetime_hint: Optional[str] = None  # "caller_scope" | "callee_owns" | "static" | "heap"

class CallRelationship(BaseModel):
    # ... existing fields ...
    relationship_type: Optional[str] = None  # "call" | "include" | "header_impl" | "compile_target" | ...
    data_flow: Optional[List[DataFlowEdge]] = None
```

### 2.2 Parameter Type Extraction

In C/C++ analyzers, when extracting function definitions:
- Parse parameter declarations to extract type + name
- Store in `Node.parameters` as structured data (currently just names)
- Enhance `parameters` field: `List[ParamInfo]` with `name`, `type`, `is_pointer`, `is_reference`, `is_const`

### 2.3 Ownership Inference Rules

At call sites, infer ownership from argument expressions:

| Pattern | Direction | Ownership | Lifetime |
|---------|-----------|-----------|----------|
| `const T&` parameter | in | borrow | caller_scope |
| `T&` parameter | inout | borrow | caller_scope |
| `T*` parameter | inout | borrow | caller_scope |
| `const T*` parameter | in | borrow | caller_scope |
| `T` (value) parameter | in | copy | callee_owns |
| `std::unique_ptr<T>` param | in | transfer | callee_owns |
| `std::shared_ptr<T>` param | in | shared | ref_counted |
| `std::move(x)` argument | in | transfer | callee_owns |
| `malloc()`/`new` return | out | — | heap |
| `free(p)`/`delete p` | in | — | deallocated |

### 2.4 Cross-File Data Flow Graph Construction

New module: `codewiki/src/be/dependency_analyzer/analysis/data_flow_analyzer.py`

Post-processing step after `CallGraphAnalyzer.analyze_code_files()`:

1. **Build function signature index**: Map function ID → parameter types
2. **For each CallRelationship**: Match call-site arguments to callee parameters, create `DataFlowEdge` entries
3. **Chain construction**: If `funcA(buf)` → `funcB(input)` → `funcC(data)`, build chain: `buf → input → data`
4. **Lifecycle tracking**: Find `malloc/new` allocation sites, trace through chain, find matching `free/delete`
5. **Output**: `DataFlowGraph` attached to analysis result, queryable by variable or function

### Files Modified/Created (Layer 2)

- `codewiki/src/be/dependency_analyzer/models/core.py` — add `DataFlowEdge`, `ParamInfo`, enhance `CallRelationship`
- `codewiki/src/be/dependency_analyzer/analyzers/c.py` — extract parameter types in function definitions
- `codewiki/src/be/dependency_analyzer/analyzers/cpp.py` — extract parameter types, detect smart pointer/move patterns
- **NEW** `codewiki/src/be/dependency_analyzer/analysis/data_flow_analyzer.py` — cross-file data flow graph builder
- `codewiki/src/be/dependency_analyzer/analysis/call_graph_analyzer.py` — invoke data flow analyzer as post-processing

---

## Layer 3: HLS Hardware Semantics + Hardware Data Flow

### 3.1 HLS Pragma Model

In `models/core.py`:

```python
class HLSPragma(BaseModel):
    pragma_type: str             # "INTERFACE" | "PIPELINE" | "DATAFLOW" | "UNROLL" | ...
    params: Dict[str, str]       # {"port": "mem", "bundle": "gmem", "offset": "slave"}
    target: Optional[str] = None # Associated variable/function name
    line: int
    hardware_semantic: str       # Human-readable hardware meaning

class Node(BaseModel):
    # ... existing fields ...
    hls_pragmas: Optional[List[HLSPragma]] = None
    is_hls_kernel: bool = False
```

### 3.2 Pragma Extraction in C/C++ Analyzers

tree-sitter parses `#pragma` as `preproc_call` nodes. Extract and classify:

```
#pragma HLS INTERFACE m_axi port=mem offset=slave bundle=gmem
→ HLSPragma(
    pragma_type="INTERFACE",
    params={"port": "mem", "offset": "slave", "bundle": "gmem"},
    target="mem",
    hardware_semantic="AXI Master memory interface, bundle 'gmem'"
  )
```

**Pragma type → hardware semantic mapping:**

| Type | Semantic Description Template |
|------|------------------------------|
| INTERFACE m_axi | "AXI Master memory interface, bundle '{bundle}'" |
| INTERFACE s_axilite | "AXI-Lite control/status register" |
| INTERFACE axis | "AXI-Stream data port" |
| PIPELINE II=N | "Pipelined with initiation interval {N} cycles" |
| DATAFLOW | "Task-level pipelining with automatic FIFOs between functions" |
| UNROLL factor=N | "Loop unrolled {N}x for parallel execution" |
| ARRAY_PARTITION | "Array partitioned for parallel memory access" |
| INLINE | "Function inlined into caller (no separate hardware module)" |
| STREAM | "Variable implemented as hardware FIFO" |

### 3.3 Kernel Top-Function Identification

Multiple detection strategies:

1. **`.cfg` file parser** (new analyzer: `analyzers/vitis_cfg.py`):
   - Parse `syn.top=<function_name>` → mark function as kernel
   - Parse `syn.file=<source_path>` → associate source file
   - Parse connectivity sections for streaming connections

2. **`extern "C"` detection** in C++ analyzer:
   - Functions wrapped in `extern "C" { ... }` block → candidate HLS kernel
   - Combined with presence of HLS pragmas → confirmed kernel

3. **Makefile `v++` command detection** in Makefile analyzer:
   - Rules containing `v++` or `vitis_hls` commands → HLS compilation targets
   - Extract source file arguments

### 3.4 Cross-File Hardware Data Flow

**a) Vitis Config Parser (`analyzers/vitis_cfg.py`):**

Parse `system.cfg` / `*.cfg` files:
```ini
[connectivity]
stream_connect=mm2s_1.s:s2mm_1.s
nk=mm2s:1:mm2s_1
nk=s2mm:1:s2mm_1
```

Extract:
- Kernel instance mappings (`nk=kernel:count:instance_name`)
- Stream connections between kernel ports
- Memory bank assignments (`sp=kernel.port:DDR[0]`)

Output: `CallRelationship(type="stream_connect")` and `CallRelationship(type="memory_map")`

**b) hls::stream Connection Tracking:**

In C++ analyzer, when `hls::stream<T>` parameters detected:
- Tag parameter with `is_hls_stream=True`
- Track `.write()` calls → producer side
- Track `.read()` calls → consumer side
- Build producer→consumer data flow edges with stream type info

**c) Memory Interface Topology:**

From INTERFACE pragmas:
- Group ports by `bundle` name → same physical memory interface
- Map bundles to memory banks (from `.cfg` `sp=` directives)
- Output topology: `kernel.port → bundle → DDR/HBM bank`

### 3.5 Hardware Architecture View

For HLS projects, generate additional metadata in analysis output:

```python
class HardwareArchitecture(BaseModel):
    kernels: List[KernelInfo]           # Kernel list with ports
    stream_connections: List[StreamEdge] # Kernel-to-kernel streaming
    memory_topology: List[MemoryMap]    # Port-to-memory mappings
    performance_annotations: Dict       # Pipeline II, dataflow regions

class KernelInfo(BaseModel):
    name: str
    top_function: str
    source_file: str
    ports: List[PortInfo]               # AXI-Stream, AXI-Master, AXI-Lite

class PortInfo(BaseModel):
    name: str
    protocol: str  # "m_axi" | "s_axilite" | "axis"
    bundle: Optional[str]
    data_type: Optional[str]
    direction: str  # "in" | "out" | "inout"
```

This is passed to the documentation generator, which can produce hardware-aware documentation sections.

### Files Modified/Created (Layer 3)

- `codewiki/src/be/dependency_analyzer/models/core.py` — add `HLSPragma`, `HardwareArchitecture`, `KernelInfo`, `PortInfo`
- `codewiki/src/be/dependency_analyzer/analyzers/c.py` — add pragma extraction for C HLS code
- `codewiki/src/be/dependency_analyzer/analyzers/cpp.py` — add pragma extraction, extern "C" detection, hls::stream tracking
- **NEW** `codewiki/src/be/dependency_analyzer/analyzers/vitis_cfg.py` — Vitis .cfg file parser
- `codewiki/src/be/dependency_analyzer/analyzers/makefile.py` — detect v++/vitis_hls build commands
- `codewiki/src/be/dependency_analyzer/analysis/call_graph_analyzer.py` — integrate HLS analysis, build hardware architecture view
- `codewiki/src/be/dependency_analyzer/analysis/analysis_service.py` — pass hardware architecture to results
- `codewiki/src/be/dependency_analyzer/utils/patterns.py` — add `.cfg`, `.xdc` to recognized extensions

---

## Implementation Priority

Layer 1 → Layer 2 → Layer 3 (each layer builds on the previous)

Within each layer, prioritize by impact:
- Layer 1: #include extraction > header-source pairing > CMake association > Makefile association
- Layer 2: Parameter type extraction > ownership inference > cross-file chain > lifecycle tracking
- Layer 3: Pragma extraction > kernel identification > stream tracking > full hardware architecture

## Testing Strategy

- Unit test each analyzer with sample files from Vitis-Tutorials
- Integration test with a small multi-file C/C++ project
- End-to-end test: generate documentation for a Vitis-Tutorials subdirectory
