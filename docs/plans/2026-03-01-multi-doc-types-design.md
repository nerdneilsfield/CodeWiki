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
Overview                          ← existing, regenerated to reference new pages
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
└── agent_orchestrator.py        ← EXISTING: reused for LLM calls
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

        # 2. Generate guides in order
        await self.generate_getting_started()
        await self.generate_beginner_guide()
        await self.generate_build_analysis()
        await self.generate_algorithm_deepdive()

        # 3. Regenerate overview to reference new pages
        await self._regenerate_overview()

        # 4. Persist cache
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

# Cache structure:
{
    "getting_started": {
        "input_hash": "abc123...",    # combined hash of all input files
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

Input hash includes: relevant repo docs + relevant generated module docs + components
hash + module_tree hash. Any change triggers regeneration.

```python
def _should_regenerate(self, guide_type: str, input_files: List[str]) -> bool:
    current_hash = self._compute_combined_hash(input_files)
    cached = self.cache.get(guide_type, {})
    if cached.get("input_hash") == current_hash:
        return not all(
            os.path.exists(f) and os.path.getsize(f) > 100
            for f in cached["output_files"]
        )
    return True
```

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
- Carry-forward summaries from previous sections

**Execution — three phases:**

1. **Phase A — Outline generation:** LLM sees full module_tree + module summaries →
   outputs JSON outline:
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
- Detected languages from `module_tree.summary.languages_found`
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
   module summaries → identifies N core algorithms:
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
├── overview.md                          ← regenerated
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
