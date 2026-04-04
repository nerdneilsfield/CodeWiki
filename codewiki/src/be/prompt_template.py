# ── Shared Mermaid safety rules ───────────────────────────────────────────────
# Embedded verbatim in every <MERMAID_REQUIREMENTS> block and in the base system
# prompts.  Rule rationale: Mermaid's lexer rejects Unicode math operators in
# node/edge labels, causing silent parse errors visible to readers.  Math belongs
# in LaTeX, not in diagrams.
_MERMAID_SAFETY_RULES = """\
MERMAID SYNTAX SAFETY — violations cause parse errors visible to readers:
- Node and edge labels must be plain ASCII (or CJK for CJK-language repos).
  NEVER put Unicode math operators in labels: ∃ ∀ ∈ ∉ ⊂ ⊆ ⊇ ∧ ∨ ∩ ∪ ≡ ≈ ≠ → ⇒ ≤ ≥.
  Use plain-text equivalents: "exists", "forall", "in", "not in", "subset",
  "and", "or", "intersect", "union", "equiv", "approx", "neq", "implies".
  BAD:  E{{∃c: lower(d(c)) = q'?}}   ← ∃ and ' break the Mermaid lexer
  GOOD: E{{exists c: lower d c = q_low?}}
- No single-quote characters (') inside node labels — rewrite as "_low" suffix or omit.
- Math expressions belong in LaTeX blocks ($$...$$), NEVER inside diagram labels.
  A diagram shows flow and structure; LaTeX expresses the math. Keep them separate.\
"""
# ──────────────────────────────────────────────────────────────────────────────

# ── Evidence-driven writing rules (v3.md 6.1) ────────────────────────────────
EVIDENCE_RULES_BLOCK = """
## Evidence-Driven Writing Rules

1. Every behavioral assertion MUST cite evidence:
   - Reference symbol_id for definitions (e.g., "py:src/auth.py#login(function)")
   - Reference file:line for call sites / imports (e.g., "src/handler.py:42")
2. If evidence is insufficient, write "Based on index analysis..." and note
   the limitation explicitly.
3. Example code MUST come from the repository (tests, README, examples);
   if synthesized, mark as "[Synthetic example]".
4. Do NOT invent function signatures, parameter types, or call chains
   not supported by the provided symbol cards and edges.
"""
# ──────────────────────────────────────────────────────────────────────────────

# ── Writing discipline (anti-formulaic writing) ──────────────────────────────
_WRITING_DISCIPLINE = """\
<WRITING_DISCIPLINE>
1. **Vary sentence structure.** No two adjacent paragraphs should open with the same pattern. If one starts with a definition, the next should start with a constraint, a scenario, or a counterpoint.

2. **Avoid these overused words and phrases:**
   - delve, tapestry, realm, paradigm, beacon, testament to, robust, comprehensive, cutting-edge, leverage, pivotal, underscores, meticulous, seamless, game-changer, utilize, holistic, actionable, synergy, interplay
   - 值得注意的是, 需要指出的是, 综上所述, 不难发现, 显而易见, 众所周知, 具有重要的理论意义, 具有广阔的应用前景

3. **No empty openers.** Never start a section with "In today's...", "In the ever-evolving...", "In the rapidly changing landscape of...". Start with the specific problem or a direct statement.

4. **Specific over vague — but only when evidence exists.** Replace vague claims with concrete mechanisms or data IF the source code, benchmarks, or comments provide it. If no evidence is available, describe the concrete mechanism or symptom. Never fabricate metrics.

5. **Connector words: use when they aid clarity, avoid when redundant.** "therefore", "因此", "从而" are fine when they make a non-obvious causal chain explicit. Drop them when the cause-effect is already clear.

6. **Analogies: one per major section, not per paragraph.** One well-chosen analogy is effective. Repeating "think of it as..." for every concept makes the text formulaic. After the first analogy, switch to concrete code-level explanation.

7. **Prose rhythm.** Mix sentence lengths. Follow a long compound sentence with a short declarative one.
</WRITING_DISCIPLINE>"""
# ──────────────────────────────────────────────────────────────────────────────

# ── Shared prompt blocks (used by both SYSTEM_PROMPT and LEAF_SYSTEM_PROMPT) ─
_SHARED_OBJECTIVES = """\
<OBJECTIVES>
Create documentation that gives a developer joining the team a deep, intuitive understanding of:
1. **Why this module exists** — the problem space, design motivation, and the alternatives that were NOT chosen
2. **How it thinks** — the mental model, core abstractions, and architectural patterns at play
3. **How it connects** — dependency chains, data flow paths, and coupling with the rest of the system
4. **How to work with it** — practical usage, configuration, extension points, and pitfalls
</OBJECTIVES>"""

_SHARED_WRITING_APPROACH = """\
<WRITING_APPROACH>
**Explain the "why", not just the "what"**
- Bad:  "`ConnectionPool` manages a pool of database connections."
- Good: "Creating a new TCP connection per query adds measurable latency on every call. `ConnectionPool` amortizes that cost by keeping connections alive between requests."
- Good: "The downstream parser expects a flat token stream, but raw input contains nested delimiters. `Tokenizer.split()` resolves this with a character-level state machine."
- Good: "Without rate limiting, a single misbehaving client can saturate the entire API. `RateLimiter` enforces per-client quotas to prevent this."

**Use analogies sparingly — one per major section, then switch to concrete explanation**

**Analyze dependencies and architecture deeply**
- Trace end-to-end data flow for key operations
- Identify the module's architectural role: gateway, orchestrator, transformer, policy enforcer, cache layer?
- Call out coupling patterns: what does this module assume about its neighbors?

**Surface design tradeoffs and decisions**
- Where you see a non-obvious design choice, explain what was chosen and why it fits
- Note tension points and their consequences
- Highlight extension points vs locked-down boundaries
</WRITING_APPROACH>"""

_SHARED_GROUNDING_RULES = """\
<GROUNDING_RULES>
- ONLY reference function names, class names, and APIs that actually exist in the provided source code
- Do NOT invent or hallucinate function signatures, parameter names, or return types — verify against the code
- Use the dependency graph (depends_on / depended_by) to describe architecture and data flow accurately
- When describing component interactions, cite the actual call relationships from the provided dependency data
- If you are uncertain about implementation details, say so rather than guessing
- **Code examples must be grounded**: every line of a code example must come from actual files in the provided source (test files, example files, main source). If a complete runnable example would require inventing helper functions or glue code that does not exist in the repo, do ONE of the following instead:
  (a) Show only the real call-site with prose explaining context, or
  (b) Clearly mark the whole block as pseudocode with a `// pseudocode` or `# pseudocode` comment on the first line.
</GROUNDING_RULES>"""
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    """
<ROLE>
You are a senior software architect writing a technical book chapter about the `{module_name}` module. Your goal is not merely to catalog code — it is to make a reader **understand** the module the way its original author does: the problems it solves, the ideas behind it, and the tradeoffs that shaped it.
</ROLE>

"""
    + _SHARED_OBJECTIVES
    + "\n\n"
    + _SHARED_WRITING_APPROACH
    + "\n\n"
    + _WRITING_DISCIPLINE
    + """

<DOCUMENTATION_STRUCTURE>
1. **Main Documentation File** (write to the filename assigned by the system in the user prompt):
   - Opening paragraph: a vivid, jargon-light explanation of what this module does and why it matters — a reader should "get it" in 30 seconds
   - Architecture overview: a Mermaid diagram followed by a narrative walkthrough explaining each component's role and the data/control flow between them
   - Key design decisions: what patterns were adopted (and what alternatives exist), with tradeoff analysis
   - Sub-module summaries: for each sub-module, a multi-sentence description of its responsibility and a link to its dedicated page
   - Cross-module dependencies: how this module interacts with the rest of the system, with references to other module docs

2. **Sub-module Documentation** (delegated via tool):
   - Each sub-module gets its own doc file; the filename is assigned by the system
   - Core components explained in narrative prose with analogies, not just bullet lists
   - Key functions/classes: purpose, internal mechanics, parameters, return values, side effects
   - Dependency analysis: what each component calls, what calls it, and the data contracts between them
   - Usage examples, error conditions, edge cases, and operational gotchas

3. **Visual Documentation**:
   - Mermaid diagrams for architecture, dependency graphs, and data flow — include ONLY when they genuinely clarify (max ~10 nodes per diagram)
   - Every diagram must be accompanied by a written explanation — diagrams supplement prose, never replace it
   - **Mermaid syntax safety**: node and edge labels must be plain ASCII text.  Never use Unicode math operators (∃ ∀ ∈ ⊂ ∧ ∨ ≡ …) or single quotes in labels — they break the Mermaid parser.  Use text equivalents ("exists", "in", "subset", …) or move the math into a LaTeX block.
   - Mathematical notation: use LaTeX syntax (`$inline$` / `$$block$$`) ONLY when a formula communicates something prose cannot — e.g., algorithmic complexity (O(n log n)), ML loss functions, probability distributions, or cryptographic properties. Default to plain language; reach for math only when it genuinely adds precision.
</DOCUMENTATION_STRUCTURE>

"""
    + _SHARED_GROUNDING_RULES
    + """

<WORKFLOW>
1. Analyze the provided code components, dependency graph, and module structure; explore additional dependencies if needed
2. Create the module doc file using the exact assigned filename from the user prompt, with overview, architecture narrative, design decisions, and sub-module summaries
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
)

LEAF_SYSTEM_PROMPT = (
    """
<ROLE>
You are a senior software architect writing a focused technical deep-dive on the `{module_name}` module. Your goal is to make a reader truly **understand** this code — not just know what it does, but grasp the reasoning behind it, the patterns it uses, and the tradeoffs embedded in its design.
</ROLE>

"""
    + _SHARED_OBJECTIVES
    + "\n\n"
    + _SHARED_WRITING_APPROACH
    + "\n\n"
    + _WRITING_DISCIPLINE
    + """

<DOCUMENTATION_REQUIREMENTS>
1. **Opening**: A clear paragraph explaining what this module does and why — a reader should grasp the purpose in 30 seconds
2. **Architecture**: A Mermaid diagram (only if it genuinely clarifies, max ~10 nodes) followed by a narrative walkthrough of the component roles and data flow.  Node and edge labels must be plain ASCII — Unicode math operators break the Mermaid parser; use "exists", "in", "subset", etc. instead.  Math belongs in LaTeX, not in diagram labels.
3. **Component deep-dives**: For each important class/function — purpose, internal mechanics, parameters, return values, side effects, with enough context that a newcomer understands the design reasoning
4. **Dependency analysis**: What this module calls (and why), what calls it (and what they expect), and the data contracts in between
5. **Design decisions & tradeoffs**: Key patterns chosen, alternatives that exist, and the tensions in the current approach
6. **Usage & examples**: Code snippets drawn from real files in the repo. If a complete example would require inventing surrounding glue code, prefer a minimal real call-site plus prose, or label the block `// pseudocode` on the first line
7. **Edge cases & gotchas**: Error conditions, behavioral constraints, known limitations
8. **References**: Link to other module docs rather than duplicating their content
9. **Prose over bullets**: Write conceptual explanations in full paragraphs; reserve bullet points for enumerations, not for narratives
10. **Mathematical notation**: Use LaTeX syntax (`$inline$` / `$$block$$`) ONLY when a formula genuinely aids understanding. Default to prose.
</DOCUMENTATION_REQUIREMENTS>

"""
    + _SHARED_GROUNDING_RULES
    + """

<WORKFLOW>
1. Analyze provided code components, dependency graph, and module structure
2. Explore additional dependencies between components if needed
3. Generate the module doc file using the exact assigned filename from the user prompt, with architecture narrative, component deep-dives, dependency analysis, and design tradeoffs
</WORKFLOW>

<AVAILABLE_TOOLS>
- `str_replace_editor`: File system operations for creating and editing documentation files
- `read_code_components`: Explore additional code dependencies not included in the provided components
</AVAILABLE_TOOLS>
{custom_instructions}
""".strip()
)

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
* NOTE: Reference other modules via links based on dependency relationships. All docs are flat in the same folder. Use ONLY the filenames explicitly provided by the system link map/context. Never guess filenames or use `../`.

<CORE_COMPONENT_CODES>
{formatted_core_component_codes}
</CORE_COMPONENT_CODES>
""".strip()

REPO_OVERVIEW_PROMPT = (
    """
You are a senior architect writing the landing page for the `{repo_name}` project wiki. This is the first page a new developer sees.

Your overview must cover ALL of these topics (mandatory), but organize them in whatever order makes the narrative flow best for THIS specific project — do NOT mechanically follow this list order in your output. Weave topics together where they naturally connect:

- **What this project does** — a clear explanation (a reader should grasp the purpose in 30 seconds)
- **Architecture at a glance** — a Mermaid diagram (max 10 nodes, big-picture) showing the most important modules and data/control flow, followed by a narrative walkthrough
- **Key design decisions** — what architectural patterns were chosen and why
- **Module guide** — for each major module, 2-3 sentences explaining its role and a link to its detailed documentation
- **End-to-end workflows** — trace 1-2 critical user journeys through the system

"""
    + _WRITING_DISCIPLINE
    + """

<REPO_STRUCTURE>
{repo_structure}
</REPO_STRUCTURE>

Generate the overview in markdown format:
<OVERVIEW>
overview_content
</OVERVIEW>
""".strip()
)

MODULE_OVERVIEW_PROMPT = (
    """
You are a senior architect writing a summary page for the `{module_name}` module, which contains several sub-modules. Focus on how the sub-modules work together as a system — don't repeat their individual details.

Your overview must cover ALL of these topics (mandatory), but organize them in whatever order produces the best narrative for THIS specific module:

- **Purpose** — what this module group achieves as a whole and why it exists as a unit
- **Architecture** — a Mermaid diagram showing sub-module relationships and data flow, followed by a narrative walkthrough
- **How sub-modules interact** — the key workflows that span multiple sub-modules, with links to their individual docs
- **Design tradeoffs** — what patterns hold this group together, and where the boundaries between sub-modules were drawn (and why)

"""
    + _WRITING_DISCIPLINE
    + """

<REPO_STRUCTURE>
{repo_structure}
</REPO_STRUCTURE>

Generate the overview in markdown format:
<OVERVIEW>
overview_content
</OVERVIEW>
""".strip()
)

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

import re
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

# ── Guide document prompt templates ──────────────────────────────────────────

GETTING_STARTED_PROMPT = (
    """
You are a technical writer creating a **Getting Started** tutorial for the
`{repo_name}` project.  Target reader: a developer who just discovered this
project and wants to run it locally within 15 minutes.

<REQUIREMENTS>
1. **Prerequisites** — runtime version, required tools, API keys / env vars
2. **Installation** — step-by-step with copy-paste shell commands
3. **First Run** — a complete runnable example with expected terminal output
4. **Configuration** — key settings table (name | required? | default | description)
5. **Common Errors** — top 3-5 errors a newcomer will hit, with one-line fixes
6. **Next Steps** — links to Beginner's Guide, Build & Code Org, and MODULE docs

Every step MUST include:
- The exact command to run
- The expected output (or screenshot description)
- The most likely error at that step and how to fix it
</REQUIREMENTS>

"""
    + _WRITING_DISCIPLINE
    + """

<MERMAID_REQUIREMENTS>
- A `flowchart TD` showing the installation pipeline (clone → install deps → configure → run)
- A `sequenceDiagram` showing the first-run interaction between user, CLI, and backend
- SYNTAX SAFETY: node/edge labels must be plain ASCII. Unicode math operators (∃ ∀ ∈ ⊂ ∧ ∨ ≡ …)
  and single quotes (') in labels cause parse errors. Use text equivalents or LaTeX blocks instead.
</MERMAID_REQUIREMENTS>

<REPO_README>
{readme}
</REPO_README>

<SETUP_FILES>
{setup_files}
</SETUP_FILES>

<CLI_ENTRY>
{cli_entry}
</CLI_ENTRY>

<CONFIG_SOURCE>
{config_source}
</CONFIG_SOURCE>

<EXISTING_OVERVIEW>
{overview}
</EXISTING_OVERVIEW>

{relevant_docs}

{language_instruction}

Generate the tutorial in Markdown.  Wrap the output in:
<GUIDE>
content
</GUIDE>
""".strip()
)

BEGINNER_OUTLINE_PROMPT = """
You are planning a multi-chapter beginner's guide for the `{repo_name}` project.
Target reader: a developer who can write basic code but has never seen this
codebase.  They need to build a mental model of what the project does and how
its parts fit together.

Examine the module tree and module documentation summaries below, then produce
a JSON outline of 4-8 chapters.  Each chapter should teach ONE concept and
build on the previous chapters.

Rules:
- Chapters must follow a progressive-disclosure arc: "What is this?" →
  "How is it organized?" → "How does data flow?" → domain-specific deep-dives
- Each chapter lists the 1-3 modules most relevant to its topic
- Prefer fewer, meatier chapters over many thin ones

<MODULE_TREE>
{module_tree}
</MODULE_TREE>

<MODULE_SUMMARIES>
{module_summaries}
</MODULE_SUMMARIES>

Return ONLY valid JSON wrapped in <OUTLINE>...</OUTLINE>:
<OUTLINE>
{{
  "title": "Beginner's Guide to {repo_name}",
  "sections": [
    {{
      "id": "kebab-case-slug",
      "title": "Chapter title in plain language",
      "focus_modules": ["module_name_1", "module_name_2"],
      "summary": "One-sentence description of what the reader will learn"
    }}
  ]
}}
</OUTLINE>
{language_instruction}
""".strip()

BEGINNER_SECTION_PROMPT = (
    """
You are writing chapter {section_number}/{total_sections} of the beginner's
guide for `{repo_name}`:

**Chapter title:** {section_title}
**Learning goal:** {section_summary}

<WRITING_STYLE>
- Use an analogy for the chapter's central concept, but switch to concrete code explanation for the rest. Do not repeat the same "think of it as..." pattern for every concept.
- Compare with well-known projects readers likely know (e.g., "similar to Express.js's Router") where it genuinely helps — not as filler.
- Every technical term MUST be explained in plain language on first use.
- Short paragraphs — one concept per paragraph.
- Heavy Mermaid usage: architecture diagrams, data-flow charts, concept maps.
</WRITING_STYLE>

"""
    + _WRITING_DISCIPLINE
    + """

<MERMAID_REQUIREMENTS>
Every major concept or flow MUST have a companion Mermaid diagram:
- `graph TD` for architecture / component relationships
- `flowchart` for data flow and process steps
- `sequenceDiagram` for request traces and interactions
- `classDiagram` for concept relationship maps (even if not literally classes)
Max ~15 nodes per diagram.  Every diagram must be followed by a prose walkthrough.

SYNTAX SAFETY (violations cause parse errors the reader sees):
- Node and edge labels must be plain ASCII (or CJK for CJK-language output).
  NEVER use Unicode math operators: ∃ ∀ ∈ ∉ ⊂ ⊆ ⊇ ∧ ∨ ∩ ∪ ≡ ≈ ≠ → ⇒ ≤ ≥.
  Use plain-text equivalents: "exists", "forall", "in", "subset", "and", "or".
  BAD:  E{{∃c: lower(d(c)) = q'?}}   ← ∃ and ' break the Mermaid lexer
  GOOD: E{{exists c: lower d c = q_low?}}
- No single-quote characters (') inside node labels.
- Math expressions belong in LaTeX blocks ($$...$$), NOT in diagram labels.
</MERMAID_REQUIREMENTS>

<FULL_OUTLINE>
{outline_json}
</FULL_OUTLINE>

<PREVIOUS_CHAPTER_SUMMARIES>
{carry_forward}
</PREVIOUS_CHAPTER_SUMMARIES>

<RELEVANT_MODULE_DOCS>
{module_docs}
</RELEVANT_MODULE_DOCS>

<RELEVANT_REPO_DOCS>
{repo_docs}
</RELEVANT_REPO_DOCS>

<MODULE_TREE>
{module_tree}
</MODULE_TREE>

{language_instruction}

Generate the chapter in Markdown.  Wrap in:
<GUIDE>
content
</GUIDE>
""".strip()
)

BEGINNER_PARENT_PROMPT = (
    """
You are writing the landing page for the beginner's guide to `{repo_name}`.
This page links to {num_sections} chapters and gives readers a roadmap.

Write a short introduction (2-3 paragraphs) covering:
- Who this guide is for
- What they will learn
- A Mermaid `flowchart LR` showing the chapter progression

Then list each chapter with its title, a 1-2 sentence teaser, and a link.

"""
    + _WRITING_DISCIPLINE
    + """

<CHAPTERS>
{chapters_list}
</CHAPTERS>

{language_instruction}

Generate in Markdown.  Wrap in:
<GUIDE>
content
</GUIDE>
""".strip()
)

BUILD_ANALYSIS_PROMPT = (
    """
You are a senior build engineer writing a **Build & Code Organization** analysis
for the `{repo_name}` project.  Target reader: a developer who wants to
understand how the project is built, how the source tree is organized, and how
dependencies are managed.

<REQUIREMENTS>
1. **Project Directory Structure** — Mermaid `graph TD` of top-level directories
   and their responsibilities
2. **Build / Compilation Pipeline** — full path from source to runnable artifact,
   as a Mermaid `flowchart TD`
3. **Dependency Management** — how external deps are declared, version locking
4. **Multi-Language Collaboration** (if applicable) — how parts in different
   languages interoperate or co-build
5. **Development Workflow** — common dev / test / build commands with examples

Every section must include at least one Mermaid diagram.
</REQUIREMENTS>

"""
    + _WRITING_DISCIPLINE
    + """

{language_specific_guides}

<DIRECTORY_STRUCTURE>
{directory_tree}
</DIRECTORY_STRUCTURE>

<BUILD_FILES>
{build_files}
</BUILD_FILES>

<MODULE_TREE>
{module_tree}
</MODULE_TREE>

<RELEVANT_MODULE_DOCS>
{module_docs}
</RELEVANT_MODULE_DOCS>

{language_instruction}

Generate the document in Markdown.  Wrap in:
<GUIDE>
content
</GUIDE>
""".strip()
)

ALGORITHM_IDENTIFY_PROMPT = """
You are analyzing `{repo_name}` to identify its **core algorithms** — the
non-trivial computational procedures that define the project's unique value.

Examine the component list and dependency graph below.  Identify 2-8 core
algorithms.  Exclude boilerplate, CRUD, and simple utility functions.

An algorithm qualifies as "core" if it:
- Implements a non-trivial computational procedure (sorting, graph traversal,
  ML inference, signal processing, optimization, etc.)
- Is central to the project's purpose (not a library wrapper)
- Has interesting complexity or design tradeoffs worth explaining

<COMPONENTS>
{components_summary}
</COMPONENTS>

<DEPENDENCY_GRAPH>
{dependency_summary}
</DEPENDENCY_GRAPH>

<MODULE_SUMMARIES>
{module_summaries}
</MODULE_SUMMARIES>

Return ONLY valid JSON wrapped in <ALGORITHMS>...</ALGORITHMS>:
<ALGORITHMS>
{{
  "algorithms": [
    {{
      "id": "kebab-case-slug",
      "title": "Algorithm Name",
      "related_components": ["file.py::FunctionName", ...],
      "summary": "One-sentence description"
    }}
  ]
}}
</ALGORITHMS>
{language_instruction}
""".strip()

ALGORITHM_DEEPDIVE_PROMPT = (
    """
You are an algorithm researcher writing a formal deep-dive on the
**{algorithm_title}** algorithm from `{repo_name}`.

<WRITING_STYLE>
- Formal, precise writing — but vary sentence structure between sections
- LaTeX math: `$inline$` and `$$block$$` for complexity, recurrences, constraints
- Pseudocode in ```pseudocode blocks alongside actual implementation
- Compare with classical algorithms or papers where applicable
</WRITING_STYLE>

"""
    + _WRITING_DISCIPLINE
    + """

<STRUCTURE>
1. **Problem Statement** — formal definition of the problem this algorithm solves
2. **Intuition** — why naive approaches fail; the key insight
3. **Formal Definition** — mathematical specification ($$LaTeX$$)
4. **Algorithm** — pseudocode + Mermaid `flowchart` of execution steps
5. **Complexity Analysis** — time and space, best / worst / average case
6. **Implementation Notes** — how the actual code diverges from theory;
   engineering compromises
7. **Comparison** — vs classical implementations or alternative approaches
</STRUCTURE>

<MERMAID_REQUIREMENTS>
- `flowchart` for algorithm execution steps
- `stateDiagram-v2` for state transitions (if applicable)
- `graph` for data structure relationships

CRITICAL SYNTAX SAFETY — this prompt uses heavy LaTeX math; do NOT let it leak
into Mermaid labels:
- Node and edge labels must be plain ASCII (or CJK for CJK-language output).
  NEVER use Unicode math operators: ∃ ∀ ∈ ∉ ⊂ ⊆ ⊇ ∧ ∨ ∩ ∪ ≡ ≈ ≠ → ⇒ ≤ ≥.
  Use plain-text equivalents: "exists", "forall", "in", "not in", "subset",
  "and", "or", "intersect", "union", "equiv", "approx", "neq", "implies".
  BAD:  E{{∃c: lower(d(c)) = q'?}}   ← ∃ and ' cause a Mermaid parse error
  GOOD: E{{exists c: lower d c = q_low?}}
- No single-quote characters (') inside node labels — use "_low" suffix or omit.
- Formal math notation (predicates, set expressions, recurrences) belongs ONLY in
  LaTeX blocks ($$...$$). A diagram shows execution flow; LaTeX shows the math.
  These are separate concerns — never mix them.
</MERMAID_REQUIREMENTS>

<ALGORITHM_SOURCE_CODE>
{source_code}
</ALGORITHM_SOURCE_CODE>

<TEST_FILES>
{test_code}
</TEST_FILES>

<RELATED_MODULE_DOCS>
{module_docs}
</RELATED_MODULE_DOCS>

<DEPENDENCY_GRAPH>
{dependency_edges}
</DEPENDENCY_GRAPH>

{language_instruction}

Generate the deep-dive in Markdown.  Wrap in:
<GUIDE>
content
</GUIDE>
""".strip()
)

ALGORITHM_PARENT_PROMPT = (
    """
You are writing the landing page for the **Core Algorithms** section of
`{repo_name}`.  This page introduces the project's key algorithms, shows how
they relate to each other, and links to individual deep-dives.

Write:
- An introduction (2-3 paragraphs) explaining the project's computational core
- A Mermaid `graph TD` showing algorithm relationships and data flow between them
- For each algorithm: title, one-paragraph summary, link to its page

"""
    + _WRITING_DISCIPLINE
    + """

<ALGORITHMS>
{algorithms_list}
</ALGORITHMS>

{language_instruction}

Generate in Markdown.  Wrap in:
<GUIDE>
content
</GUIDE>
""".strip()
)

OVERVIEW_AUGMENT_PROMPT = """
The following overview was previously generated for the `{repo_name}` project.
New guide documents have been created alongside the existing module documentation.
Your task: insert a "Documentation Guide" or equivalent navigation section near
the top of the overview (after the first introductory paragraph) that introduces
each guide with 1-2 sentences and a Markdown link.

Do NOT remove or significantly alter the existing overview content — only add
the guide navigation section.

<EXISTING_OVERVIEW>
{existing_overview}
</EXISTING_OVERVIEW>

<AVAILABLE_GUIDES>
{guides_list}
</AVAILABLE_GUIDES>

{language_instruction}

Return the full augmented overview wrapped in:
<GUIDE>
content
</GUIDE>
""".strip()


def format_language_instruction(output_language: str) -> str:
    """Return a language instruction string for guide prompts.

    Identical rules to ``_build_language_section()`` so that guide and
    module/overview prompts produce consistent cross-language output.
    """
    if not output_language or output_language.lower() == "en":
        return ""
    lang_name = LANGUAGE_NAMES.get(output_language.lower(), output_language)
    return (
        f"\n<OUTPUT_LANGUAGE>\n"
        f"Write ALL documentation prose in {lang_name}.\n\n"
        f"- Section headings: prefer {lang_name}, but keep well-known English terms "
        f'when they are more recognizable in context (e.g., "API Reference", "CLI Commands")\n'
        f"- Technical terms with no standard translation: keep the English term, "
        f"optionally add a brief parenthetical explanation on first use\n"
        f"- Code identifiers (function names, class names, variable names): always keep as-is in English\n"
        f"- File paths and CLI commands: keep as-is\n"
        f"- Register: technical documentation — not academic thesis style, not casual blog style\n"
        f"- Avoid mid-sentence language switching unless quoting a code identifier\n"
        f"</OUTPUT_LANGUAGE>"
    )


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


def format_user_prompt(
    module_name: str,
    core_component_ids: list[str],
    components: Dict[str, Any],
    module_tree: dict[str, Any],
) -> str:
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

    def _format_module_tree(module_tree: dict[str, Any], indent: int = 0):
        for key, value in module_tree.items():
            is_current = key == module_name
            if is_current:
                lines.append(f"{'  ' * indent}{key} (current module)")
                lines.append(
                    f"{'  ' * (indent + 1)} Core components: {', '.join(value['components'])}"
                )
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
                if node.component_type in ("kernel_instance", "hls_project"):
                    core_component_codes += f"  HLS Kernel: yes (Vitis kernel instance)\n"
                else:
                    core_component_codes += f'  HLS Kernel: yes (extern "C" / Vitis top)\n'
            hls_pragmas = getattr(node, "hls_pragmas", None)
            if hls_pragmas:
                for pragma in hls_pragmas:
                    ptype = getattr(pragma, "pragma_type", "") or pragma.get("pragma_type", "")
                    semantic = (
                        getattr(pragma, "hardware_semantic", "")
                        or pragma.get("hardware_semantic", "")
                        or ptype
                    )
                    params_d = getattr(pragma, "params", {}) or pragma.get("params", {})
                    param_str = (
                        ", ".join(f"{k}={v}" for k, v in params_d.items()) if params_d else ""
                    )
                    core_component_codes += f"  #pragma HLS {ptype}"
                    if param_str:
                        core_component_codes += f" ({param_str})"
                    if semantic and semantic != ptype:
                        core_component_codes += f" → {semantic}"
                    core_component_codes += "\n"
            # Docstring (truncated)
            if node.docstring:
                doc = node.docstring.strip().split("\n")[0][:200]
                core_component_codes += f"  Summary: {doc}\n"
            # Dependencies within this module
            deps_in_module = node.depends_on & module_ids
            if deps_in_module:
                core_component_codes += f"  Depends on: {', '.join(sorted(deps_in_module))}\n"
            # Dependencies on external components
            deps_external = node.depends_on - module_ids
            if deps_external:
                core_component_codes += f"  External deps: {', '.join(sorted(deps_external))}\n"

        ext = "." + path.split(".")[-1] if "." in path else ""
        lang = EXTENSION_TO_LANGUAGE.get(ext, ext.lstrip(".") or "text")
        core_component_codes += f"\n## File Content:\n```{lang}\n"

        # Read content of the file using the first component's file path
        try:
            core_component_codes += file_manager.load_text(
                components[component_ids_in_file[0]].file_path
            )
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

    # ── Real call-site snippets from external callers ─────────────────────────
    # Find components outside this module that depend on it, extract their
    # source_code snippet (the function body), and inject as usage examples.
    _MAX_CALLER_SNIPPETS = 4
    _MAX_SNIPPET_CHARS = 1000
    _EXAMPLE_PATH_RE = re.compile(r"(?:test|example|demo|sample|bench)", re.IGNORECASE)

    caller_snippets: list[dict] = []
    for ext_cid, ext_node in components.items():
        if ext_cid in module_ids:
            continue
        called = ext_node.depends_on & module_ids
        if not called:
            continue
        snippet = ext_node.source_code or ""
        if not snippet:
            continue
        truncated = len(snippet) > _MAX_SNIPPET_CHARS
        if truncated:
            snippet = snippet[:_MAX_SNIPPET_CHARS]
        path = ext_node.relative_path or ""
        caller_snippets.append(
            {
                "path": path,
                "name": ext_node.name,
                "calls": sorted(called),
                "snippet": snippet,
                "truncated": truncated,
                "is_test": bool(_EXAMPLE_PATH_RE.search(path)),
            }
        )

    # Prioritise test/example files; keep only the top N
    caller_snippets.sort(key=lambda x: (not x["is_test"], x["path"]))
    caller_snippets = caller_snippets[:_MAX_CALLER_SNIPPETS]

    callers_section = ""
    if caller_snippets:
        callers_section = "\n<REAL_USAGE_EXAMPLES>\n"
        callers_section += (
            "These are actual function bodies from the repository that call this "
            "module's API. Use them as the basis for usage examples — do NOT invent "
            "surrounding code that is not shown here.\n\n"
        )
        for info in caller_snippets:
            ext = "." + info["path"].rsplit(".", 1)[-1] if "." in info["path"] else ""
            lang = EXTENSION_TO_LANGUAGE.get(ext, ext.lstrip(".") or "text")
            truncation_note = "\n_(truncated for brevity)_\n" if info["truncated"] else ""
            callers_section += (
                f"# {info['path']} — `{info['name']}` "
                f"(uses: {', '.join(info['calls'])})\n"
                f"```{lang}\n{info['snippet']}\n```\n"
                f"{truncation_note}\n"
            )
        callers_section += "</REAL_USAGE_EXAMPLES>\n"

    return (
        USER_PROMPT.format(
            module_name=module_name,
            formatted_core_component_codes=core_component_codes,
            module_tree=formatted_module_tree,
        )
        + dependency_section
        + extra_guides
        + callers_section
    )


def format_cluster_prompt(
    potential_core_components: str,
    module_tree: dict[str, Any] | None = None,
    module_name: str | None = None,
    graph_clusters_hint: str = "",
) -> str:
    """
    Format the cluster prompt with potential core components, module tree,
    and optional graph-based pre-clustering hints.
    """

    # format module tree — only show component lists for the current module
    lines = []

    def _format_module_tree(module_tree: dict[str, Any], indent: int = 0):
        for key, value in module_tree.items():
            is_current = key == module_name
            if is_current:
                lines.append(f"{'  ' * indent}{key} (current module)")
                lines.append(
                    f"{'  ' * (indent + 1)} Core components: {', '.join(value['components'])}"
                )
            else:
                lines.append(f"{'  ' * indent}{key}")
            if (
                ("children" in value)
                and isinstance(value["children"], dict)
                and len(value["children"]) > 0
            ):
                lines.append(f"{'  ' * (indent + 1)} Children:")
                _format_module_tree(value["children"], indent + 2)

    module_tree = module_tree or {}
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
        f"Write ALL documentation prose in {lang_name}.\n\n"
        f"- Section headings: prefer {lang_name}, but keep well-known English terms "
        f'when they are more recognizable in context (e.g., "API Reference", "CLI Commands")\n'
        f"- Technical terms with no standard translation: keep the English term, "
        f"optionally add a brief parenthetical explanation on first use\n"
        f"- Code identifiers (function names, class names, variable names): always keep as-is in English\n"
        f"- File paths and CLI commands: keep as-is\n"
        f"- Register: technical documentation — not academic thesis style, not casual blog style\n"
        f"- Avoid mid-sentence language switching unless quoting a code identifier\n"
        f"</OUTPUT_LANGUAGE>"
    )


def format_system_prompt(
    module_name: str, custom_instructions: str | None = None, output_language: str = "en"
) -> str:
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

    result = SYSTEM_PROMPT.format(
        module_name=module_name, custom_instructions=custom_section
    ).strip()
    return result + EVIDENCE_RULES_BLOCK


def format_leaf_system_prompt(
    module_name: str, custom_instructions: str | None = None, output_language: str = "en"
) -> str:
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

    result = LEAF_SYSTEM_PROMPT.format(
        module_name=module_name, custom_instructions=custom_section
    ).strip()
    return result + EVIDENCE_RULES_BLOCK


def format_overview_prompt(
    name: str, repo_structure: str, is_repo: bool = True, output_language: str = "en"
) -> str:
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
    if is_repo:
        prompt = REPO_OVERVIEW_PROMPT.format(repo_name=name, repo_structure=repo_structure)
    else:
        prompt = MODULE_OVERVIEW_PROMPT.format(module_name=name, repo_structure=repo_structure)

    lang_section = _build_language_section(output_language)
    if lang_section:
        prompt = f"{lang_section}\n\n{prompt}"

    return prompt + EVIDENCE_RULES_BLOCK
