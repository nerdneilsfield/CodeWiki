# CodeWiki

**Turn any codebase into structured, navigable documentation — powered by static analysis and LLM agents.**

CodeWiki parses your repository with language-aware analyzers (AST + tree-sitter), builds a symbol table and dependency graph, clusters related components into modules, then dispatches LLM agents to write documentation grounded in code evidence. The output is a set of interconnected Markdown files with architecture diagrams, API references, and cross-module links.

[Chinese Documentation (中文文档)](README_ZH.md) | [Paper](https://arxiv.org/abs/2510.24428)

---

## What It Does

1. **Analyzes** your code across 9 languages (Python, TypeScript, JavaScript, Java, C, C++, C#, Go, Rust)
2. **Indexes** symbols, imports, calls, and inheritance relationships with confidence levels and source evidence
3. **Clusters** components into logical modules using graph algorithms (directory priors, SCC contraction, Louvain community detection), then names them with a constrained LLM call
4. **Generates** module documentation with evidence-driven prompts — every behavioral assertion cites a symbol ID or source location
5. **Validates** output with link checking, heading anchor consistency, Mermaid/Math lint, and configurable strict gates

## Quick Start

### Install

```bash
pip install git+https://github.com/nerdneilsfield/CodeWiki.git
```

Requires Python 3.12+ and Node.js 14+ (for Mermaid validation).

### Configure

```bash
# Set your LLM API credentials
codewiki config set --api-key YOUR_KEY --base-url https://api.anthropic.com/v1

# Set the model
codewiki config set --main-model claude-sonnet-4
```

API keys are stored in your system keychain (macOS Keychain, Windows Credential Manager, Linux Secret Service).

### Generate

```bash
# Generate documentation for a local repository
codewiki generate /path/to/your/repo

# Generate with Chinese output
codewiki generate /path/to/repo --language zh

# Generate with GitHub Pages viewer
codewiki generate /path/to/repo --github-pages
```

<details>
<summary><strong>Full CLI options</strong></summary>

```
codewiki generate [REPO_PATH] [OPTIONS]

Output:
  --output DIR              Output directory (default: ./docs)
  --create-branch           Create a git branch for generated docs
  --github-pages            Generate index.html viewer
  --static                  Pre-render standalone HTML pages
  --no-cache                Force full regeneration

Language:
  --language CODE           Output language: en, zh, zh-tw, ja, ko, fr, de, es

Model:
  --main-model NAME         Primary LLM model
  --cluster-model NAME      Model for module naming
  --long-context-model NAME Model for oversized prompts
  --long-context-threshold N Token threshold for long-context switch

Limits:
  --max-tokens N            Max response tokens (default: 32768)
  --max-depth N             Hierarchical decomposition depth (default: 2)
  --max-concurrent N        Parallel module workers (default: 3)
  --max-retries N           Fill-pass retries (default: 2)

Filtering:
  --include PATTERNS        Comma-separated file patterns to include
  --exclude PATTERNS        Comma-separated file patterns to exclude
  --focus MODULES           Modules to document in detail

Customization:
  --doc-type TYPE           api, architecture, user-guide, developer
  --instructions TEXT       Custom agent instructions
  --verbose                 Show detailed progress
```

</details>

### Output

```
docs/
├── overview.md              # Start here — repo architecture and entry points
├── module_name.md           # Per-module documentation
├── module_tree.json         # Hierarchical module structure
├── metadata.json            # Generation metadata
├── _lint_report.json        # Post-processing lint results
└── index.html               # Interactive viewer (with --github-pages)
```

---

## Architecture

CodeWiki processes a repository through five layers, each building on the previous:

```
Source Code
    │
    ▼
┌─────────────────────┐
│ 1. Index Layer       │  AST + tree-sitter → SymbolTable, ImportGraph,
│                      │  ComponentCards, IMPORTS/CALLS/EXTENDS edges
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ 2. Graph Layer       │  Typed edges with confidence + evidence_refs,
│                      │  EdgeIndex (callers_of / callees_of / edges_of)
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ 3. Clustering Layer  │  Directory priors → SCC contraction → Louvain →
│                      │  LLM naming → naming freeze → stability metrics
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ 4. Generation Layer  │  Evidence-driven prompts with symbol cards,
│                      │  boundary edges, glossary, link map
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ 5. Post Layer        │  Link validation, heading anchor consistency,
│                      │  Mermaid/Math lint + degradation, LintReport
└─────────────────────┘
```

<details>
<summary><strong>Layer details</strong></summary>

### Index Layer

Parses source files with language-specific adapters:
- **PythonIndexAdapter** — uses `ast` module for classes, methods, imports, signatures (including kwonlyargs), visibility (`__all__` detection), and import path resolution
- **TSJSIndexAdapter** — uses tree-sitter for TypeScript/JavaScript class/function/import extraction with relative import resolution (.ts/.tsx/.js/.jsx probing)
- **GenericIndexAdapter** — fallback that converts existing dependency analyzer `Node` objects to `Symbol` format

Products: `SymbolTable` (O(1) lookup by id/file/name/qualified_name), `ImportGraph` (file-level import tracking with resolution), `ComponentCard` (LLM-facing summaries with truncated docstrings and top-5 edges).

### Graph Layer

Builds typed relationship edges from the index:
- **IMPORTS** — from resolved import statements (HIGH confidence)
- **CALLS** — from function/method call sites (confidence varies by resolution quality)
- **EXTENDS** — from class inheritance (parsed from signatures)

Every edge carries `evidence_refs` (file + line range) and `confidence` (HIGH/MEDIUM/LOW). Unresolved references are preserved with `to_unresolved` instead of being discarded.

`EdgeIndex` provides O(1) queries: `callers_of(symbol)`, `callees_of(symbol)`, `edges_of(symbol, type_filter)`, `dependency_subgraph(symbol_set)`.

### Clustering Layer

Replaces LLM-driven grouping with a deterministic graph algorithm pipeline:
1. **Directory priors** — groups components by top-level package directory
2. **SCC contraction** — merges cyclic dependencies into super-nodes (Tarjan)
3. **Louvain community detection** — with directory-prior edge injection (weight 2.0) and fixed seed (42) for determinism
4. **LLM naming** — constrained to title + description only (no member rearrangement); falls back to heuristic naming on failure
5. **Naming freeze** — when module members haven't changed, reuses previous title/path to prevent churn
6. **Stability metrics** — Jaccard similarity, path stability, module ID consistency across runs

Output validated against `ModuleTree` schema with fail-fast on invariant violations (duplicate components, missing assignments, non-unique paths).

### Generation Layer

Injects evidence-rich context into LLM prompts:
- **Context pack** — component-level precise symbol cards, boundary/internal edges, evidence snippets
- **Glossary** — public API terms with docstring definitions, injected as shared context
- **Link map** — stable path-based module cross-references using `module_doc_filename()`
- **Evidence rules** — system prompt block requiring every assertion to cite `symbol_id` or `file:line`

All three prompt paths (system, leaf, overview) receive evidence rules. Recursive sub-module generation inherits `global_assets` via `CodeWikiDeps`.

### Post Layer

Validates and repairs generated documentation:
- **Link validation** — scans internal `[text](file.md#anchor)` links, verifies file existence and heading anchor presence
- **Heading anchors** — `heading_to_slug()` as single source of truth, used by both renderer and validator, with duplicate heading dedup (-1, -2 suffixes)
- **Mermaid degradation** — unfixable diagrams replaced with `text` code blocks + error comments
- **Math degradation** — inline math → backtick code; display math → `latex` fenced block
- **LintReport** — JSON report saved to `_lint_report.json` with all failures
- **Strict gate** — `Config.postprocess_strict = True` raises `LintError` on unfixable issues

</details>

---

## Docker Deployment

<details>
<summary><strong>Docker setup</strong></summary>

```bash
cd docker
cp env.example .env
# Edit .env with your API credentials
docker compose up -d
```

The web interface is available at `http://localhost:8000`.

**Environment variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_API_KEY` | (required) | API key for LLM provider |
| `LLM_BASE_URL` | (required) | API base URL |
| `MAIN_MODEL` | `claude-sonnet-4` | Primary model |
| `FALLBACK_MODEL_1` | `glm-4p5` | Fallback model |
| `CLUSTER_MODEL` | (same as MAIN_MODEL) | Module naming model |
| `APP_PORT` | `8000` | Web interface port |

</details>

---

## Web Interface

Submit a GitHub repository URL through the browser and view generated documentation with:
- Dark/light mode (follows OS preference)
- Collapsible sidebar with module tree navigation
- Auto-generated table of contents per page
- Syntax highlighting (highlight.js)
- Math rendering (KaTeX)
- Mobile-responsive layout

---

## Configuration Reference

<details>
<summary><strong>All configuration options</strong></summary>

```bash
# Models
codewiki config set --main-model claude-sonnet-4
codewiki config set --cluster-model glm-4p5
codewiki config set --long-context-model claude-sonnet-4
codewiki config set --long-context-threshold 200000

# Token limits
codewiki config set --max-tokens 32768
codewiki config set --max-token-per-module 36369
codewiki config set --max-token-per-leaf-module 16000

# Concurrency
codewiki config set --max-concurrent 3
codewiki config set --max-retries 2
codewiki config set --max-depth 2

# Language
codewiki config set --language zh

# Agent instructions
codewiki config agent --include "src/**/*.py" --exclude "tests/**"
codewiki config agent --focus "core,api"
codewiki config agent --doc-type architecture

# View current config
codewiki config show

# Validate config
codewiki config validate
```

**Post-processing:**

Set `postprocess_strict: true` in config to block builds when Mermaid/Math/Link issues cannot be fixed.

</details>

---

## Supported Languages

| Language | Adapter | Extraction |
|----------|---------|------------|
| Python | `ast` module | Classes, methods, imports, signatures, visibility, `__all__` |
| TypeScript | tree-sitter | Classes, methods, functions, imports, exports |
| JavaScript | tree-sitter | Classes, methods, functions, imports |
| Java | tree-sitter | Classes, methods, interfaces |
| C | tree-sitter | Functions, structs, includes |
| C++ | tree-sitter | Classes, functions, namespaces, includes |
| C# | tree-sitter | Classes, methods, interfaces |
| Go | tree-sitter | Functions, structs, interfaces |
| Rust | tree-sitter | Functions, structs, traits, impls |

Python and TypeScript/JavaScript have enhanced adapters with import resolution and call extraction. Other languages use the generic adapter with basic symbol extraction.

---

## Development

```bash
# Clone and install in development mode
git clone https://github.com/nerdneilsfield/CodeWiki.git
cd CodeWiki
pip install -e ".[dev]"

# Run tests (536 tests)
python -m pytest tests/ -q

# Run index/clustering/generation/postprocess tests
python -m pytest tests/test_index_*.py tests/test_clustering_*.py \
    tests/test_generation_*.py tests/test_postprocess_*.py -q
```

---

## Performance

Evaluated on 30 repositories across 6 programming languages (up to 1.4M LOC):

| Category | CodeWiki | DeepWiki |
|----------|----------|----------|
| High-Level (Python, JS, TS) | **79.14%** | 68.67% |
| Managed (C#, Java) | **68.84%** | 64.80% |
| Systems (C, C++) | 53.24% | 56.39% |
| **Overall** | **68.79%** | 64.06% |

---

## Citation

```bibtex
@article{codewiki2025,
  title={Evaluating AI's Ability to Generate Holistic Documentation for Large-Scale Codebases},
  author={...},
  journal={arXiv preprint arXiv:2510.24428},
  year={2025}
}
```

## License

MIT
