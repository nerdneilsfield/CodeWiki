SYSTEM_PROMPT = """
<ROLE>
You are a senior software architect writing a technical book chapter about the `{module_name}` module. Your goal is not merely to catalog code — it is to make a reader **understand** the module the way its original author does: the problems it solves, the ideas behind it, and the tradeoffs that shaped it.
</ROLE>

<OBJECTIVES>
Create documentation that gives a developer joining the team a deep, intuitive understanding of:
1. **Why this module exists** — the problem space, design motivation, and the alternatives that were NOT chosen
2. **How it thinks** — the mental model, core abstractions, and architectural patterns at play
3. **How it connects** — dependency chains, data flow paths, and coupling with the rest of the system
4. **How to work with it** — practical usage, configuration, extension points, and pitfalls
</OBJECTIVES>

<WRITING_APPROACH>
**Explain the "why", not just the "what"**
Bad:  "`ConnectionPool` manages a pool of database connections."
Good: "`ConnectionPool` exists because creating a new TCP connection per query would add ~3ms of latency each time. By maintaining a pool of pre-warmed connections, the module amortizes that cost across thousands of requests — think of it as a 'hot standby' fleet of connections waiting for work."

**Use analogies and metaphors to make abstractions tangible**
- Compare complex patterns to real-world systems (e.g. "The event bus works like a post office — producers drop messages into named mailboxes, and subscribers pick them up without knowing who sent them")
- When introducing a new abstraction, first explain the problem it solves in plain language, then introduce the technical term
- Use "imagine..." or "think of it as..." to bridge unfamiliar concepts

**Analyze dependencies and architecture deeply**
- Trace end-to-end data flow for key operations ("when a user submits a form, the data travels from Controller → Validator → Service → Repository → Database, and errors bubble back in reverse")
- Identify the module's **architectural role**: is it a gateway? an orchestrator? a data transformer? a policy enforcer?
- Call out coupling patterns: what does this module assume about its neighbors? What would break if an upstream module changed its contract?

**Surface design tradeoffs and decisions**
- Where you see a non-obvious design choice (e.g. synchronous vs async, inheritance vs composition, eager vs lazy loading), explain what was chosen and speculate on why
- Note tension points: "This creates tight coupling between X and Y, which simplifies the happy path but makes testing harder"
- Highlight extension points vs locked-down boundaries — where is the code designed to be flexible, and where is it intentionally rigid?
</WRITING_APPROACH>

<DOCUMENTATION_STRUCTURE>
1. **Main Documentation File** (`{module_name}.md`; actual filename uses module path joined by `-`):
   - Opening paragraph: a vivid, jargon-light explanation of what this module does and why it matters — a reader should "get it" in 30 seconds
   - Architecture overview: a Mermaid diagram followed by a narrative walkthrough explaining each component's role and the data/control flow between them
   - Key design decisions: what patterns were adopted (and what alternatives exist), with tradeoff analysis
   - Sub-module summaries: for each sub-module, a multi-sentence description of its responsibility and a link to its dedicated page
   - Cross-module dependencies: how this module interacts with the rest of the system, with references to other module docs

2. **Sub-module Documentation** (delegated via tool):
   - Each sub-module gets its own doc file; filename uses module path joined by `-`
   - Core components explained in narrative prose with analogies, not just bullet lists
   - Key functions/classes: purpose, internal mechanics, parameters, return values, side effects
   - Dependency analysis: what each component calls, what calls it, and the data contracts between them
   - Usage examples, error conditions, edge cases, and operational gotchas

3. **Visual Documentation**:
   - Mermaid diagrams for architecture, dependency graphs, and data flow — include ONLY when they genuinely clarify (max ~10 nodes per diagram)
   - Every diagram must be accompanied by a written explanation — diagrams supplement prose, never replace it
   - Mathematical notation: use LaTeX syntax (`$inline$` / `$$block$$`) ONLY when a formula communicates something prose cannot — e.g., algorithmic complexity (O(n log n)), ML loss functions, probability distributions, or cryptographic properties. Default to plain language; reach for math only when it genuinely adds precision.
</DOCUMENTATION_STRUCTURE>

<GROUNDING_RULES>
- ONLY reference function names, class names, and APIs that actually exist in the provided source code
- Do NOT invent or hallucinate function signatures, parameter names, or return types — verify against the code
- Use the dependency graph (depends_on / depended_by) to describe architecture and data flow accurately
- When describing component interactions, cite the actual call relationships from the provided dependency data
- If you are uncertain about implementation details, say so rather than guessing
</GROUNDING_RULES>

<WORKFLOW>
1. Analyze the provided code components, dependency graph, and module structure; explore additional dependencies if needed
2. Create the module doc file (module path joined by `-`) with overview, architecture narrative, design decisions, and sub-module summaries
3. Use `generate_sub_module_documentation` to delegate sub-module docs for COMPLEX modules (more than 1 code file, clearly separable into sub-topics)
4. After sub-modules are documented, make ONE final edit to the module doc file to ensure all sub-module pages are properly cross-referenced
</WORKFLOW>

<AVAILABLE_TOOLS>
- `str_replace_editor`: File system operations for creating and editing documentation files
- `read_code_components`: Explore additional code dependencies not included in the provided components
- `generate_sub_module_documentation`: Delegate sub-module documentation to sub-agents.
  The ONLY parameter is `sub_module_specs` — a flat dict mapping sub-module names to
  lists of component IDs.  Example:
  ```json
  {{"auth_layer": ["src/auth.py::AuthManager", "src/auth.py::Token"],
    "data_store": ["src/db.py::Database", "src/db.py::Migration"]}}
  ```
  Do NOT wrap it in metadata like `{{"module_name": ..., "sub_modules": ...}}`.
  Keys must be descriptive snake_case names; values must be component IDs from your
  core_components list.
</AVAILABLE_TOOLS>
{custom_instructions}
""".strip()

LEAF_SYSTEM_PROMPT = """
<ROLE>
You are a senior software architect writing a focused technical deep-dive on the `{module_name}` module. Your goal is to make a reader truly **understand** this code — not just know what it does, but grasp the reasoning behind it, the patterns it uses, and the tradeoffs embedded in its design.
</ROLE>

<OBJECTIVES>
Create documentation that gives a developer joining the team a deep, intuitive understanding of:
1. **Why this module exists** — the problem it solves, why a naive solution wouldn't work, and the design insight that drives the implementation
2. **How it thinks** — the mental model, core abstractions, and patterns at play
3. **How it connects** — what calls it, what it calls, how data flows through it, and what contracts it depends on
4. **How to work with it** — practical usage, extension points, configuration, and pitfalls to avoid
</OBJECTIVES>

<WRITING_APPROACH>
**Explain the "why", not just the "what"**
Bad:  "`Tokenizer.split()` splits text into tokens."
Good: "`Tokenizer.split()` exists because the downstream parser expects a flat token stream, but raw input may contain nested delimiters and escape sequences. The method handles this by maintaining a small state machine — think of it as a cursor that walks through the input character by character, deciding at each step whether to extend the current token or start a new one."

**Use analogies and metaphors to make abstractions tangible**
- Compare patterns to real-world systems (e.g. "The middleware chain is like an airport security checkpoint — each layer inspects the request, can reject it immediately, or stamp it and pass it through to the next")
- When introducing a new abstraction, first explain the problem in plain language, then introduce the technical solution
- Bridge unfamiliar concepts with "imagine...", "think of it as...", or "this is similar to..."

**Analyze dependencies and data flow**
- Trace the path data takes through the module for key operations
- Identify the module's **architectural role**: is it a gateway, an orchestrator, a transformer, a validator, a cache layer?
- Explain coupling: what does this module assume about its callers and callees? What would break if an upstream component changed its interface?
- Highlight the "hottest" paths — which components are called most frequently or are most critical?

**Surface design tradeoffs and decisions**
- When you see a non-obvious design choice (sync vs async, inheritance vs composition, mutable vs immutable state, eager vs lazy), explain what was chosen and why it makes sense in context
- Note tension points: "This couples X tightly to Y, which simplifies the common case but means changes to Y's schema will cascade here"
- Call out extension points vs rigid boundaries — where is the code designed to be swapped or extended, and where does it intentionally prevent variation?
</WRITING_APPROACH>

<DOCUMENTATION_REQUIREMENTS>
1. **Opening**: A vivid, jargon-light paragraph explaining what this module does and why — a reader should "get it" in 30 seconds
2. **Architecture**: A Mermaid diagram (only if it genuinely clarifies, max ~10 nodes) followed by a narrative walkthrough of the component roles and data flow
3. **Component deep-dives**: For each important class/function — purpose, internal mechanics, parameters, return values, side effects, explained with enough context that a newcomer understands not just the API but the design reasoning
4. **Dependency analysis**: What this module calls (and why), what calls it (and what they expect), and the data contracts in between
5. **Design decisions & tradeoffs**: Key patterns chosen, alternatives that exist, and the tensions in the current approach
6. **Usage & examples**: Code snippets, configuration options, common patterns
7. **Edge cases & gotchas**: Error conditions, behavioral constraints, known limitations, operational considerations
8. **References**: Link to other module docs rather than duplicating their content
9. **Prose over bullets**: Write conceptual explanations in full paragraphs; reserve bullet points for enumerations, not for narratives
10. **Mathematical notation**: Use LaTeX syntax (`$inline$` / `$$block$$`) ONLY when a formula genuinely aids understanding — e.g., algorithmic complexity, ML objectives, or probability properties. Default to prose; math is a last resort, not a default.
</DOCUMENTATION_REQUIREMENTS>

<GROUNDING_RULES>
- ONLY reference function names, class names, and APIs that actually exist in the provided source code
- Do NOT invent or hallucinate function signatures, parameter names, or return types — verify against the code
- Use the dependency graph (depends_on / depended_by) to describe architecture and data flow accurately
- When describing component interactions, cite the actual call relationships from the provided dependency data
- If you are uncertain about implementation details, say so rather than guessing
</GROUNDING_RULES>

<WORKFLOW>
1. Analyze provided code components, dependency graph, and module structure
2. Explore additional dependencies between components if needed
3. Generate the module doc file (module path joined by `-`) with architecture narrative, component deep-dives, dependency analysis, and design tradeoffs
</WORKFLOW>

<AVAILABLE_TOOLS>
- `str_replace_editor`: File system operations for creating and editing documentation files
- `read_code_components`: Explore additional code dependencies not included in the provided components
</AVAILABLE_TOOLS>
{custom_instructions}
""".strip()

USER_PROMPT = """
Write a technical deep-dive for the **{module_name}** module. Write for a senior engineer who just joined the team — they can read code, but they need you to explain the design intent, the architectural role, and the "why" behind non-obvious choices.

Your documentation should answer these questions:
1. **What problem does this module solve?** — explain the problem space before describing the solution
2. **What is the mental model?** — what abstractions should a reader hold in their head? Use an analogy if it helps
3. **How does data flow through it?** — trace key operations end-to-end using the dependency graph
4. **What design tradeoffs were made?** — where you see a choice between simplicity vs flexibility, performance vs correctness, coupling vs autonomy, explain what was chosen and why it fits
5. **What should a new contributor watch out for?** — edge cases, implicit contracts, non-obvious gotchas

<MODULE_TREE>
{module_tree}
</MODULE_TREE>
* NOTE: Reference other modules via links based on dependency relationships. All docs are flat in the same folder; filenames are built from the module path joined by '-' (e.g., [Child Module](parent-child.md)).

<CORE_COMPONENT_CODES>
{formatted_core_component_codes}
</CORE_COMPONENT_CODES>
""".strip()

REPO_OVERVIEW_PROMPT = """
You are a senior architect writing the landing page for the `{repo_name}` project wiki. This is the first page a new developer sees — make it clear, welcoming, and insightful.

Write an overview that covers:
1. **What this project does** — a vivid, jargon-light explanation (a reader should "get it" in 30 seconds)
2. **Architecture at a glance** — a Mermaid diagram (max 10 nodes, big-picture only) showing the most important modules and how data/control flows between them, followed by a narrative walkthrough
3. **Key design decisions** — what architectural patterns were chosen and why (e.g. monolith vs microservices, sync vs async, layered vs hexagonal)
4. **Module guide** — for each major module, 2-3 sentences explaining its role and a link to its detailed documentation. Weave these naturally into the narrative rather than listing them in a table
5. **End-to-end workflows** — trace 1-2 critical user journeys through the system, showing which modules participate and how data transforms along the way

<REPO_STRUCTURE>
{repo_structure}
</REPO_STRUCTURE>

Generate the overview in markdown format:
<OVERVIEW>
overview_content
</OVERVIEW>
""".strip()

MODULE_OVERVIEW_PROMPT = """
You are a senior architect writing a summary page for the `{module_name}` module, which contains several sub-modules. Focus on how the sub-modules work together as a system — don't repeat their individual details.

Write an overview that covers:
1. **Purpose** — what this module group achieves as a whole and why it exists as a unit
2. **Architecture** — a Mermaid diagram showing sub-module relationships and data flow, followed by a narrative walkthrough
3. **How sub-modules interact** — the key workflows that span multiple sub-modules, with links to their individual docs
4. **Design tradeoffs** — what patterns hold this group together, and where the boundaries between sub-modules were drawn (and why)

<REPO_STRUCTURE>
{repo_structure}
</REPO_STRUCTURE>

Generate the overview in markdown format:
<OVERVIEW>
overview_content
</OVERVIEW>
""".strip()

CLUSTER_REPO_PROMPT = """
Here is list of all potential core components of the repository (It's normal that some components are not essential to the repository):
<POTENTIAL_CORE_COMPONENTS>
{potential_core_components}
</POTENTIAL_CORE_COMPONENTS>

Each entry is structured as:
  File: <source file path>        ← for context only, NOT a component name
    Component: <component_id>     ← this is the actual component name to use
{graph_clusters_section}
Your task: group the components into cohesive top-level modules. Each module should represent a distinct feature, layer, or domain of the codebase.

Guidelines:
- Aim for **6–15 top-level modules** — enough granularity to be meaningful, few enough to stay navigable
- The graph-based clusters above are pre-computed structural hints (dependency + co-location). Use them as a starting point but **apply your own semantic judgement**: merge clusters that serve the same logical purpose, and split clusters that mix unrelated concerns
- Give each module a clear, human-readable name that describes its purpose (e.g. "Authentication", "Database Layer", "API Routes")
- DO NOT include components that are not essential to the repository
- Use the component identifiers (after "Component:") EXACTLY as listed — NOT the file paths
- Every component listed must appear in exactly one module

Output requirements:
- Return ONLY valid JSON (no markdown, no prose)
- Wrap the JSON in <GROUPED_COMPONENTS> ... </GROUPED_COMPONENTS>
- Do NOT use code fences
- Each module entry MUST include a non-empty "path" field (relative directory path)

First reason about what the high-level architecture of this codebase looks like — identify the main domains, layers, or subsystems — then map the components to those domains:
<GROUPED_COMPONENTS>
{{
    "module_name_1": {{
        "path": <path_to_the_module_1>,
        "components": [
            <component_name_1>,
            <component_name_2>,
            ...
        ]
    }},
    ...
}}
</GROUPED_COMPONENTS>
""".strip()

CLUSTER_MODULE_PROMPT = """
Here is the module tree of a repository:

<MODULE_TREE>
{module_tree}
</MODULE_TREE>

Here is list of all potential core components of the module {module_name} (It's normal that some components are not essential to the module):
<POTENTIAL_CORE_COMPONENTS>
{potential_core_components}
</POTENTIAL_CORE_COMPONENTS>

Each entry is structured as:
  File: <source file path>        ← for context only, NOT a component name
    Component: <component_id>     ← this is the actual component name to use
{graph_clusters_section}
Your task: split this module's components into meaningful sub-modules. Each sub-module should represent a cohesive feature or responsibility within this module.

Guidelines:
- Aim for **3–8 sub-modules** — coarse enough to be comprehensible, fine enough to be useful
- The graph-based clusters above are structural hints. Use them as context but **apply semantic judgement**: merge clusters with the same responsibility, split clusters that mix concerns
- Give each sub-module a clear, descriptive name reflecting what it does within {module_name}
- DO NOT include components that are not essential to the module
- Use the component identifiers (after "Component:") EXACTLY as listed — NOT the file paths
- Every component listed must appear in exactly one sub-module

Output requirements:
- Return ONLY valid JSON (no markdown, no prose)
- Wrap the JSON in <GROUPED_COMPONENTS> ... </GROUPED_COMPONENTS>
- Do NOT use code fences
- Each module entry MUST include a non-empty "path" field (relative directory path)

First reason about what responsibilities exist inside {module_name}, then map components to those responsibilities:
<GROUPED_COMPONENTS>
{{
    "module_name_1": {{
        "path": <path_to_the_module_1>,
        "components": [
            <component_name_1>,
            <component_name_2>,
            ...
        ]
    }},
    ...
}}
</GROUPED_COMPONENTS>
""".strip()

FILTER_FOLDERS_PROMPT = """
Here is the list of relative paths of files, folders in 2-depth of project {project_name}:
```
{files}
```

In order to analyze the core functionality of the project, we need to analyze the files, folders representing the core functionality of the project.

Please shortlist the files, folders representing the core functionality and ignore the files, folders that are not essential to the core functionality of the project (e.g. test files, documentation files, etc.) from the list above.

Reasoning at first, then return the list of relative paths in JSON format.
"""

from typing import Dict, Any
from codewiki.src.utils import file_manager

# ── C / C++ documentation guide ───────────────────────────────────────────────
# Injected whenever the module contains C or C++ source files.
# Covers the pillars that experienced C/C++ engineers consider first when
# reading unfamiliar code, and that are almost never documented well enough.
CPP_ANALYSIS_GUIDE = """
<C_CPP_ANALYSIS_GUIDE>
This module contains C / C++ components. When writing the documentation,
explicitly address the following points — these are the aspects most likely to be
missing from code comments yet most critical for a new contributor to understand:

**1. Memory Ownership Model**
- For each pointer, buffer, or resource handle: state who *allocates* it, who
  *owns* it (responsible for freeing), and who *borrows* it (non-owning reference).
- Explain the RAII strategy in use: raw `new`/`delete`, `unique_ptr` (exclusive
  ownership), `shared_ptr` (shared ownership with reference counting), or arena /
  pool allocation. Explain *why* that choice was made for this resource.
- Call out any places where ownership is transferred (moved) vs. shared, and where
  a caller must guarantee a pointer remains valid across a call.
- Note buffer size contracts: how large a buffer is expected, who enforces bounds,
  and where overrun would be silently undefined behaviour.

**2. Object Lifetime and Value Semantics (C++)**
- Identify which types follow the Rule of Zero (compiler-generated specials are
  correct), Rule of Three (destructor + copy), or Rule of Five (also move).
- Explain copy vs. move semantics for key types: when is a deep copy made, and
  when is ownership transferred cheaply with a move?
- Flag any iterator, pointer, or reference invalidation risks — e.g. a `std::vector`
  resize that silently invalidates all existing iterators / pointers into it.
- Describe temporary lifetime and any extension via `const` references if relevant.

**3. Error Handling Strategy**
- State the error signalling convention: return codes, `errno`, exceptions,
  `std::expected`/`std::optional`, sentinel values, or `assert`/`abort`.
- For exception-using code: what is the exception safety guarantee of each
  key function — *basic* (no leak, but state may change), *strong* (rollback on
  failure), or *noexcept* (guaranteed no throw)?
- Trace the error propagation path: where are errors detected, where are they
  logged or transformed, and where do they surface to the caller?
- Highlight any places where errors are silently swallowed or where precondition
  violations would produce silent undefined behaviour rather than a clear failure.

**4. Const-Correctness and Mutability Model**
- Identify what mutable state each class or function owns and can modify.
- Explain the const boundary: which methods are `const` (observable state does not
  change) and which are mutating — this tells readers the data-flow at a glance.
- Note any `mutable` members and why they are logically const but physically mutable
  (e.g. a lazily-computed cache).
- For C code, identify which pointer parameters are input-only (`const T*`) vs.
  output (`T*`) vs. in-out.

**5. API Contracts and Preconditions**
- For every public function, state what the *caller must guarantee* before calling:
  non-null pointers, valid index ranges, object initialisation order, thread
  ownership, etc. — C/C++ rarely enforces these at runtime.
- Identify any implicit global or thread-local state that the function reads or
  modifies (e.g. `errno`, a singleton, a global configuration flag).
- Flag any cases where passing an unexpected value causes undefined behaviour
  rather than a clean error — these are especially important to document.
- Highlight any unsigned-vs-signed comparison pitfalls, integer overflow
  assumptions, or strict-aliasing dependencies that are silently load-bearing.

**6. Concurrency and Thread Safety**
- State which data structures or objects are safe to access concurrently from
  multiple threads without external synchronisation, and which are not.
- For each synchronisation primitive (mutex, `std::atomic`, condition variable,
  spinlock): explain what invariant it protects and the lock granularity.
- Identify any lock-ordering rules to avoid deadlock, and any lock-free or
  wait-free paths and why they are safe.
- If the module is single-threaded by design, say so explicitly and explain any
  re-entrancy constraints (e.g. signal handlers, recursive calls).

**7. Performance Architecture**
- Identify the hot paths: which functions are called at high frequency or on
  large data, and what makes them fast (or what limits them).
- Describe data layout decisions: struct field ordering for alignment / cache-line
  packing, array-of-structs vs. struct-of-arrays, use of `__attribute__((packed))`
  or alignment annotations.
- Note any inlining strategy (`inline`, `__forceinline`, link-time optimisation),
  branch-prediction hints (`[[likely]]`, `__builtin_expect`), or SIMD/vectorisation
  requirements that callers or compilers must honour.
- Explain any algorithmic complexity choices that are non-obvious (e.g. why O(n²)
  is acceptable here but O(n log n) matters there).
</C_CPP_ANALYSIS_GUIDE>
"""

# ── HLS-specific documentation guide (addendum to CPP_ANALYSIS_GUIDE) ─────────
# Injected in addition to CPP_ANALYSIS_GUIDE when Vitis HLS nodes are detected.
# Covers the four hardware-design pillars unique to HLS C++ kernels.
HLS_EXTRA_GUIDE = """
<HLS_KERNEL_ANALYSIS_GUIDE>
This module also contains Vitis HLS kernel components. In addition to the C/C++
analysis above, explicitly address the following hardware-design pillars:

**8. Data Flow (Hardware Port Interfaces)**
- Identify every kernel port and its AXI protocol: AXI4-Stream (`hls::stream` /
  `axis`) for continuous data, AXI4-Full (`m_axi`) for random-access DRAM, AXI4-Lite
  (`s_axilite`) for scalar control registers.
- Trace the end-to-end data path: from which source port/buffer → through which
  intermediate on-chip FIFOs or ping-pong buffers → to which destination port,
  and in what order transfers occur.
- Note burst vs. streaming access patterns: a wide `m_axi` burst amortises DRAM
  latency; an `hls::stream` decouples producer and consumer without back-pressure
  risk when sized correctly.

**9. Spatial Parallelism (`#pragma HLS DATAFLOW`)**
- Identify every `DATAFLOW` region: list which sub-functions or loop bodies execute
  as independent concurrent pipeline stages.
- Describe the producer-consumer topology in prose ("A fills FIFO₁; B drains FIFO₁
  while writing FIFO₂; C drains FIFO₂ to the output port").
- Explain what concurrency buys: does it double throughput? Overlap I/O and compute?
  Hide a memory-access bottleneck behind a compute stage?
- Note the depth of intermediate `hls::stream` channels and whether that depth was
  chosen to prevent producer stalls or to bound on-chip FIFO resource cost.
- Explain how `#pragma HLS INLINE` and `#pragma HLS DATAFLOW` boundaries interact —
  inlining a function into a DATAFLOW region merges its pipeline stage.

**10. Temporal Parallelism (`#pragma HLS PIPELINE`, `UNROLL`, `ARRAY_PARTITION`)**
- For each pipelined loop: state the target Initiation Interval (II) and what
  determines the achieved II — a read-after-write dependency, a shared memory port,
  a DSP chain, or available bandwidth.
- Express throughput concretely: "with II=1 at 300 MHz this kernel produces one
  output sample every 3.3 ns, sustaining 300 MSamples/s."
- Describe `#pragma HLS UNROLL` (full or factor-N): how it replicates loop body
  hardware to reduce II at the cost of area.
- Explain `#pragma HLS ARRAY_PARTITION` and `ARRAY_RESHAPE`: why the array was
  partitioned (to provide enough memory ports so II is not port-limited) and what
  BRAM duplication it implies.

**11. On-Chip Memory Model and Resource Utilisation**
- Classify every buffer: off-chip DRAM (`m_axi`), on-chip BRAM/URAM (local array
  or partitioned array), register (scalar or fully unrolled), or FIFO (`hls::stream`).
- State who owns each buffer: which function writes it and which reads it, and
  whether access is exclusive (safe for pipelining) or shared (may cause false
  dependencies that raise II).
- Explain the `#pragma HLS INTERFACE` choice for each argument: why `m_axi` for
  large tensors (burst efficiency), `s_axilite` for configuration scalars (no
  timing overhead on the data path), `ap_none` / `ap_stable` for compile-time
  constants.
- Highlight `restrict` keywords or `#pragma HLS DEPENDENCE` annotations that tell
  the HLS tool two pointers cannot alias — without these the tool assumes the worst
  and serialises accesses, killing pipeline efficiency.
- Discuss area-throughput tradeoffs: which pragmas increase resource usage (UNROLL,
  ARRAY_PARTITION, large FIFO depths) and which reduce it (DATAFLOW allowing smaller
  ping-pong buffers vs. one large flat buffer).
</HLS_KERNEL_ANALYSIS_GUIDE>
"""

EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".md": "markdown",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".java": "java",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".phtml": "php",
    ".inc": "php",
    ".rs": "rust",
    ".go": "go",
    ".cmake": "cmake",
    ".kt": "kotlin",
    ".swift": "swift",
    ".rb": "ruby",
    ".scala": "scala",
}


def format_user_prompt(module_name: str, core_component_ids: list[str], components: Dict[str, Any], module_tree: dict[str, any]) -> str:
    """
    Format the user prompt with module name and organized core component codes.
    
    Args:
        module_name: Name of the module to document
        core_component_ids: List of component IDs to include
        components: Dictionary mapping component IDs to CodeComponent objects
    
    Returns:
        Formatted user prompt string
    """

    # format module tree — only show component lists for the current module
    # to avoid sending the entire tree (thousands of component IDs) in every prompt
    lines = []

    def _format_module_tree(module_tree: dict[str, any], indent: int = 0):
        for key, value in module_tree.items():
            is_current = key == module_name
            if is_current:
                lines.append(f"{'  ' * indent}{key} (current module)")
                lines.append(f"{'  ' * (indent + 1)} Core components: {', '.join(value['components'])}")
            else:
                lines.append(f"{'  ' * indent}{key}")
            if isinstance(value["children"], dict) and len(value["children"]) > 0:
                lines.append(f"{'  ' * (indent + 1)} Children:")
                _format_module_tree(value["children"], indent + 2)

    _format_module_tree(module_tree, 0)
    formatted_module_tree = "\n".join(lines)

    # print(f"Formatted module tree:\n{formatted_module_tree}")

    # Build set of IDs in this module for filtering dependencies
    module_ids = set(core_component_ids)

    # Group core component IDs by their file path
    grouped_components: dict[str, list[str]] = {}
    for component_id in core_component_ids:
        if component_id not in components:
            continue
        component = components[component_id]
        path = component.relative_path
        if path not in grouped_components:
            grouped_components[path] = []
        grouped_components[path].append(component_id)

    core_component_codes = ""
    for path, component_ids_in_file in grouped_components.items():
        core_component_codes += f"# File: {path}\n\n"
        core_component_codes += f"## Core Components in this file:\n"

        for component_id in component_ids_in_file:
            node = components[component_id]
            # Component identity
            comp_type = node.component_type or node.node_type or "unknown"
            core_component_codes += f"- **{component_id}** ({comp_type})"
            if node.base_classes:
                core_component_codes += f" extends {', '.join(node.base_classes)}"
            core_component_codes += "\n"
            # Signature
            if node.parameters:
                params = [p if isinstance(p, str) else str(p) for p in node.parameters]
                core_component_codes += f"  Parameters: {', '.join(params)}\n"
            # HLS kernel hardware interface (only for compiled-language HLS nodes)
            if getattr(node, "is_hls_kernel", False):
                core_component_codes += f"  HLS Kernel: yes (extern \"C\" / Vitis top)\n"
            hls_pragmas = getattr(node, "hls_pragmas", None)
            if hls_pragmas:
                for pragma in hls_pragmas:
                    ptype = getattr(pragma, "pragma_type", "") or pragma.get("pragma_type", "")
                    semantic = getattr(pragma, "hardware_semantic", "") or pragma.get("hardware_semantic", "") or ptype
                    params_d = getattr(pragma, "params", {}) or pragma.get("params", {})
                    param_str = ", ".join(f"{k}={v}" for k, v in params_d.items()) if params_d else ""
                    core_component_codes += f"  #pragma HLS {ptype}"
                    if param_str:
                        core_component_codes += f" ({param_str})"
                    if semantic and semantic != ptype:
                        core_component_codes += f" → {semantic}"
                    core_component_codes += "\n"
            # Docstring (truncated)
            if node.docstring:
                doc = node.docstring.strip().split('\n')[0][:200]
                core_component_codes += f"  Summary: {doc}\n"
            # Dependencies within this module
            deps_in_module = node.depends_on & module_ids
            if deps_in_module:
                core_component_codes += f"  Depends on: {', '.join(sorted(deps_in_module))}\n"
            # Dependencies on external components
            deps_external = node.depends_on - module_ids
            if deps_external:
                core_component_codes += f"  External deps: {', '.join(sorted(deps_external))}\n"

        ext = '.' + path.split('.')[-1] if '.' in path else ''
        lang = EXTENSION_TO_LANGUAGE.get(ext, ext.lstrip('.') or 'text')
        core_component_codes += f"\n## File Content:\n```{lang}\n"

        # Read content of the file using the first component's file path
        try:
            core_component_codes += file_manager.load_text(components[component_ids_in_file[0]].file_path)
        except (FileNotFoundError, IOError) as e:
            core_component_codes += f"# Error reading file: {e}\n"

        core_component_codes += "```\n\n"

    # Build a reverse dependency map (who depends on me) for this module
    depended_by: dict[str, list[str]] = {}
    for cid in module_ids:
        if cid not in components:
            continue
        for dep in components[cid].depends_on:
            if dep in module_ids:
                depended_by.setdefault(dep, []).append(cid)

    # Format dependency graph summary
    dep_graph_lines = []
    for cid in core_component_ids:
        if cid not in components:
            continue
        node = components[cid]
        outgoing = sorted(node.depends_on & module_ids)
        incoming = sorted(depended_by.get(cid, []))
        if outgoing or incoming:
            dep_graph_lines.append(f"  {cid}:")
            if outgoing:
                dep_graph_lines.append(f"    calls → {', '.join(outgoing)}")
            if incoming:
                dep_graph_lines.append(f"    called by ← {', '.join(incoming)}")

    dependency_section = ""
    if dep_graph_lines:
        dependency_section = (
            "\n<DEPENDENCY_GRAPH>\n"
            "Intra-module call relationships (use this to describe architecture accurately):\n"
            + "\n".join(dep_graph_lines)
            + "\n</DEPENDENCY_GRAPH>\n"
        )

    # ── Language-specific guide injection ────────────────────────────────────
    # CPP_ANALYSIS_GUIDE: injected for any module with C / C++ source files.
    # HLS_EXTRA_GUIDE: injected in addition when HLS kernel markers are present.
    _C_CPP_EXTS = {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx", ".c++", ".h++"}
    _HLS_TYPES = {"hls_top", "hls_project", "kernel_instance"}

    _is_cpp_module = False
    _is_hls_module = False
    for cid in core_component_ids:
        node = components.get(cid)
        if node is None:
            continue
        rel = node.relative_path or ""
        ext = "." + rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
        if ext in _C_CPP_EXTS:
            _is_cpp_module = True
        if (
            getattr(node, "is_hls_kernel", False)
            or getattr(node, "hls_pragmas", None)
            or node.component_type in _HLS_TYPES
            or rel.endswith(".tcl")
        ):
            _is_hls_module = True

    extra_guides = ""
    if _is_cpp_module or _is_hls_module:
        extra_guides += CPP_ANALYSIS_GUIDE
    if _is_hls_module:
        extra_guides += HLS_EXTRA_GUIDE

    return USER_PROMPT.format(
        module_name=module_name,
        formatted_core_component_codes=core_component_codes,
        module_tree=formatted_module_tree,
    ) + dependency_section + extra_guides



def format_cluster_prompt(
    potential_core_components: str,
    module_tree: dict[str, any] = {},
    module_name: str = None,
    graph_clusters_hint: str = "",
) -> str:
    """
    Format the cluster prompt with potential core components, module tree,
    and optional graph-based pre-clustering hints.
    """

    # format module tree — only show component lists for the current module
    lines = []

    def _format_module_tree(module_tree: dict[str, any], indent: int = 0):
        for key, value in module_tree.items():
            is_current = key == module_name
            if is_current:
                lines.append(f"{'  ' * indent}{key} (current module)")
                lines.append(f"{'  ' * (indent + 1)} Core components: {', '.join(value['components'])}")
            else:
                lines.append(f"{'  ' * indent}{key}")
            if ("children" in value) and isinstance(value["children"], dict) and len(value["children"]) > 0:
                lines.append(f"{'  ' * (indent + 1)} Children:")
                _format_module_tree(value["children"], indent + 2)

    _format_module_tree(module_tree, 0)
    formatted_module_tree = "\n".join(lines)

    # Build graph clusters section
    if graph_clusters_hint:
        graph_section = (
            "\n<GRAPH_BASED_CLUSTERS>\n"
            "The following clusters were pre-computed by analyzing actual call/dependency "
            "relationships between components using community detection (Louvain algorithm). "
            "Use these as a strong starting point — components within the same cluster call "
            "each other or live in the same file:\n"
            f"{graph_clusters_hint}\n"
            "</GRAPH_BASED_CLUSTERS>\n"
        )
    else:
        graph_section = ""

    if module_tree == {}:
        return CLUSTER_REPO_PROMPT.format(
            potential_core_components=potential_core_components,
            graph_clusters_section=graph_section,
        )
    else:
        return CLUSTER_MODULE_PROMPT.format(
            potential_core_components=potential_core_components,
            module_tree=formatted_module_tree,
            module_name=module_name,
            graph_clusters_section=graph_section,
        )


LANGUAGE_NAMES = {
    "en": "English",
    "zh": "Chinese (Simplified)",
    "zh-tw": "Chinese (Traditional)",
    "ja": "Japanese",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "pt": "Portuguese",
    "ru": "Russian",
    "ar": "Arabic",
}


def _build_language_section(output_language: str) -> str:
    """Return a language instruction section, or empty string for English."""
    if not output_language or output_language.lower() == "en":
        return ""
    lang_name = LANGUAGE_NAMES.get(output_language.lower(), output_language)
    return (
        f"\n\n<OUTPUT_LANGUAGE>\n"
        f"Write ALL documentation content in {lang_name}. "
        f"Keep code snippets, file names, identifiers, and technical keywords in their original language.\n"
        f"</OUTPUT_LANGUAGE>"
    )


def format_system_prompt(module_name: str, custom_instructions: str = None, output_language: str = "en") -> str:
    """
    Format the system prompt with module name and optional custom instructions.

    Args:
        module_name: Name of the module to document
        custom_instructions: Optional custom instructions to append
        output_language: Language code for generated documentation (e.g. "en", "zh")

    Returns:
        Formatted system prompt string
    """
    custom_section = ""
    if custom_instructions:
        custom_section = f"\n\n<CUSTOM_INSTRUCTIONS>\n{custom_instructions}\n</CUSTOM_INSTRUCTIONS>"
    custom_section += _build_language_section(output_language)

    return SYSTEM_PROMPT.format(module_name=module_name, custom_instructions=custom_section).strip()


def format_leaf_system_prompt(module_name: str, custom_instructions: str = None, output_language: str = "en") -> str:
    """
    Format the leaf system prompt with module name and optional custom instructions.

    Args:
        module_name: Name of the module to document
        custom_instructions: Optional custom instructions to append
        output_language: Language code for generated documentation (e.g. "en", "zh")

    Returns:
        Formatted leaf system prompt string
    """
    custom_section = ""
    if custom_instructions:
        custom_section = f"\n\n<CUSTOM_INSTRUCTIONS>\n{custom_instructions}\n</CUSTOM_INSTRUCTIONS>"
    custom_section += _build_language_section(output_language)

    return LEAF_SYSTEM_PROMPT.format(module_name=module_name, custom_instructions=custom_section).strip()


def format_overview_prompt(name: str, repo_structure: str, is_repo: bool = True, output_language: str = "en") -> str:
    """
    Format the overview prompt for repo or module with optional language instruction.

    Args:
        name: Repository or module name
        repo_structure: JSON-formatted structure string
        is_repo: True for repo-level overview, False for module-level
        output_language: Language code for generated documentation

    Returns:
        Formatted prompt string
    """
    lang_instruction = ""
    if output_language and output_language.lower() != "en":
        lang_name = LANGUAGE_NAMES.get(output_language.lower(), output_language)
        lang_instruction = f"\nIMPORTANT: Write the overview content in {lang_name}. Keep code, file names, and identifiers in their original language.\n"

    if is_repo:
        prompt = REPO_OVERVIEW_PROMPT.format(repo_name=name, repo_structure=repo_structure)
    else:
        prompt = MODULE_OVERVIEW_PROMPT.format(module_name=name, repo_structure=repo_structure)

    if lang_instruction:
        prompt = prompt + lang_instruction

    return prompt
