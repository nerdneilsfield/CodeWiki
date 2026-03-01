# Multi-Document-Type Generation Design

> Date: 2026-03-01
> Status: Approved

## 1. Motivation

CodeWiki currently generates one type of documentation: **MODULE docs** — deep technical
deep-dives aimed at senior engineers. This design adds four new documentation types that
run as an automatic post-processing phase after MODULE docs are complete, targeting
different audiences and purposes.

## 2. Final Navigation Structure

```
Overview                          ← existing, augmented with guide entry links
Get Started                       ← NEW single page
Beginner's Guide                  ← NEW parent page
  ├── Part 1: ...                 ← NEW sub-pages (LLM-planned)
  ├── Part 2: ...
  └── Part N: ...
Build & Code Organization         ← NEW single page (all detected languages)
Core Algorithms                   ← NEW parent page
  ├── Algorithm A: ...            ← NEW sub-pages (LLM-identified)
  ├── Algorithm B: ...
  └── Algorithm N: ...
MODULE docs...                    ← existing
```

## 3. Architecture

### 3.1 New Files

```
codewiki/src/be/
├── guide_generator.py           ← NEW: orchestrates all 4 guide types
├── repo_docs_collector.py       ← NEW: 3-layer doc collection + relevance selection
├── prompt_template.py           ← MODIFIED: add 4 sets of prompt constants
├── documentation_generator.py   ← MODIFIED: call GuideGenerator.run() after MODULE docs
└── llm_services.py              ← EXISTING: call_llm for guide LLM calls (no agent tools needed)
```

### 3.2 Pipeline Integration

```python
# documentation_generator.py — in run()
async def run(self):
    # ... existing pipeline ...
    working_dir = await self.generate_module_documentation(components, leaf_nodes)

    # NEW: generate guides after MODULE docs are complete
    guide_gen = GuideGenerator(
        config=self.config,
        components=components,
        module_tree=module_tree,
        working_dir=working_dir,
    )
    await guide_gen.run()
```

### 3.3 GuideGenerator Class

```python
class GuideGenerator:
    """Orchestrates generation of all guide document types."""

    def __init__(self, config, components, module_tree, working_dir):
        self.config = config
        self.components = components
        self.module_tree = module_tree
        self.working_dir = working_dir
        self.collector = RepoDocsCollector()
        self.cache = self._load_cache()

    async def run(self):
        # 1. Collect all available documentation context
        self.docs_bundle = self.collector.collect(
            self.config.repo_path, self.working_dir, self.components
        )

        # 2. Generate guides (phased concurrency, warn-and-continue)
        #    Phase 1: getting_started + build_analysis (parallel)
        #    Phase 2: beginner_guide (serial sections)
        #    Phase 3: algorithm_deepdive (parallel deep-dives)
        ...  # see §4.5 for concurrency details

        # 3. Regenerate overview with guide awareness
        await self._regenerate_overview()

        # 4. Print generation report + persist cache
        self._report_results()
        self._save_cache()
```

## 4. Three-Layer Document Context

```
┌─────────────────────────────────────────────────┐
│             GuideGenerator Context               │
├─────────────────────────────────────────────────┤
│ Layer 1: Repository Original Docs               │
│   README.md, docs/*.md, .rst, .txt              │
│   requirements.txt, pyproject.toml, setup.py    │
│   package.json, Cargo.toml, go.mod, pom.xml     │
│   Makefile, CMakeLists.txt, *.tcl, *.cfg        │
│   Dockerfile, CI/CD configs                     │
├─────────────────────────────────────────────────┤
│ Layer 2: Code Analysis Results (existing)       │
│   components (AST nodes + docstrings)           │
│   dependency graph (calls/called-by edges)      │
│   module_tree (clustering result)               │
├─────────────────────────────────────────────────┤
│ Layer 3: Generated MODULE Docs                  │
│   working_dir/*.md (just-generated module docs) │
│   overview.md (generated overview)              │
│   → select_relevant() picks by topic            │
└─────────────────────────────────────────────────┘
```

### 4.1 RepoDocsCollector

```python
class RepoDocsCollector:
    """Three-layer document collector: repo docs + code analysis + generated docs."""

    def collect(self, repo_path, working_dir, components) -> DocsBundle:
        """Scan and index all three documentation layers."""
        # Layer 1: Scan repo for .md/.rst/.txt (exclude node_modules, .git, __pycache__)
        # Layer 2: Extract docstrings from components
        # Layer 3: Read working_dir/*.md (generated MODULE docs)
        # Build unified index: path + content summary + token estimate

    def select_relevant(self, topic: str, max_tokens: int) -> List[DocSnippet]:
        """Select most relevant doc snippets for a given topic."""
        # Keyword matching + path association
        # Priority: generated module docs > repo docs > docstrings
        # Truncate to max_tokens
```

### 4.2 Hash-Based Cache

```python
GUIDE_CACHE_FILENAME = "_guide_cache.json"

# Cache structure (output_files stores RELATIVE filenames, not absolute paths,
# so the cache remains valid across directory moves and CI environments):
{
    "getting_started": {
        "input_hash": "abc123...",    # combined hash of all input files + _PROMPT_VERSION
        "output_files": ["getting-started.md"]
    },
    "beginner_guide": {
        "input_hash": "def456...",
        "output_files": ["beginners-guide.md", "beginners-guide-part1.md", ...]
    },
    "build_analysis": {
        "input_hash": "ghi789...",
        "output_files": ["build-and-organization.md"]
    },
    "algorithm_deepdive": {
        "input_hash": "jkl012...",
        "output_files": ["core-algorithms.md", "core-algorithms-louvain.md", ...]
    }
}
```

Input hash includes per guide type:

| Guide Type | Hash Inputs |
|---|---|
| **getting_started** | README, setup files, overview.md, prompt version |
| **beginner_guide** | All generated module docs, module_tree hash, prompt version |
| **build_analysis** | Build/config files, component source files, prompt version |
| **algorithm_deepdive** | All component source files, test files, prompt version |

A `_PROMPT_VERSION` constant (bumped on prompt edits) is mixed into every hash
so that prompt changes force regeneration even if source data is unchanged.

```python
_PROMPT_VERSION = "v1"   # bump on any prompt template change

def _should_regenerate(self, guide_type: str, input_files: List[str]) -> bool:
    current_hash = self._compute_combined_hash(input_files, extra=_PROMPT_VERSION)
    cached = self.cache.get(guide_type, {})
    if cached.get("input_hash") == current_hash:
        return not all(
            os.path.exists(f) and os.path.getsize(f) > 100
            for f in cached["output_files"]
        )
    return True
```

### 4.3 Overview Augmentation

After all guides are generated, the existing `overview.md` is augmented with a
guide navigation section.  This does NOT reuse `generate_parent_module_docs()`
(which knows nothing about guide pages).  Instead, `_regenerate_overview()` uses
a dedicated prompt:

```python
OVERVIEW_AUGMENT_PROMPT = """
The following overview was previously generated for {repo_name}.  New guide
documents have been created.  Insert a "Documentation Guide" section near the
top that introduces each guide with 1-2 sentences and a link.

<EXISTING_OVERVIEW>
{existing_overview}
</EXISTING_OVERVIEW>

<AVAILABLE_GUIDES>
{guides_list}
</AVAILABLE_GUIDES>

Return the full augmented overview wrapped in <OVERVIEW>...</OVERVIEW>.
"""
```

Input:
- The current `overview.md` content (read from disk)
- A list of successfully generated guide files with titles and summaries

This approach avoids the fragile `DocumentationGenerator.__new__()` anti-pattern
and ensures the LLM has explicit context about the guide pages it needs to link.

### 4.4 Slug Sanitization (Path Safety)

LLM-generated `id` fields (beginner section slugs, algorithm slugs) are used in
filenames.  To prevent path traversal attacks:

```python
import re
from codewiki.src.be.dependency_analyzer.utils.security import assert_safe_path

def _sanitize_slug(raw: str, index: int = 0) -> str:
    """Sanitize an LLM-generated slug to [a-z0-9-] only.

    If the slug becomes empty after sanitization (e.g. pure Chinese title),
    falls back to "part-{index}" to avoid filename collisions.
    """
    slug = re.sub(r'[^a-z0-9-]', '', raw.lower().strip())
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug or f"part-{index}"
```

Every filename constructed from an LLM slug MUST:
1. Pass through `_sanitize_slug(raw, index=i)` with the loop index
2. Be validated with `assert_safe_path(working_dir, output_path)`

### 4.5 JSON Schema Validation

LLM JSON outputs (beginner guide outline, algorithm identification) are validated
with pydantic models.  On validation failure, log a warning and use a fallback
empty list rather than crashing.

```python
from pydantic import BaseModel, Field

class OutlineSection(BaseModel):
    id: str
    title: str
    focus_modules: list[str] = Field(default_factory=list)
    summary: str = ""

class OutlineSchema(BaseModel):
    title: str = ""
    sections: list[OutlineSection] = Field(default_factory=list)

class AlgorithmEntry(BaseModel):
    id: str
    title: str
    related_components: list[str] = Field(default_factory=list)
    summary: str = ""

class AlgorithmListSchema(BaseModel):
    algorithms: list[AlgorithmEntry] = Field(default_factory=list)
```

### 4.6 Concurrency

Guide generation uses the same concurrency primitives as the MODULE doc pipeline
(`asyncio.Semaphore` bounded by `config.max_concurrent`).

```
┌─ Phase 1: Independent single-page guides (parallel) ──────────┐
│  getting_started ─┐                                            │
│                   ├─ asyncio.gather (bounded by Semaphore)     │
│  build_analysis  ─┘                                            │
└────────────────────────────────────────────────────────────────┘

┌─ Phase 2: Beginner's Guide ───────────────────────────────────┐
│  Phase A: outline (1 LLM call)                                 │
│  Phase B: sections (serial — carry-forward context dependency) │
│  Phase C: parent page (1 LLM call)                             │
└────────────────────────────────────────────────────────────────┘

┌─ Phase 3: Core Algorithms ────────────────────────────────────┐
│  Phase A: identify (1 LLM call)                                │
│  Phase B: per-algorithm deep-dives (parallel, Semaphore)       │
│  Phase C: parent page (1 LLM call)                             │
└────────────────────────────────────────────────────────────────┘

┌─ Phase 4: Regenerate overview ────────────────────────────────┐
└────────────────────────────────────────────────────────────────┘
```

- **Single-page guides** (getting_started, build_analysis) are independent →
  run concurrently via `asyncio.gather`
- **Algorithm deep-dives** are independent per algorithm → run concurrently
  via `asyncio.gather` bounded by `asyncio.Semaphore(config.max_concurrent)`
- **Beginner sections** must remain serial (each section's carry-forward summary
  feeds the next section's prompt)

### 4.7 Failure Mode & Generation Report

Individual guide generation failures do NOT abort the pipeline.  Each guide runs
inside a try/except that logs a warning and continues to the next guide.  This
ensures one bad LLM response doesn't prevent the other guides from generating.

At the end of `run()`, a summary report is logged listing each guide's status:

```
📖 Guide generation report:
  ✓ Getting Started       → getting-started.md
  ✓ Beginner's Guide      → beginners-guide.md (4 sections)
  ✗ Build & Code Org      → SKIPPED (LLM call failed: timeout)
  ✓ Core Algorithms       → core-algorithms.md (3 deep-dives)
```

This ensures skipped/failed guides are immediately visible to the user, not
silently lost in log noise.

### 4.8 LLM Calling Convention

Guides use `call_llm()` (from `llm_services.py`) wrapped in a
`_call_llm_with_fallback()` method, NOT the `agent_orchestrator`.  Guides are
single-prompt tasks with no tool use — the agent framework's tool loop adds
unnecessary overhead.  This is intentional, not a design/impl mismatch.

The wrapper mirrors the agent framework's full resilience chain:

```python
async def _call_llm_with_fallback(self, prompt: str) -> str:
    """Call LLM with: long-context pre-select → retry → fallback chain."""
    # 1. Pre-select: if prompt exceeds threshold → long-context model directly
    #    (matches select_agent_model() pattern, avoids wasted retries)
    # 2. Otherwise try models in order: main → fallback(s) → long_context
    #    (matches create_fallback_models() chain)
    # 3. Each call_llm() has its own 4-retry loop (10s/30s/90s backoff)
```

This provides the same resilience guarantees as the agent framework:
- **Retry**: 4 attempts per model with exponential backoff (via `call_llm()`)
- **Fallback**: main_model → fallback_model(s) → long_context_model
- **Long-context**: auto-switch when prompt exceeds `long_context_threshold`

## 5. Guide Type Details

### 5.1 Get Started (Single Page)

**Input context:**
- README + package management files (requirements.txt / pyproject.toml / package.json / Cargo.toml / ...)
- CLI entry file content + Config class source
- Generated overview.md

**Execution:** Single LLM call → `getting-started.md`

**Prompt requirements:**
1. Prerequisites — runtime, tools, API keys
2. Installation — step-by-step with executable commands
3. First Run — complete example with expected output
4. Configuration — key settings (required vs optional, defaults)
5. Common Errors — top 3-5 errors with fixes
6. Next Steps — links to other doc pages

Each step must include: command to run, expected output, possible errors and fixes.

**Required Mermaid:** `flowchart` for installation flow, `sequenceDiagram` for first-run
interaction.

### 5.2 Beginner's Guide (Multi-Page, Outline-First)

**Input context:**
- module_tree structure
- All generated module docs (first 500 chars as summaries; full text for focus modules)
- README + repo existing docs
- Carry-forward summaries from previous sections (paragraph-boundary truncation)

**Execution — three phases:**

1. **Phase A — Outline generation:** LLM sees full module_tree + module summaries →
   outputs JSON outline, validated with `OutlineSchema` (§4.4).  Section `id`
   values are sanitized with `_sanitize_slug()` (§4.3):
   ```json
   {
     "title": "Beginner's Guide to {repo_name}",
     "sections": [
       {
         "id": "what-is-this",
         "title": "这个项目是做什么的？",
         "focus_modules": ["overview"],
         "summary": "..."
       },
       ...
     ]
   }
   ```

2. **Phase B — Serial section generation:** Generate each section sequentially. Each
   section's prompt includes:
   - Full outline (global coherence)
   - Previous sections' summaries (carry-forward context)
   - Full text of focus_modules' generated docs
   - `select_relevant()` repo docs

3. **Phase C — Parent page:** Generate `beginners-guide.md` with section links and intro.

**Writing style requirements:**
- Everyday analogies for every technical concept
  - Good: "模块就像乐高积木——每块有自己的形状和功能，组合起来才能搭出完整的城堡"
  - Bad: "模块是封装了相关函数和类的命名空间"
- Compare with well-known projects (e.g. "这个模块的作用类似于 Express.js 中的 Router")
- Every technical term explained in plain language on first use
- "想象一下……"、"你可以把它理解为……" to introduce new concepts
- Short paragraphs, one concept per paragraph
- Heavy Mermaid usage: architecture diagrams, data flow, concept relationship maps

**Required Mermaid per section:** `graph TD` (architecture), `flowchart` (data flow),
`classDiagram` (concept relationships), `sequenceDiagram` (end-to-end traces).

### 5.3 Build & Code Organization (Single Page, Multi-Language Adaptive)

**Input context:**
- Detected languages via file extension inference from `components` (always
  available; `module_tree.summary.languages_found` may not be populated)
- Build/config files for each detected language
- Directory structure
- module_tree + relevant module docs

**Execution:** Single LLM call with dynamically-injected language-specific guides →
`build-and-organization.md`

**Prompt requirements:**
1. Project Directory Structure — Mermaid graph of top-level directory relationships
2. Build/Compilation Pipeline — full path from source to runnable artifact
3. Dependency Management — how external deps are declared, version locking
4. Multi-Language Collaboration (if applicable) — how different language parts interoperate
5. Development Workflow — common dev/test/build commands

**Language-specific guide injection** (dynamically composed based on detected languages):

| Detected Language | Guide Content |
|---|---|
| Python | pyproject.toml/setup.py entry points, dependency groups, `__init__.py` structure, virtualenv |
| JavaScript/TS | package.json scripts, deps vs devDeps, bundler config, monorepo |
| Java | pom.xml/build.gradle, module structure, build lifecycle |
| Go | go.mod, package conventions, build tags |
| Rust | Cargo.toml, workspace structure, feature flags |
| C/C++ | CMakeLists.txt targets, Makefile rules, compilation flags |
| HLS | .tcl scripts, .cfg kernel config, pragma topology |
| Generic | Dockerfile, docker-compose, CI/CD configs |

**Required Mermaid:** `flowchart` (build pipeline), `graph TD` (directory structure),
`flowchart` (dependency chain).

### 5.4 Core Algorithms (Multi-Page, Outline-First + Formal)

**Input context:**
- All components + dependency graph
- Module doc summaries
- Algorithm source code (full)
- Related test files
- Related repo docs

**Execution — three phases:**

1. **Phase A — Algorithm identification:** LLM sees components + dependency graph +
   module summaries → identifies N core algorithms, validated with
   `AlgorithmListSchema` (§4.4).  Algorithm `id` values are sanitized with
   `_sanitize_slug()` (§4.3):
   ```json
   {
     "algorithms": [
       {
         "id": "louvain-clustering",
         "title": "Louvain Community Detection",
         "related_components": ["cluster_modules.py::louvain_communities", ...],
         "summary": "..."
       },
       ...
     ]
   }
   ```

2. **Phase B — Per-algorithm generation:** Serial generation, each prompt includes:
   - Full algorithm list (global perspective)
   - Related components' full source code
   - Related test files
   - Related module docs
   - Dependency graph edges for those components

3. **Phase C — Parent page:** `core-algorithms.md` with algorithm list, categories,
   inter-relationships.

**Writing style requirements:**
- Formal academic style
- LaTeX math formulas ($inline$ / $$block$$) for complexity, recurrences, constraints
- Pseudocode blocks (```pseudocode) alongside actual implementation
- Compare with classical algorithms/papers

**Required document structure per algorithm:**
1. **Problem Statement** — formal definition of the problem
2. **Intuition** — why naive approaches fail, core insight
3. **Formal Definition** — mathematical definition, input/output spec ($$LaTeX$$)
4. **Algorithm** — pseudocode + Mermaid flowchart of execution steps
5. **Complexity Analysis** — time/space, best/worst/average
6. **Implementation Notes** — differences from theory, engineering compromises
7. **Comparison** — vs classical implementations or alternatives

**Required Mermaid:** `flowchart` (algorithm steps), `stateDiagram` (state transitions),
`graph` (data structure relationships).

## 6. Context Assembly Per Guide Type

| Guide Type | Layer 1 (Repo Docs) | Layer 2 (Code Analysis) | Layer 3 (Generated Docs) |
|---|---|---|---|
| **Get Started** | README, requirements, setup files | CLI entry components, Config | overview.md |
| **Beginner's Guide** | README | module_tree structure | **All module doc summaries** + focus module full text |
| **Build & Code Org** | All build/config files for detected languages | Build-related components + dep graph | Relevant module docs |
| **Core Algorithms** | Relevant .md | Algorithm components + dep graph + test files | **Related module docs full text** |

## 7. Mermaid Strategy (Global)

All guide types share these Mermaid rules:
- Every major concept or process **must** have a companion Mermaid diagram
- Max ~15 nodes per diagram (readable)
- Every diagram followed by narrative explanation
- Prefer `flowchart` / `sequenceDiagram`; use `graph TD` for complex architecture
- Use `classDiagram` for concept relationships, `stateDiagram` for state machines

## 8. Static Site Integration

The static site generator (`cli/static_generator.py`) needs to be updated to:
1. Recognize guide pages by filename prefix (e.g. `getting-started`, `beginners-guide-*`,
   `build-and-organization`, `core-algorithms-*`)
2. Place them in the correct navigation order: Overview → Get Started → Beginner's Guide
   (with sub-pages) → Build & Code Org → Core Algorithms (with sub-pages) → MODULE docs
3. Generate proper navigation hierarchy for multi-page guides

## 9. Output Files

```
output/docs/{repo}-docs/
├── overview.md                          ← augmented with guide links
├── getting-started.md                   ← NEW
├── beginners-guide.md                   ← NEW parent
├── beginners-guide-{section-id}.md      ← NEW sub-pages
├── build-and-organization.md            ← NEW
├── core-algorithms.md                   ← NEW parent
├── core-algorithms-{algorithm-id}.md    ← NEW sub-pages
├── _guide_cache.json                    ← NEW cache
├── module-a.md                          ← existing
├── module-b.md                          ← existing
└── ...
