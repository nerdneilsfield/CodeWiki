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

Create a TOML config file and fill in your provider credentials:

```bash
codewiki config gen > config.toml
$EDITOR config.toml           # set your API key env vars and models
```

The generated file looks like this (minimal single-provider example):

```toml
[generation]
main_model    = "openai/gpt-4o-mini"
cluster_model = "openai/gpt-4o-mini"

[postprocess]
strict    = false
fix_links = true
# repair_model = "openai/gpt-4o-mini"  # optional: dedicated repair model

[[providers]]
name      = "openai"
type      = "openai_compatible"
base_url  = "https://api.openai.com/v1"
api_keys  = ["env:OPENAI_API_KEY"]
model_list = ["gpt-4o-mini"]
```

Export the referenced env var, then validate:

```bash
export OPENAI_API_KEY=sk-...
codewiki config validate --config config.toml
```

### Generate

```bash
# Generate documentation for current directory
codewiki generate --config config.toml

# Specify a different repository
codewiki generate -C /path/to/repo --config config.toml

# Generate with Chinese output
codewiki generate -C /path/to/repo --config config.toml --language zh

# Generate with static HTML pages (Bulma CSS, works offline)
codewiki generate --config config.toml --static

# Generate with GitHub Pages viewer
codewiki generate --config config.toml --github-pages

# Resume interrupted generation (completed modules are skipped)
codewiki generate --config config.toml

# Write debug logs to file for diagnostics
codewiki generate --config config.toml --log-file codewiki.log
```

<details>
<summary><strong>Full CLI options</strong></summary>

```
codewiki generate [OPTIONS]

Repository:
  -C DIR                    Repository directory (default: current directory)
  --output DIR              Output directory (default: ./docs)
  --create-branch           Create a git branch for generated docs

Output Format:
  --github-pages            Generate index.html viewer
  --static                  Pre-render standalone HTML pages (Bulma CSS)
  --no-repo-links           Omit Repository/DeepWiki links from HTML
  --no-cache                Force full regeneration

Language:
  --language CODE           Output language: en, zh, zh-tw, ja, ko, fr, de, es

Model:
  --main-model NAME         Primary LLM model
  --cluster-model NAME      Model for module naming
  --long-context-model NAME Model for oversized prompts
  --long-context-fallback N Fallback when long-context model fails (comma-separated)
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

Diagnostics:
  --verbose                 Show detailed progress (DEBUG level)
  --log-file PATH           Write DEBUG-level JSON logs to file
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
- **Math validation** — `pylatexenc` structural parsing plus KaTeX render checks, deterministic cleanup rules, then batch LLM repair when needed
- **Mermaid validation** — `mmdc` when available, regex fallback otherwise, deterministic cleanup rules, then batch LLM repair when needed
- **Repair model chain** — `repair_model -> repair_fallback_1 -> repair_fallback_2`, batched by `repair_batch_size`
- **Mermaid degradation** — controlled by `degrade_mermaid` config (default: off). When disabled, unfixable diagrams keep the original mermaid code for browser-side rendering
- **Math degradation** — inline math → backtick code; display math → `latex` fenced block
- **LintReport** — JSON report saved to `_lint_report.json` with all failures
- **Strict gate** — `config.postprocess.strict = true` raises `LintError` on unfixable issues

</details>

---

## Resilience

CodeWiki includes built-in error handling, retry logic, caching, and cancellation support.

**Structured error classification** — LLM SDK exceptions are classified into categories (transient, auth, client error, config error, resource exhausted) so the system can decide whether to retry, fall back to the next model, or fail fast. Non-retryable errors (context length exceeded, invalid config) break out of the retry loop immediately.

**Automatic retry with backoff** — Transient errors (429, 500, 502, 503, timeouts) trigger exponential backoff with jitter. `Retry-After` headers from rate-limited APIs are respected. Auth errors retry once. Retry sleeps respond to cancellation within 1 second.

**Streaming fallback** — For models marked with `stream = true` in config, timeout errors trigger a retry using streaming mode. This helps with providers that have aggressive non-streaming timeouts. Only `openai_compatible` providers support this in the current release.

```toml
# Enable streaming fallback for a specific model
model_list = [
  "gpt-4o-mini",
  {name = "gpt-4.1", stream = true},
]
```

**Graceful Ctrl+C** — First Ctrl+C sets a cancellation token. The pipeline finishes in-flight tasks, persists all state (generation state, module tree), and exits cleanly. Completed modules are preserved for resume. Second Ctrl+C force-quits.

**Multi-layer caching** — Each pipeline stage has its own cache to avoid redundant work on resumed runs:

| Stage | Cache Key | Skip Condition |
|-------|-----------|----------------|
| GraphBuild (AST) | commit + include/exclude patterns | Same commit |
| IndexBuild | commit + INDEX_VERSION | Same commit |
| Clustering | language + max_depth + patterns | Same config |
| Module generation | per-module component hash | Module unchanged |
| Guide generation | input file content + language | Inputs unchanged |
| Postprocess | per-file content hash | File unchanged |

Switching models does **not** invalidate any cache. Only structural changes (code, config, language) trigger reprocessing. `--no-cache` clears all caches for a full rebuild.

**Crash recovery** — Tasks left in `running` state after a crash are automatically reset to `ready` on next load. The pipeline flushes generation state and module tree on every exit path (success, cancel, or failure).

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
- Dark/light mode (Bulma v1 native, follows OS preference + localStorage)
- Collapsible sidebar with module tree navigation
- Table of contents dropdown in the navbar
- Repository and DeepWiki links in the navbar (controllable via `--no-repo-links`)
- Syntax highlighting (Highlight.js) with theme-aware switching
- Math rendering (KaTeX + MathJax fallback, CJK-aware extraction)
- Mermaid diagram rendering with error details
- Mobile-responsive layout with hamburger sidebar toggle
- Job cancellation for in-progress generation

The `--static` flag generates self-contained HTML pages using Bulma CSS (CDN, no JS build tools). These work offline via `file://` protocol and can be deployed to any static hosting.

---

## Configuration Reference

<details>
<summary><strong>All configuration options</strong></summary>

CodeWiki is configured through a single TOML file.  Run `codewiki config init`
to get a commented starter file, then edit it.

```toml
[runtime]
output_dir         = "docs"       # where generated Markdown goes
max_depth          = 2            # module-tree depth
max_concurrent     = 3            # parallel LLM workers
max_retries        = 2            # fill-pass retries for skipped modules
output_language    = "en"         # "en" | "zh" | "ja" | …

[tokens]
max_tokens                = 32768
max_token_per_module      = 36369
max_token_per_leaf_module = 16000
long_context_threshold    = 200000

[generation]
# All model fields use "provider_name/model_name" format.
main_model         = "openai/gpt-4o-mini"
cluster_model      = "openai/gpt-4o-mini"
fallback_models    = ["openai/gpt-4o-mini"]
# long_context_model = "openai/gpt-4o"             # optional: for oversized prompts
# long_context_fallback = "openai/gpt-4o-mini"     # optional: fallback chain if long-context fails

[agent]
# doc_type            = "architecture"   # api | architecture | user-guide | developer
# focus_modules       = ["src/core"]
# custom_instructions = ""

[postprocess]
strict             = false                 # true = block build on unfixable lint issues
fix_links          = true                  # validate and rewrite internal links
degrade_mermaid    = false                 # true = replace unfixable mermaid with text blocks
# repair_model     = "openai/gpt-4o-mini"  # empty = main_model
# repair_fallback_1 = ""
# repair_fallback_2 = ""
# repair_batch_size = 8
# repair_max_retries = 2

# ── Providers ────────────────────────────────────────────────────────────────
# API keys use env: references — the variable is read at generation time.
[[providers]]
name       = "openai"
type       = "openai_compatible"
base_url   = "https://api.openai.com/v1"
api_keys   = ["env:OPENAI_API_KEY"]
model_list = [
  "gpt-4o-mini",                          # plain string: stream defaults to false
  {name = "gpt-4o", stream = true},        # dict form: enables streaming fallback on timeout
]

# Multiple providers can coexist; models reference them by name.
# [[providers]]
# name              = "claude"
# type              = "claude"
# api_keys          = ["env:ANTHROPIC_API_KEY"]
# anthropic_version = "2024-02-15"
# model_list        = ["claude-sonnet-4-5-20250929"]
```

**Config commands:**

```bash
codewiki config gen                           # print starter config.toml to stdout
codewiki config init                          # create starter config.toml
codewiki config validate --config config.toml             # structural check
codewiki config validate --config config.toml --check-secrets  # + verify env vars
codewiki config get      --config config.toml             # display parsed config
```

**Passing config to generate:**

```bash
# Explicit path (recommended)
codewiki generate -C /path/to/repo --config config.toml

# Via env var (useful in Docker / CI)
export CODEWIKI_CONFIG=/path/to/config.toml
codewiki generate -C /path/to/repo

# Log diagnostics to file
CODEWIKI_LOG_FILE=codewiki.log codewiki generate -C /path/to/repo
```

`codewiki config set` and `codewiki config agent` edit the TOML file in place.

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
uv sync --extra dev
pre-commit install

# Run local quality checks
uv run ruff check .
uv run ruff format --check .
uv run ty check

# Run tests
python -m pytest tests/ -q

# Run index/clustering/generation/postprocess tests
python -m pytest tests/test_index_*.py tests/test_clustering_*.py \
    tests/test_generation_*.py tests/test_postprocess_*.py -q

# Run all local hooks
pre-commit run --all-files
```

---

## Performance

Evaluated on 30 repositories — 6 languages, up to 1.4M LOC:

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
