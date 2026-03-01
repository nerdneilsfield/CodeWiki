# Multi-Document-Type Generation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add four new auto-generated documentation types (Get Started, Beginner's Guide, Build & Code Organization, Core Algorithms) that run after MODULE docs, with hash-based caching, three-layer context assembly, and static site navigation integration.

**Architecture:** New `GuideGenerator` class orchestrates four guide types, each with specialized prompts. `RepoDocsCollector` assembles a three-layer context bundle (repo docs + code analysis + generated MODULE docs). Hash-based caching avoids redundant LLM calls. Static site generator updated with fixed navigation order.

**Tech Stack:** Python 3, pytest, asyncio, existing `call_llm` / `file_manager` utilities

**Reference:** `docs/plans/2026-03-01-multi-doc-types-design.md`

---

### Task 1: Create RepoDocsCollector — scanning and indexing

**Files:**
- Create: `codewiki/src/be/repo_docs_collector.py`
- Test: `tests/test_repo_docs_collector.py`

**Step 1: Write failing tests for RepoDocsCollector**

```python
# tests/test_repo_docs_collector.py
import os
import tempfile
from pathlib import Path

from codewiki.src.be.repo_docs_collector import RepoDocsCollector, DocSnippet


def _make_tree(base, files: dict):
    """Create a file tree under base. files maps relative path to content."""
    for rel, content in files.items():
        p = Path(base) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def test_collect_repo_docs_finds_markdown():
    with tempfile.TemporaryDirectory() as repo:
        _make_tree(repo, {
            "README.md": "# Hello\nThis is a readme.",
            "docs/guide.md": "# Guide\nSome guide content.",
            "src/main.py": "# not a doc file",
        })
        collector = RepoDocsCollector()
        bundle = collector.collect(repo_path=repo, working_dir=None, components={})
        md_paths = [s.path for s in bundle.repo_docs]
        assert any("README.md" in p for p in md_paths)
        assert any("guide.md" in p for p in md_paths)
        assert not any("main.py" in p for p in md_paths)


def test_collect_excludes_node_modules_and_git():
    with tempfile.TemporaryDirectory() as repo:
        _make_tree(repo, {
            "README.md": "# Hello",
            "node_modules/pkg/README.md": "# skip me",
            ".git/HEAD": "ref: refs/heads/main",
        })
        collector = RepoDocsCollector()
        bundle = collector.collect(repo_path=repo, working_dir=None, components={})
        paths = [s.path for s in bundle.repo_docs]
        assert not any("node_modules" in p for p in paths)
        assert not any(".git" in p for p in paths)


def test_collect_generated_docs():
    with tempfile.TemporaryDirectory() as repo:
        with tempfile.TemporaryDirectory() as wd:
            _make_tree(wd, {
                "module-a.md": "# Module A\nDeep dive content.",
                "overview.md": "# Overview\nProject overview.",
            })
            collector = RepoDocsCollector()
            bundle = collector.collect(repo_path=repo, working_dir=wd, components={})
            gen_paths = [s.path for s in bundle.generated_docs]
            assert any("module-a.md" in p for p in gen_paths)


def test_collect_docstrings_from_components():
    from codewiki.src.be.dependency_analyzer.models.core import Node
    comp = Node(
        id="mod.py::MyClass", name="MyClass", component_type="class",
        file_path="/tmp/mod.py", relative_path="mod.py",
        source_code="class MyClass: pass", start_line=1, end_line=1,
        has_docstring=True, docstring="This class manages user sessions.",
        parameters=None, node_type="class", base_classes=None,
        class_name=None, display_name="MyClass", component_id="mod.py::MyClass",
    )
    collector = RepoDocsCollector()
    bundle = collector.collect(repo_path="/tmp", working_dir=None, components={"mod.py::MyClass": comp})
    assert any("user sessions" in s.content for s in bundle.docstrings)


def test_select_relevant_returns_matching_snippets():
    with tempfile.TemporaryDirectory() as repo:
        _make_tree(repo, {
            "README.md": "# Project\nInstallation instructions for setup.",
            "docs/api.md": "# API\nEndpoint documentation for REST.",
            "docs/auth.md": "# Auth\nAuthentication flow with JWT tokens.",
        })
        collector = RepoDocsCollector()
        bundle = collector.collect(repo_path=repo, working_dir=None, components={})
        results = bundle.select_relevant("installation setup", max_tokens=2000)
        # README should rank higher for "installation setup"
        assert len(results) > 0
        assert "installation" in results[0].content.lower() or "setup" in results[0].content.lower()


def test_select_relevant_respects_max_tokens():
    with tempfile.TemporaryDirectory() as repo:
        _make_tree(repo, {
            f"docs/doc{i}.md": f"# Doc {i}\n{'content ' * 500}" for i in range(20)
        })
        collector = RepoDocsCollector()
        bundle = collector.collect(repo_path=repo, working_dir=None, components={})
        results = bundle.select_relevant("content", max_tokens=500)
        total_chars = sum(len(r.content) for r in results)
        # Rough token estimate: 1 token ≈ 4 chars
        assert total_chars < 500 * 4
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && python -m pytest tests/test_repo_docs_collector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codewiki.src.be.repo_docs_collector'`

**Step 3: Implement RepoDocsCollector**

```python
# codewiki/src/be/repo_docs_collector.py
"""
Three-layer document collector for guide generation context.

Layer 1: Repository original docs (.md, .rst, .txt)
Layer 2: Code docstrings extracted from AST-parsed components
Layer 3: Already-generated MODULE documentation
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

_EXCLUDED_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".eggs", "*.egg-info",
}
_DOC_EXTENSIONS = {".md", ".rst", ".txt"}
# Internal guide files that should not be collected as "generated docs"
_GUIDE_PREFIXES = (
    "guide-",           # all guide output files
    "_guide_cache", "_parent_doc_hashes", "_tree_cache_meta",
)


@dataclass
class DocSnippet:
    """A single documentation snippet with metadata."""
    path: str           # relative path or identifier
    content: str        # text content
    source: str         # "repo" | "generated" | "docstring"
    token_estimate: int = 0

    def __post_init__(self):
        if self.token_estimate == 0:
            # Rough estimate: 1 token ≈ 4 chars
            self.token_estimate = len(self.content) // 4


@dataclass
class DocsBundle:
    """Unified bundle of documentation from all three layers."""
    repo_docs: List[DocSnippet] = field(default_factory=list)
    generated_docs: List[DocSnippet] = field(default_factory=list)
    docstrings: List[DocSnippet] = field(default_factory=list)

    def select_relevant(
        self, topic: str, max_tokens: int,
    ) -> List[DocSnippet]:
        """Select most relevant doc snippets for a given topic.

        Uses simple keyword overlap scoring. Priority order:
        generated module docs > repo docs > docstrings.
        """
        keywords = set(topic.lower().split())

        def _score(snippet: DocSnippet) -> float:
            text_lower = (snippet.content + " " + snippet.path).lower()
            hit_count = sum(1 for kw in keywords if kw in text_lower)
            # Priority multiplier by source
            multiplier = {"generated": 3.0, "repo": 2.0, "docstring": 1.0}
            return hit_count * multiplier.get(snippet.source, 1.0)

        all_snippets = self.generated_docs + self.repo_docs + self.docstrings
        scored = [(s, _score(s)) for s in all_snippets]
        scored.sort(key=lambda x: x[1], reverse=True)

        selected: List[DocSnippet] = []
        used_tokens = 0
        for snippet, score in scored:
            if score <= 0:
                continue
            if used_tokens + snippet.token_estimate > max_tokens:
                continue
            selected.append(snippet)
            used_tokens += snippet.token_estimate

        return selected


class RepoDocsCollector:
    """Scans repo, generated docs, and code analysis to build a DocsBundle."""

    def collect(
        self,
        repo_path: str,
        working_dir: Optional[str],
        components: Dict[str, Any],
    ) -> DocsBundle:
        bundle = DocsBundle()

        # Layer 1: Repo docs
        if repo_path and os.path.isdir(repo_path):
            bundle.repo_docs = self._scan_repo_docs(repo_path)
            logger.debug(f"Collected {len(bundle.repo_docs)} repo doc snippets")

        # Layer 2: Docstrings
        if components:
            bundle.docstrings = self._extract_docstrings(components)
            logger.debug(f"Collected {len(bundle.docstrings)} docstring snippets")

        # Layer 3: Generated docs
        if working_dir and os.path.isdir(working_dir):
            bundle.generated_docs = self._scan_generated_docs(working_dir)
            logger.debug(f"Collected {len(bundle.generated_docs)} generated doc snippets")

        return bundle

    def _scan_repo_docs(self, repo_path: str) -> List[DocSnippet]:
        snippets: List[DocSnippet] = []
        for root, dirs, files in os.walk(repo_path):
            # Prune excluded directories in-place
            dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS]
            for fname in files:
                ext = Path(fname).suffix.lower()
                if ext not in _DOC_EXTENSIONS:
                    continue
                full = os.path.join(root, fname)
                try:
                    content = Path(full).read_text(encoding="utf-8", errors="replace")
                    rel = os.path.relpath(full, repo_path)
                    snippets.append(DocSnippet(path=rel, content=content, source="repo"))
                except Exception as e:
                    logger.warning(f"Could not read {full}: {e}")
        return snippets

    def _scan_generated_docs(self, working_dir: str) -> List[DocSnippet]:
        snippets: List[DocSnippet] = []
        for fname in os.listdir(working_dir):
            if not fname.endswith(".md"):
                continue
            if fname.startswith(_GUIDE_PREFIXES):
                continue
            full = os.path.join(working_dir, fname)
            try:
                content = Path(full).read_text(encoding="utf-8", errors="replace")
                snippets.append(DocSnippet(path=fname, content=content, source="generated"))
            except Exception as e:
                logger.warning(f"Could not read generated doc {full}: {e}")
        return snippets

    def _extract_docstrings(self, components: Dict[str, Any]) -> List[DocSnippet]:
        snippets: List[DocSnippet] = []
        for comp_id, node in components.items():
            doc = getattr(node, "docstring", None) or ""
            if len(doc.strip()) < 20:
                continue
            snippets.append(DocSnippet(
                path=comp_id,
                content=doc.strip(),
                source="docstring",
            ))
        return snippets
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && python -m pytest tests/test_repo_docs_collector.py -v`
Expected: All 6 tests PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/repo_docs_collector.py tests/test_repo_docs_collector.py
git commit -m "feat(guides): add RepoDocsCollector with 3-layer doc scanning"
```

---

### Task 2: Create GuideGenerator skeleton with hash caching

**Files:**
- Create: `codewiki/src/be/guide_generator.py`
- Test: `tests/test_guide_generator.py`

**Step 1: Write failing tests for hash caching**

```python
# tests/test_guide_generator.py
import os
import tempfile
from pathlib import Path

from codewiki.src.be.guide_generator import GuideGenerator


def _minimal_config():
    """Return a minimal Config-like object for testing."""
    from codewiki.src.config import Config
    return Config(
        repo_path="/tmp/fake-repo",
        output_dir="/tmp/output",
        dependency_graph_dir="/tmp/dg",
        docs_dir="/tmp/docs",
        max_depth=2,
        llm_base_url="http://localhost:4000/",
        llm_api_key="sk-test",
        main_model="test-model",
        cluster_model="test-model",
    )


def test_should_regenerate_when_no_cache():
    with tempfile.TemporaryDirectory() as wd:
        gen = GuideGenerator(
            config=_minimal_config(),
            components={},
            module_tree={},
            working_dir=wd,
        )
        assert gen._should_regenerate("getting_started", []) is True


def test_should_not_regenerate_when_hash_matches():
    with tempfile.TemporaryDirectory() as wd:
        # Create a fake input file
        inp = os.path.join(wd, "input.md")
        Path(inp).write_text("hello", encoding="utf-8")
        out = os.path.join(wd, "guide-getting-started.md")
        Path(out).write_text("# Getting Started\nContent here.", encoding="utf-8")

        gen = GuideGenerator(
            config=_minimal_config(),
            components={},
            module_tree={},
            working_dir=wd,
        )
        # Simulate a cache entry
        gen._update_cache("getting_started", [inp], [out])
        gen._save_cache()

        # Reload
        gen2 = GuideGenerator(
            config=_minimal_config(),
            components={},
            module_tree={},
            working_dir=wd,
        )
        assert gen2._should_regenerate("getting_started", [inp]) is False


def test_should_regenerate_when_input_changes():
    with tempfile.TemporaryDirectory() as wd:
        inp = os.path.join(wd, "input.md")
        Path(inp).write_text("hello", encoding="utf-8")
        out = os.path.join(wd, "guide-getting-started.md")
        Path(out).write_text("# Getting Started", encoding="utf-8")

        gen = GuideGenerator(
            config=_minimal_config(),
            components={},
            module_tree={},
            working_dir=wd,
        )
        gen._update_cache("getting_started", [inp], [out])
        gen._save_cache()

        # Mutate the input
        Path(inp).write_text("changed!", encoding="utf-8")

        gen2 = GuideGenerator(
            config=_minimal_config(),
            components={},
            module_tree={},
            working_dir=wd,
        )
        assert gen2._should_regenerate("getting_started", [inp]) is True
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && python -m pytest tests/test_guide_generator.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement GuideGenerator skeleton**

```python
# codewiki/src/be/guide_generator.py
"""
Guide Generator — orchestrates generation of four guide documentation types.

Called after MODULE docs are complete. Each guide type has:
- Specialized context assembly
- Dedicated prompt template
- Hash-based caching to avoid redundant LLM calls
"""

import asyncio
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from codewiki.src.be.dependency_analyzer.utils.security import assert_safe_path
from codewiki.src.be.llm_services import call_llm
from codewiki.src.be.repo_docs_collector import RepoDocsCollector, DocsBundle
from codewiki.src.config import Config
from codewiki.src.utils import file_manager

logger = logging.getLogger(__name__)

GUIDE_CACHE_FILENAME = "_guide_cache.json"
# Per-guide prompt versions — bump only the guide whose prompt changed
_PROMPT_VERSIONS = {
    "getting_started": "v1",
    "beginner_guide": "v1",
    "build_analysis": "v1",
    "algorithm_deepdive": "v1",
}


class GuideGenerator:
    """Orchestrates generation of all guide document types."""

    def __init__(
        self,
        config: Config,
        components: Dict[str, Any],
        module_tree: Dict[str, Any],
        working_dir: str,
    ):
        self.config = config
        self.components = components
        self.module_tree = module_tree
        self.working_dir = working_dir
        self.collector = RepoDocsCollector()
        self.docs_bundle: Optional[DocsBundle] = None
        self.cache = self._load_cache()

    # ── Cache management ──────────────────────────────────────────────

    def _cache_path(self) -> str:
        return os.path.join(self.working_dir, GUIDE_CACHE_FILENAME)

    def _load_cache(self) -> Dict[str, Any]:
        p = self._cache_path()
        if os.path.exists(p):
            return file_manager.load_json(p) or {}
        return {}

    def _save_cache(self):
        file_manager.save_json(self.cache, self._cache_path())

    @staticmethod
    def _compute_combined_hash(file_paths: List[str], extra: str = "") -> str:
        h = hashlib.md5()
        if extra:
            h.update(extra.encode())
        for fp in sorted(file_paths):
            try:
                h.update(Path(fp).read_bytes())
            except OSError:
                h.update(fp.encode())
        return h.hexdigest()

    @staticmethod
    def _sanitize_slug(raw: str, index: int = 0) -> str:
        """Sanitize an LLM-generated slug to [a-z0-9-] only.

        Falls back to "part-{index}" when the slug becomes empty after
        sanitization (e.g. pure Chinese titles), preventing filename collisions.
        """
        slug = re.sub(r'[^a-z0-9-]', '', raw.lower().strip())
        slug = re.sub(r'-+', '-', slug).strip('-')
        return slug or f"part-{index}"

    def _unique_slug(self, raw: str, index: int = 0) -> str:
        """Sanitize slug and deduplicate against already-used slugs."""
        base = self._sanitize_slug(raw, index)
        if not hasattr(self, '_used_slugs'):
            self._used_slugs: set = set()
        slug = base
        counter = 2
        while slug in self._used_slugs:
            slug = f"{base}-{counter}"
            counter += 1
        self._used_slugs.add(slug)
        return slug

    def _safe_output_path(self, filename: str) -> str:
        """Build output path and validate it doesn't escape working_dir."""
        out = os.path.join(self.working_dir, filename)
        assert_safe_path(Path(self.working_dir), Path(out))
        return out

    def _should_regenerate(
        self, guide_type: str, input_files: List[str], extra_salt: str = ""
    ) -> bool:
        version = _PROMPT_VERSIONS.get(guide_type, "v1")
        extra = f"{version}:{extra_salt}" if extra_salt else version
        current_hash = self._compute_combined_hash(input_files, extra=extra)
        cached = self.cache.get(guide_type, {})
        if cached.get("input_hash") == current_hash:
            # output_files are relative filenames — resolve against working_dir
            outputs = cached.get("output_files", [])
            return not all(
                os.path.exists(os.path.join(self.working_dir, f))
                and os.path.getsize(os.path.join(self.working_dir, f)) > 10
                for f in outputs
            )
        return True

    def _update_cache(
        self, guide_type: str, input_files: List[str], output_files: List[str],
        extra_salt: str = "",
    ):
        version = _PROMPT_VERSIONS.get(guide_type, "v1")
        extra = f"{version}:{extra_salt}" if extra_salt else version
        # Store relative filenames (not absolute paths) for cross-environment portability
        rel_names = [os.path.basename(f) for f in output_files]
        self.cache[guide_type] = {
            "input_hash": self._compute_combined_hash(input_files, extra=extra),
            "output_files": rel_names,
        }

    # ── LLM calling with full resilience chain ─────────────────────────

    async def _call_llm_with_fallback(self, prompt: str) -> str:
        """Call LLM with: long-context pre-select → retry → fallback chain.

        Mirrors the agent framework's resilience pattern:
        1. Pre-select long-context model when prompt exceeds threshold
        2. Otherwise try models in order: main → fallback(s) → long_context
        3. Each call_llm() has its own 4-retry loop (10/30/90s backoff)
        """
        from codewiki.src.be.utils import count_tokens

        prompt_tokens = count_tokens(prompt)

        # Pre-select: skip straight to long-context model for oversized prompts
        if (
            self.config.long_context_model
            and prompt_tokens > self.config.long_context_threshold
        ):
            logger.info(
                f"Pre-selecting long-context model {self.config.long_context_model} "
                f"(prompt {prompt_tokens} tokens > threshold {self.config.long_context_threshold})"
            )
            async with self._semaphore:
                return await asyncio.to_thread(
                    call_llm, prompt, self.config, model=self.config.long_context_model
                )

        # Build fallback chain: main → fallback(s) → long_context (last resort)
        models = [self.config.main_model]
        if self.config.fallback_model:
            models.extend(
                n.strip() for n in self.config.fallback_model.split(",") if n.strip()
            )
        if self.config.long_context_model and self.config.long_context_model not in models:
            models.append(self.config.long_context_model)

        last_exc = None
        for model_name in models:
            try:
                async with self._semaphore:
                    return await asyncio.to_thread(
                        call_llm, prompt, self.config, model=model_name
                    )
            except Exception as e:
                logger.warning(f"Guide LLM call failed with model {model_name}: {e}")
                last_exc = e
        raise last_exc

    # ── Main entry point ──────────────────────────────────────────────

    async def run(self):
        """Generate all guide types with phased concurrency.

        Phase 1: Independent single-page guides (parallel)
        Phase 2: Beginner's Guide (serial sections — carry-forward)
        Phase 3: Core Algorithms (parallel deep-dives)
        Phase 4: Regenerate overview
        """
        logger.info("📖 Starting guide generation phase")

        # Semaphore bounds concurrent LLM calls (same as MODULE doc pipeline)
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)
        self._results: Dict[str, str] = {}  # guide name → "success" | "FAILED: ...""

        # Collect all available documentation context
        self.docs_bundle = self.collector.collect(
            self.config.repo_path, self.working_dir, self.components
        )

        # Layer 3 quality gate: warn if no MODULE docs were generated
        gen_docs = [
            f for f in os.listdir(self.working_dir)
            if f.endswith(".md") and not f.startswith(_GUIDE_PREFIXES)
        ]
        if not gen_docs:
            logger.warning(
                "⚠ No MODULE docs found in working_dir — guide quality will be degraded. "
                "Consider re-running MODULE generation first."
            )

        # Phase 1: Independent single-page guides — run concurrently
        phase1 = [
            self._safe_generate(self.generate_getting_started),
            self._safe_generate(self.generate_build_analysis),
        ]
        await asyncio.gather(*phase1)

        # Phase 2: Beginner's Guide (sections are serial due to carry-forward)
        await self._safe_generate(self.generate_beginner_guide)

        # Phase 3: Core Algorithms (per-algorithm deep-dives are parallel)
        await self._safe_generate(self.generate_algorithm_deepdive)

        # Phase 4: Regenerate overview to reference new guide pages
        await self._regenerate_overview()

        self._report_results()
        self._save_cache()
        logger.info("📖 Guide generation phase complete")

    async def _safe_generate(self, gen_fn):
        """Wrap a guide generator in try/except — warn and continue on failure."""
        name = gen_fn.__name__
        try:
            await gen_fn()
            self._results[name] = "success"
        except Exception as e:
            logger.warning(f"Guide generation failed ({name}): {e}")
            self._results[name] = f"FAILED: {e}"

    def _report_results(self):
        """Print a summary report of guide generation results."""
        labels = {
            "generate_getting_started": "Getting Started",
            "generate_build_analysis": "Build & Code Org",
            "generate_beginner_guide": "Beginner's Guide",
            "generate_algorithm_deepdive": "Core Algorithms",
        }
        lines = ["📖 Guide generation report:"]
        for fn_name, label in labels.items():
            status = self._results.get(fn_name, "skipped")
            icon = "✓" if status == "success" else "✗"
            lines.append(f"  {icon} {label:25s} → {status}")
        logger.info("\n".join(lines))

    async def _regenerate_overview(self):
        """Augment overview.md with guide navigation section."""
        from codewiki.src.be.prompt_template import (
            OVERVIEW_AUGMENT_PROMPT, format_language_instruction,
        )
        overview_path = os.path.join(self.working_dir, "overview.md")
        existing = self._read_file_safe(overview_path)
        if not existing:
            logger.warning("No overview.md found, skipping augmentation")
            return

        # Build list of successfully generated guides
        guides_list = []
        guide_files = [
            ("guide-getting-started.md", "Get Started", "Quick installation and first-run tutorial"),
            ("guide-beginners-guide.md", "Beginner's Guide", "Accessible multi-chapter walkthrough"),
            ("guide-build-and-organization.md", "Build & Code Organization", "Build pipeline and project structure"),
            ("guide-core-algorithms.md", "Core Algorithms", "Formal algorithm deep-dives"),
        ]
        for fname, title, summary in guide_files:
            fpath = os.path.join(self.working_dir, fname)
            if os.path.exists(fpath) and os.path.getsize(fpath) > 100:
                guides_list.append(f"- [{title}]({fname}): {summary}")

        if not guides_list:
            logger.info("No guide pages generated, skipping overview augmentation")
            return

        repo_name = os.path.basename(os.path.normpath(self.config.repo_path))
        prompt = OVERVIEW_AUGMENT_PROMPT.format(
            repo_name=repo_name,
            existing_overview=existing,
            guides_list="\n".join(guides_list),
            language_instruction=format_language_instruction(self.config.output_language),
        )
        response = await self._call_llm_with_fallback(prompt)
        content = self._parse_guide_response(response)
        if content:
            file_manager.save_text(content, overview_path)
            logger.info("✓ Overview augmented with guide navigation")

    # ── Guide generators (stubs — implemented in subsequent tasks) ────

    async def generate_getting_started(self):
        """Generate getting-started.md."""
        pass  # Task 4

    async def generate_beginner_guide(self):
        """Generate beginner's guide (outline → sub-pages → parent)."""
        pass  # Task 5

    async def generate_build_analysis(self):
        """Generate build-and-organization.md."""
        pass  # Task 6

    async def generate_algorithm_deepdive(self):
        """Generate core-algorithms pages."""
        pass  # Task 7
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && python -m pytest tests/test_guide_generator.py -v`
Expected: All 3 tests PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/guide_generator.py tests/test_guide_generator.py
git commit -m "feat(guides): add GuideGenerator skeleton with hash caching"
```

---

### Task 3: Add guide prompt templates

**Files:**
- Modify: `codewiki/src/be/prompt_template.py` (append after existing constants)

**Step 1: Add Get Started prompt constant**

Append after the `HLS_EXTRA_GUIDE` constant (after line 467):

```python
# ── Guide document prompt templates ──────────────────────────────────────────

GETTING_STARTED_PROMPT = """
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

<MERMAID_REQUIREMENTS>
- A `flowchart TD` showing the installation pipeline (clone → install deps → configure → run)
- A `sequenceDiagram` showing the first-run interaction between user, CLI, and backend
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
""".strip()

BEGINNER_SECTION_PROMPT = """
You are writing chapter {section_number}/{total_sections} of the beginner's
guide for `{repo_name}`:

**Chapter title:** {section_title}
**Learning goal:** {section_summary}

<WRITING_STYLE>
- Use everyday analogies for every technical concept
  GOOD: "模块就像乐高积木——每块有自己的形状和功能，组合起来才能搭出完整的城堡"
  BAD:  "模块是封装了相关函数和类的命名空间"
- Compare with well-known projects readers likely know
  ("This module's role is similar to Express.js's Router" / "Think of it like
   React's useState, but for …")
- Every technical term MUST be explained in plain language on first use
- Use "Imagine …", "Think of it as …", "You can picture this as …"
- Short paragraphs — one concept per paragraph
- Heavy Mermaid usage: architecture diagrams, data-flow charts, concept maps
</WRITING_STYLE>

<MERMAID_REQUIREMENTS>
Every major concept or flow MUST have a companion Mermaid diagram:
- `graph TD` for architecture / component relationships
- `flowchart` for data flow and process steps
- `sequenceDiagram` for request traces and interactions
- `classDiagram` for concept relationship maps (even if not literally classes)
Max ~15 nodes per diagram.  Every diagram must be followed by a prose walkthrough.
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

BEGINNER_PARENT_PROMPT = """
You are writing the landing page for the beginner's guide to `{repo_name}`.
This page links to {num_sections} chapters and gives readers a roadmap.

Write a short, welcoming introduction (2-3 paragraphs) explaining:
1. Who this guide is for
2. What they will learn
3. A Mermaid `flowchart LR` showing the chapter progression

Then list each chapter with its title, a 1-2 sentence teaser, and a link.

<CHAPTERS>
{chapters_list}
</CHAPTERS>

{language_instruction}

Generate in Markdown.  Wrap in:
<GUIDE>
content
</GUIDE>
""".strip()

BUILD_ANALYSIS_PROMPT = """
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
""".strip()

ALGORITHM_DEEPDIVE_PROMPT = """
You are an algorithm researcher writing a formal deep-dive on the
**{algorithm_title}** algorithm from `{repo_name}`.

<WRITING_STYLE>
- Formal, academic-quality writing
- LaTeX math: `$inline$` and `$$block$$` for complexity, recurrences, constraints
- Pseudocode in ```pseudocode blocks alongside actual implementation
- Compare with classical algorithms or papers where applicable
</WRITING_STYLE>

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

ALGORITHM_PARENT_PROMPT = """
You are writing the landing page for the **Core Algorithms** section of
`{repo_name}`.  This page introduces the project's key algorithms, shows how
they relate to each other, and links to individual deep-dives.

Write:
1. An introduction (2-3 paragraphs) explaining the project's computational core
2. A Mermaid `graph TD` showing algorithm relationships and data flow between them
3. For each algorithm: title, one-paragraph summary, link to its page

<ALGORITHMS>
{algorithms_list}
</ALGORITHMS>

{language_instruction}

Generate in Markdown.  Wrap in:
<GUIDE>
content
</GUIDE>
""".strip()
```

**Step 2: Add overview augmentation prompt**

After `ALGORITHM_PARENT_PROMPT`, add:

```python
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
```

**Step 3: Add language_instruction helper**

After the new constants, add a helper function:

```python
def format_language_instruction(output_language: str) -> str:
    """Return a language instruction string for guide prompts."""
    if output_language and output_language.lower() != "en":
        return f"\n<LANGUAGE_INSTRUCTION>\nWrite the documentation in {output_language}.\n</LANGUAGE_INSTRUCTION>"
    return ""
```

**Step 3: Commit**

```bash
git add codewiki/src/be/prompt_template.py
git commit -m "feat(guides): add prompt templates for all 4 guide types"
```

---

### Task 4: Implement generate_getting_started()

**Files:**
- Modify: `codewiki/src/be/guide_generator.py`

**Step 1: Add context-assembly helpers**

Add these private methods to `GuideGenerator`:

```python
    # ── Context assembly helpers ──────────────────────────────────────

    def _read_file_safe(self, path: str) -> str:
        """Read file content, return empty string on failure."""
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

    def _find_readme(self) -> str:
        """Find and read README from repo root."""
        for name in ("README.md", "README", "readme.md", "README.txt"):
            p = os.path.join(self.config.repo_path, name)
            if os.path.exists(p):
                return self._read_file_safe(p)
        return ""

    def _find_setup_files(self) -> str:
        """Collect content of package/build setup files."""
        candidates = [
            "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg",
            "package.json", "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
            "Makefile", "CMakeLists.txt", "Dockerfile",
        ]
        parts = []
        for name in candidates:
            p = os.path.join(self.config.repo_path, name)
            if os.path.exists(p):
                content = self._read_file_safe(p)
                if content:
                    parts.append(f"--- {name} ---\n{content}")
        return "\n\n".join(parts)

    def _find_cli_entry(self) -> str:
        """Find main entry point file content."""
        candidates = [
            "codewiki/src/be/main.py", "src/main.py", "main.py",
            "cli/main.py", "app.py", "manage.py",
            "__main__.py", "src/__main__.py",
            "src/index.ts", "src/index.js", "index.js",
            "cmd/main.go", "main.go",
        ]
        for name in candidates:
            p = os.path.join(self.config.repo_path, name)
            if os.path.exists(p):
                return f"--- {name} ---\n{self._read_file_safe(p)}"
        return ""

    def _find_config_source(self) -> str:
        """Find configuration file/class source."""
        candidates = [
            "codewiki/src/config.py", "src/config.py", "config.py",
            "src/config.ts", "config/settings.py",
        ]
        for name in candidates:
            p = os.path.join(self.config.repo_path, name)
            if os.path.exists(p):
                return f"--- {name} ---\n{self._read_file_safe(p)}"
        return ""

    def _read_overview(self) -> str:
        """Read the generated overview.md."""
        p = os.path.join(self.working_dir, "overview.md")
        return self._read_file_safe(p)

    def _format_relevant_docs(self, topic: str, max_tokens: int = 4000) -> str:
        """Select relevant docs and format as prompt section."""
        if not self.docs_bundle:
            return ""
        snippets = self.docs_bundle.select_relevant(topic, max_tokens)
        if not snippets:
            return ""
        parts = [f"<RELEVANT_DOCS>"]
        for s in snippets:
            parts.append(f"--- {s.path} ({s.source}) ---\n{s.content}")
        parts.append("</RELEVANT_DOCS>")
        return "\n\n".join(parts)

    def _parse_guide_response(self, response: str) -> str:
        """Extract content from <GUIDE>...</GUIDE> tags."""
        if "<GUIDE>" in response and "</GUIDE>" in response:
            return response.split("<GUIDE>")[1].split("</GUIDE>")[0].strip()
        return response.strip()
```

**Step 2: Implement generate_getting_started()**

```python
    async def generate_getting_started(self):
        """Generate getting-started.md."""
        from codewiki.src.be.prompt_template import (
            GETTING_STARTED_PROMPT, format_language_instruction,
        )

        output_path = self._safe_output_path("guide-getting-started.md")
        repo_name = os.path.basename(os.path.normpath(self.config.repo_path))

        # Gather ALL input files for hash (comprehensive per design §4.2)
        readme_path = None
        for name in ("README.md", "README", "readme.md"):
            p = os.path.join(self.config.repo_path, name)
            if os.path.exists(p):
                readme_path = p
                break
        setup_file_names = [
            "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg",
            "package.json", "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
            "Makefile", "CMakeLists.txt", "Dockerfile",
        ]
        overview_path = os.path.join(self.working_dir, "overview.md")
        input_files = [p for p in [readme_path] if p]
        input_files.extend(
            os.path.join(self.config.repo_path, n)
            for n in setup_file_names
            if os.path.exists(os.path.join(self.config.repo_path, n))
        )
        if os.path.exists(overview_path):
            input_files.append(overview_path)

        if not self._should_regenerate("getting_started", input_files):
            logger.info("✓ getting-started.md is up to date (cache hit)")
            return

        logger.info("📝 Generating Getting Started guide")

        prompt = GETTING_STARTED_PROMPT.format(
            repo_name=repo_name,
            readme=self._find_readme(),
            setup_files=self._find_setup_files(),
            cli_entry=self._find_cli_entry(),
            config_source=self._find_config_source(),
            overview=self._read_overview(),
            relevant_docs=self._format_relevant_docs("installation setup getting started"),
            language_instruction=format_language_instruction(self.config.output_language),
        )

        response = await self._call_llm_with_fallback(prompt)
        content = self._parse_guide_response(response)
        file_manager.save_text(content, output_path)

        self._update_cache("getting_started", input_files, [output_path])
        logger.info(f"✓ Getting Started guide saved to {output_path}")
```

**Step 3: Commit**

```bash
git add codewiki/src/be/guide_generator.py
git commit -m "feat(guides): implement generate_getting_started()"
```

---

### Task 5: Implement generate_beginner_guide()

**Files:**
- Modify: `codewiki/src/be/guide_generator.py`

**Step 1: Add module summary helper**

```python
    def _build_module_summaries(self, max_chars_per_module: int = 500) -> str:
        """Build summaries of all generated module docs (first N chars each)."""
        parts = []
        for fname in sorted(os.listdir(self.working_dir)):
            if not fname.endswith(".md"):
                continue
            if fname.startswith(_GUIDE_PREFIXES) or fname == "overview.md":
                continue
            full = os.path.join(self.working_dir, fname)
            content = self._read_file_safe(full)
            if content:
                summary = content[:max_chars_per_module]
                if len(content) > max_chars_per_module:
                    summary += "\n... (truncated)"
                parts.append(f"### {fname}\n{summary}")
        return "\n\n".join(parts)

    def _read_module_doc(self, module_name: str) -> str:
        """Read the full generated doc for a module by name."""
        from codewiki.src.utils import find_module_doc
        path = find_module_doc(self.working_dir, [module_name])
        if path:
            return self._read_file_safe(path)
        # Fallback: try direct filename
        candidates = [
            os.path.join(self.working_dir, f"{module_name}.md"),
            os.path.join(self.working_dir, f"{module_name.replace(' ', '-').lower()}.md"),
        ]
        for c in candidates:
            if os.path.exists(c):
                return self._read_file_safe(c)
        return ""
```

Also add at the top of the file:
```python
# Filename prefixes for guide pages (excluded from "generated module docs" collection)
_GUIDE_PREFIXES = (
    "guide-",           # all guide output files
    "_guide_cache", "_parent_doc_hashes", "_tree_cache_meta",
)
```

**Step 2: Implement generate_beginner_guide()**

```python
    async def generate_beginner_guide(self):
        """Generate beginner's guide: outline → serial sections → parent page."""
        from pydantic import BaseModel, Field, ValidationError
        from codewiki.src.be.prompt_template import (
            BEGINNER_OUTLINE_PROMPT, BEGINNER_SECTION_PROMPT,
            BEGINNER_PARENT_PROMPT, format_language_instruction,
        )

        class OutlineSection(BaseModel):
            id: str
            title: str
            focus_modules: list[str] = Field(default_factory=list)
            summary: str = ""

        class OutlineSchema(BaseModel):
            title: str = ""
            sections: list[OutlineSection] = Field(default_factory=list)

        repo_name = os.path.basename(os.path.normpath(self.config.repo_path))
        module_tree_str = json.dumps(
            self._strip_tree_for_display(self.module_tree), indent=2
        )

        # Hash check: generated docs + module_tree structure
        gen_doc_files = [
            os.path.join(self.working_dir, f)
            for f in sorted(os.listdir(self.working_dir))
            if f.endswith(".md") and not f.startswith(_GUIDE_PREFIXES)
        ]
        module_tree_hash = hashlib.sha256(
            json.dumps(self.module_tree, sort_keys=True).encode()
        ).hexdigest()[:16]
        if not self._should_regenerate(
            "beginner_guide", gen_doc_files, extra_salt=module_tree_hash
        ):
            logger.info("✓ Beginner's guide is up to date (cache hit)")
            return

        logger.info("📝 Generating Beginner's Guide — Phase A: outline")

        # ── Phase A: generate outline ─────────────────────────────────
        outline_prompt = BEGINNER_OUTLINE_PROMPT.format(
            repo_name=repo_name,
            module_tree=module_tree_str,
            module_summaries=self._build_module_summaries(),
        )
        outline_response = await self._call_llm_with_fallback(outline_prompt)
        raw_outline = self._parse_json_response(outline_response, "OUTLINE")
        try:
            outline = OutlineSchema(**raw_outline)
        except ValidationError as e:
            raise ValueError(f"Beginner guide outline validation failed: {e}")
        sections = outline.sections
        if not sections:
            raise ValueError("LLM returned empty beginner guide outline")

        logger.info(f"📝 Beginner's Guide — Phase B: generating {len(sections)} sections")

        # ── Phase B: serial section generation ────────────────────────
        output_files = []
        carry_forward = ""
        lang_inst = format_language_instruction(self.config.output_language)

        for i, section in enumerate(sections):
            section_id = self._unique_slug(section.id, index=i)
            section_title = section.title or f"Part {i+1}"
            section_summary = section.summary
            focus_modules = section.focus_modules

            # Gather focus module docs
            focus_docs = "\n\n".join(
                self._read_module_doc(m) for m in focus_modules
                if self._read_module_doc(m)
            )

            section_prompt = BEGINNER_SECTION_PROMPT.format(
                repo_name=repo_name,
                section_number=i + 1,
                total_sections=len(sections),
                section_title=section_title,
                section_summary=section_summary,
                outline_json=json.dumps(outline.model_dump(), indent=2),
                carry_forward=carry_forward,
                module_docs=focus_docs,
                repo_docs=self._format_relevant_docs(section_title, max_tokens=3000),
                module_tree=module_tree_str,
                language_instruction=lang_inst,
            )

            response = await self._call_llm_with_fallback(section_prompt)
            content = self._parse_guide_response(response)

            out_path = self._safe_output_path(f"guide-beginners-guide-{section_id}.md")
            file_manager.save_text(content, out_path)
            output_files.append(out_path)

            # Build carry-forward summary (first complete paragraph, max ~400 chars)
            paras = content.split("\n\n")
            summary_line = paras[0].replace("\n", " ")
            if len(summary_line) > 400:
                summary_line = summary_line[:400].rsplit(" ", 1)[0] + "..."
            elif len(paras) > 1:
                summary_line += "..."
            carry_forward += f"\n\n### Chapter {i+1}: {section_title}\n{summary_line}"

            logger.info(f"  ✓ Section {i+1}/{len(sections)}: {section_title}")

        # ── Phase C: parent page ──────────────────────────────────────
        logger.info("📝 Beginner's Guide — Phase C: parent page")
        chapters_list = "\n".join(
            f"- [{s.title}](beginners-guide-{self._unique_slug(s.id, index=i)}.md): {s.summary}"
            for i, s in enumerate(sections)
        )
        parent_prompt = BEGINNER_PARENT_PROMPT.format(
            repo_name=repo_name,
            num_sections=len(sections),
            chapters_list=chapters_list,
            language_instruction=lang_inst,
        )
        parent_response = await self._call_llm_with_fallback(parent_prompt)
        parent_content = self._parse_guide_response(parent_response)
        parent_path = os.path.join(self.working_dir, "guide-beginners-guide.md")
        file_manager.save_text(parent_content, parent_path)
        output_files.insert(0, parent_path)

        self._update_cache(
            "beginner_guide", gen_doc_files, output_files,
            extra_salt=module_tree_hash,
        )
        logger.info(f"✓ Beginner's Guide complete: {len(sections)} sections")

    @staticmethod
    def _strip_tree_for_display(tree: Dict[str, Any]) -> Dict[str, Any]:
        """Return a lightweight module tree for display in prompts."""
        light: Dict[str, Any] = {}
        for name, info in tree.items():
            entry: Dict[str, Any] = {}
            children = info.get("children")
            if isinstance(children, dict) and children:
                entry["children"] = GuideGenerator._strip_tree_for_display(children)
            light[name] = entry
        return light

    @staticmethod
    def _parse_json_response(response: str, tag: str) -> Dict[str, Any]:
        """Extract and parse JSON from an XML-tagged LLM response."""
        open_tag = f"<{tag}>"
        close_tag = f"</{tag}>"
        if open_tag in response and close_tag in response:
            json_str = response.split(open_tag)[1].split(close_tag)[0].strip()
        else:
            json_str = response.strip()
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse JSON from {tag} response")
            return {}
```

**Step 3: Commit**

```bash
git add codewiki/src/be/guide_generator.py
git commit -m "feat(guides): implement beginner guide (outline → serial sections → parent)"
```

---

### Task 6: Implement generate_build_analysis()

**Files:**
- Modify: `codewiki/src/be/guide_generator.py`

**Step 1: Add language-specific build guide constants**

Add at the module level in `guide_generator.py`:

```python
# ── Language-specific build guide snippets ────────────────────────────────

_LANG_BUILD_GUIDES = {
    "python": """<PYTHON_BUILD_GUIDE>
- Analyze pyproject.toml / setup.py: entry points, dependency groups (dev/test/prod)
- Explain __init__.py and package structure
- Virtual environment management strategy
- Common commands: pip install, pytest, python -m
</PYTHON_BUILD_GUIDE>""",

    "javascript": """<JS_BUILD_GUIDE>
- Analyze package.json: scripts, dependencies vs devDependencies
- Bundler configuration (webpack / vite / esbuild) if present
- Monorepo structure (workspaces) if applicable
- Common commands: npm install, npm run build, npm test
</JS_BUILD_GUIDE>""",

    "typescript": """<TS_BUILD_GUIDE>
- Analyze tsconfig.json: target, module resolution, strict flags
- Build pipeline: tsc → bundler → output
- Type declaration strategy (.d.ts files)
</TS_BUILD_GUIDE>""",

    "java": """<JAVA_BUILD_GUIDE>
- Analyze pom.xml / build.gradle: dependency management, build lifecycle
- Module structure (multi-module projects)
- Common commands: mvn package, gradle build
</JAVA_BUILD_GUIDE>""",

    "go": """<GO_BUILD_GUIDE>
- Analyze go.mod: module path, dependency versions
- Package conventions and directory layout
- Build tags and cross-compilation
- Common commands: go build, go test, go mod tidy
</GO_BUILD_GUIDE>""",

    "rust": """<RUST_BUILD_GUIDE>
- Analyze Cargo.toml: workspace structure, feature flags, dependency features
- Build profiles (dev vs release)
- Common commands: cargo build, cargo test, cargo clippy
</RUST_BUILD_GUIDE>""",

    "c": """<C_BUILD_GUIDE>
- Analyze Makefile / CMakeLists.txt: targets, compilation flags, link dependencies
- Header / source file organization
- Cross-platform considerations
</C_BUILD_GUIDE>""",

    "cpp": """<CPP_BUILD_GUIDE>
- Analyze CMakeLists.txt: targets, C++ standard, link dependencies
- Header / source file organization and include paths
- Template instantiation and compilation model
</CPP_BUILD_GUIDE>""",
}
```

**Step 2: Implement generate_build_analysis()**

```python
    async def generate_build_analysis(self):
        """Generate build-and-organization.md (multi-language adaptive)."""
        from codewiki.src.be.prompt_template import (
            BUILD_ANALYSIS_PROMPT, format_language_instruction,
        )

        output_path = self._safe_output_path("guide-build-and-organization.md")
        repo_name = os.path.basename(os.path.normpath(self.config.repo_path))

        # Collect build files
        build_file_names = [
            "Makefile", "GNUmakefile", "makefile", "CMakeLists.txt",
            "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
            "package.json", "tsconfig.json", "webpack.config.js", "vite.config.ts",
            "Cargo.toml", "go.mod",
            "pom.xml", "build.gradle", "build.gradle.kts",
            "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
            ".github/workflows/ci.yml", ".github/workflows/ci.yaml",
        ]
        # Lock files are auto-generated and bloat the prompt — exclude them
        _LOCK_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml",
                       "Cargo.lock", "poetry.lock", "Pipfile.lock", "go.sum"}
        MAX_BUILD_FILE_CHARS = 20000  # per file

        build_files_content = []
        input_files = []
        for name in build_file_names:
            if os.path.basename(name) in _LOCK_FILES:
                continue
            p = os.path.join(self.config.repo_path, name)
            if os.path.exists(p):
                content = self._read_file_safe(p)
                if content:
                    if len(content) > MAX_BUILD_FILE_CHARS:
                        content = content[:MAX_BUILD_FILE_CHARS] + "\n... (truncated)"
                    build_files_content.append(f"--- {name} ---\n{content}")
                    input_files.append(p)

        # Also hash component source files (design §4.2 requirement)
        component_source_files = sorted({
            getattr(n, "file_path", "")
            for n in self.components.values()
            if getattr(n, "file_path", "")
        })
        input_files.extend(f for f in component_source_files if f not in input_files)

        if not self._should_regenerate("build_analysis", input_files):
            logger.info("✓ build-and-organization.md is up to date (cache hit)")
            return

        logger.info("📝 Generating Build & Code Organization guide")

        # Detect languages and assemble language-specific guides
        detected = set()
        for comp in self.components.values():
            rel = getattr(comp, "relative_path", "") or ""
            ext = os.path.splitext(rel)[1].lower()
            lang = {
                ".py": "python", ".js": "javascript", ".mjs": "javascript",
                ".ts": "typescript", ".tsx": "typescript",
                ".java": "java", ".go": "go", ".rs": "rust",
                ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp",
                ".hpp": "cpp", ".cs": "csharp", ".php": "php",
            }.get(ext)
            if lang:
                detected.add(lang)

        lang_guides = "\n\n".join(
            _LANG_BUILD_GUIDES[lang]
            for lang in sorted(detected)
            if lang in _LANG_BUILD_GUIDES
        )

        # Directory tree (2-level)
        dir_tree = self._build_directory_tree(self.config.repo_path, max_depth=2)

        module_tree_str = json.dumps(
            self._strip_tree_for_display(self.module_tree), indent=2
        )

        prompt = BUILD_ANALYSIS_PROMPT.format(
            repo_name=repo_name,
            language_specific_guides=lang_guides,
            directory_tree=dir_tree,
            build_files="\n\n".join(build_files_content),
            module_tree=module_tree_str,
            module_docs=self._format_relevant_docs(
                "build compilation organization structure", max_tokens=4000
            ),
            language_instruction=format_language_instruction(self.config.output_language),
        )

        response = await self._call_llm_with_fallback(prompt)
        content = self._parse_guide_response(response)
        file_manager.save_text(content, output_path)

        self._update_cache("build_analysis", input_files, [output_path])
        logger.info(f"✓ Build & Code Organization guide saved to {output_path}")

    @staticmethod
    def _build_directory_tree(root: str, max_depth: int = 2) -> str:
        """Build a simple directory tree string."""
        lines = []
        root_p = Path(root)
        excluded = {
            "node_modules", ".git", "__pycache__", ".venv", "venv",
            ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
            ".eggs",
        }

        def _walk(path: Path, prefix: str, depth: int):
            if depth > max_depth:
                return
            try:
                entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name))
            except PermissionError:
                return
            dirs = [e for e in entries if e.is_dir() and e.name not in excluded]
            files = [e for e in entries if e.is_file()]
            items = dirs + files
            for i, item in enumerate(items):
                is_last = i == len(items) - 1
                connector = "└── " if is_last else "├── "
                suffix = "/" if item.is_dir() else ""
                lines.append(f"{prefix}{connector}{item.name}{suffix}")
                if item.is_dir():
                    extension = "    " if is_last else "│   "
                    _walk(item, prefix + extension, depth + 1)

        lines.append(f"{root_p.name}/")
        _walk(root_p, "", 0)
        return "\n".join(lines)
```

**Step 3: Commit**

```bash
git add codewiki/src/be/guide_generator.py
git commit -m "feat(guides): implement build & code organization analysis"
```

---

### Task 7: Implement generate_algorithm_deepdive()

**Files:**
- Modify: `codewiki/src/be/guide_generator.py`

**Step 1: Add helpers for algorithm context**

```python
    def _build_components_summary(self) -> str:
        """Build a summary of all components for algorithm identification."""
        lines = []
        for comp_id, node in sorted(self.components.items()):
            comp_type = getattr(node, "component_type", "unknown")
            doc = (getattr(node, "docstring", "") or "")[:100]
            lines.append(f"  {comp_id} ({comp_type}): {doc}")
        return "\n".join(lines)

    def _build_dependency_summary(self) -> str:
        """Build a summary of dependency relationships."""
        lines = []
        for comp_id, node in sorted(self.components.items()):
            deps = getattr(node, "depends_on", set()) or set()
            if deps:
                lines.append(f"  {comp_id} → {', '.join(sorted(deps))}")
        return "\n".join(lines) if lines else "(no dependencies found)"

    def _read_component_source(
        self, comp_ids: List[str], max_tokens: int = 30000
    ) -> str:
        """Read source code for given component IDs, truncated to max_tokens."""
        from codewiki.src.be.utils import count_tokens
        parts = []
        total = 0
        seen_files = set()
        for cid in comp_ids:
            node = self.components.get(cid)
            if node is None:
                continue
            fp = getattr(node, "file_path", "")
            if fp and fp not in seen_files:
                seen_files.add(fp)
                # Prefer node source_code (AST-extracted) over full file
                content = getattr(node, "source_code", "") or self._read_file_safe(fp)
                if content:
                    chunk_tokens = count_tokens(content)
                    if total + chunk_tokens > max_tokens:
                        remaining = max_tokens - total
                        if remaining > 500:
                            # Truncate this file
                            content = content[:remaining * 4] + "\n... (truncated)"
                        else:
                            logger.debug(
                                f"Skipping {fp}: would exceed max_tokens ({total}/{max_tokens})"
                            )
                            continue
                    rel = getattr(node, "relative_path", fp)
                    parts.append(f"--- {rel} ---\n{content}")
                    total += count_tokens(content)
        return "\n\n".join(parts)

    def _find_test_file_paths(self, comp_ids) -> List[str]:
        """Return paths of test files related to comp_ids (for hashing)."""
        if isinstance(comp_ids, str):
            comp_ids = [comp_ids]
        stems = set()
        for cid in comp_ids:
            node = self.components.get(cid)
            if node:
                rel = getattr(node, "relative_path", "")
                stem = Path(rel).stem
                if stem:
                    stems.add(stem)
        paths = []
        for td in ("tests", "test", "spec"):
            test_dir = os.path.join(self.config.repo_path, td)
            if not os.path.isdir(test_dir):
                continue
            for fname in sorted(os.listdir(test_dir)):
                if any(stem in fname for stem in stems):
                    paths.append(os.path.join(test_dir, fname))
        return paths

    def _find_test_files(
        self, comp_ids: List[str], max_tokens: int = 15000
    ) -> str:
        """Find and read test files, truncated to max_tokens."""
        from codewiki.src.be.utils import count_tokens
        paths = self._find_test_file_paths(comp_ids)
        parts = []
        total = 0
        for full in paths:
            content = self._read_file_safe(full)
            if content:
                chunk_tokens = count_tokens(content)
                if total + chunk_tokens > max_tokens:
                    logger.debug(f"Skipping test {full}: would exceed max_tokens")
                    continue
                td = os.path.basename(os.path.dirname(full))
                fname = os.path.basename(full)
                parts.append(f"--- {td}/{fname} ---\n{content}")
                total += chunk_tokens
        return "\n\n".join(parts)

    def _build_dependency_edges(self, comp_ids: List[str]) -> str:
        """Build dependency graph edges for specific components."""
        target_set = set(comp_ids)
        lines = []
        for cid in comp_ids:
            node = self.components.get(cid)
            if not node:
                continue
            deps = getattr(node, "depends_on", set()) or set()
            for dep in sorted(deps):
                lines.append(f"  {cid} → {dep}")
        return "\n".join(lines) if lines else "(no edges)"
```

**Step 2: Implement generate_algorithm_deepdive()**

```python
    async def generate_algorithm_deepdive(self):
        """Generate core algorithm pages: identify → per-algorithm → parent."""
        from pydantic import BaseModel, Field, ValidationError
        from codewiki.src.be.prompt_template import (
            ALGORITHM_IDENTIFY_PROMPT, ALGORITHM_DEEPDIVE_PROMPT,
            ALGORITHM_PARENT_PROMPT, format_language_instruction,
        )

        class AlgorithmEntry(BaseModel):
            id: str
            title: str
            related_components: list[str] = Field(default_factory=list)
            summary: str = ""

        class AlgorithmListSchema(BaseModel):
            algorithms: list[AlgorithmEntry] = Field(default_factory=list)

        repo_name = os.path.basename(os.path.normpath(self.config.repo_path))

        # Hash check: component source files + test files (design §4.2)
        source_files = sorted({
            getattr(n, "file_path", "")
            for n in self.components.values()
            if getattr(n, "file_path", "")
        })
        test_files = sorted({
            t for comp_id in self.components
            for t in self._find_test_file_paths(comp_id)
        })
        if not self._should_regenerate("algorithm_deepdive", source_files + test_files):
            logger.info("✓ Core Algorithms pages are up to date (cache hit)")
            return

        logger.info("📝 Generating Core Algorithms — Phase A: identification")

        # ── Phase A: identify algorithms ──────────────────────────────
        id_prompt = ALGORITHM_IDENTIFY_PROMPT.format(
            repo_name=repo_name,
            components_summary=self._build_components_summary(),
            dependency_summary=self._build_dependency_summary(),
            module_summaries=self._build_module_summaries(max_chars_per_module=200),
        )
        id_response = await self._call_llm_with_fallback(id_prompt)
        raw_algo = self._parse_json_response(id_response, "ALGORITHMS")
        try:
            algo_data = AlgorithmListSchema(**raw_algo)
        except ValidationError as e:
            raise ValueError(f"Algorithm list validation failed: {e}")
        algorithms = algo_data.algorithms
        if not algorithms:
            raise ValueError("No core algorithms identified by LLM")

        logger.info(
            f"📝 Core Algorithms — Phase B: generating {len(algorithms)} deep-dives"
        )

        # ── Phase B: per-algorithm deep-dives (parallel, Semaphore) ───
        lang_inst = format_language_instruction(self.config.output_language)
        output_files: List[str] = [None] * len(algorithms)  # preserve order

        async def _generate_one_algo(idx: int, algo: AlgorithmEntry):
            algo_id = self._unique_slug(algo.id, index=idx)
            algo_title = algo.title or f"Algorithm {idx+1}"
            related = algo.related_components

            dd_prompt = ALGORITHM_DEEPDIVE_PROMPT.format(
                repo_name=repo_name,
                algorithm_title=algo_title,
                source_code=self._read_component_source(related),
                test_code=self._find_test_files(related),
                module_docs=self._format_relevant_docs(algo_title, max_tokens=4000),
                dependency_edges=self._build_dependency_edges(related),
                language_instruction=lang_inst,
            )

            response = await self._call_llm_with_fallback(dd_prompt)
            content = self._parse_guide_response(response)
            out_path = self._safe_output_path(f"guide-core-algorithms-{algo_id}.md")
            file_manager.save_text(content, out_path)
            output_files[idx] = out_path
            logger.info(f"  ✓ Algorithm {idx+1}/{len(algorithms)}: {algo_title}")

        await asyncio.gather(
            *[_generate_one_algo(i, algo) for i, algo in enumerate(algorithms)]
        )
        output_files = [p for p in output_files if p]  # filter None (shouldn't happen)

        # ── Phase C: parent page ──────────────────────────────────────
        logger.info("📝 Core Algorithms — Phase C: parent page")
        algos_list = "\n".join(
            f"- [{a.title}](core-algorithms-{self._unique_slug(a.id, index=i)}.md): {a.summary}"
            for i, a in enumerate(algorithms)
        )
        parent_prompt = ALGORITHM_PARENT_PROMPT.format(
            repo_name=repo_name,
            algorithms_list=algos_list,
            language_instruction=lang_inst,
        )
        parent_response = await self._call_llm_with_fallback(parent_prompt)
        parent_content = self._parse_guide_response(parent_response)
        parent_path = os.path.join(self.working_dir, "guide-core-algorithms.md")
        file_manager.save_text(parent_content, parent_path)
        output_files.insert(0, parent_path)

        self._update_cache("algorithm_deepdive", source_files + test_files, output_files)
        logger.info(f"✓ Core Algorithms complete: {len(algorithms)} deep-dives")
```

**Step 3: Commit**

```bash
git add codewiki/src/be/guide_generator.py
git commit -m "feat(guides): implement core algorithm deep-dive (identify → generate → parent)"
```

---

### Task 7b: Add contract tests for guide generators

**Files:**
- Modify: `tests/test_guide_generator.py`

**Step 1: Add contract tests**

Append to `tests/test_guide_generator.py`:

```python
import json
from unittest.mock import patch, AsyncMock

import pytest

from codewiki.src.be.guide_generator import GuideGenerator, _PROMPT_VERSIONS


def test_sanitize_slug_strips_unsafe_chars():
    assert GuideGenerator._sanitize_slug("hello-world") == "hello-world"
    assert GuideGenerator._sanitize_slug("../../../etc/passwd") == "etcpasswd"
    assert GuideGenerator._sanitize_slug("Hello World!") == "helloworld"
    assert GuideGenerator._sanitize_slug("section_1:overview") == "section1overview"
    assert GuideGenerator._sanitize_slug("") == "part-0"
    assert GuideGenerator._sanitize_slug("---") == "part-0"
    assert GuideGenerator._sanitize_slug("", index=3) == "part-3"


def test_sanitize_slug_collapses_dashes():
    assert GuideGenerator._sanitize_slug("a---b") == "a-b"
    assert GuideGenerator._sanitize_slug("-leading-trailing-") == "leading-trailing"


def test_safe_output_path_rejects_traversal():
    with tempfile.TemporaryDirectory() as wd:
        gen = GuideGenerator(
            config=_minimal_config(),
            components={},
            module_tree={},
            working_dir=wd,
        )
        # Normal filename should work
        p = gen._safe_output_path("guide-getting-started.md")
        assert wd in p

        # Path traversal should raise
        with pytest.raises(Exception):
            gen._safe_output_path("../../../etc/passwd")


def test_prompt_version_affects_hash():
    with tempfile.TemporaryDirectory() as wd:
        inp = os.path.join(wd, "input.md")
        Path(inp).write_text("content", encoding="utf-8")

        h1 = GuideGenerator._compute_combined_hash([inp], extra="v1")
        h2 = GuideGenerator._compute_combined_hash([inp], extra="v2")
        assert h1 != h2


def test_parse_json_response_fallback():
    """Malformed JSON returns empty dict, not crash."""
    result = GuideGenerator._parse_json_response("not json at all", "OUTLINE")
    assert result == {}

    # Valid JSON in tags
    result = GuideGenerator._parse_json_response(
        '<OUTLINE>{"sections": []}</OUTLINE>', "OUTLINE"
    )
    assert result == {"sections": []}


@pytest.mark.asyncio
async def test_run_continues_on_guide_failure():
    """One guide failure should not prevent others from running."""
    with tempfile.TemporaryDirectory() as wd:
        gen = GuideGenerator(
            config=_minimal_config(),
            components={},
            module_tree={},
            working_dir=wd,
        )
        gen.docs_bundle = gen.collector.collect("/tmp", None, {})

        call_count = {"value": 0}
        original_build = gen.generate_build_analysis

        async def failing_guide():
            raise RuntimeError("LLM exploded")

        async def counting_guide():
            call_count["value"] += 1

        gen.generate_getting_started = failing_guide
        gen.generate_beginner_guide = counting_guide
        gen.generate_build_analysis = counting_guide
        gen.generate_algorithm_deepdive = counting_guide

        with patch.object(gen, '_regenerate_overview', new_callable=AsyncMock):
            await gen.run()

        # 3 guides should have run despite the first one failing
        assert call_count["value"] == 3
```

**Step 2: Run tests**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && python -m pytest tests/test_guide_generator.py -v`
Expected: All tests PASS (3 original + 6 new)

**Step 3: Commit**

```bash
git add tests/test_guide_generator.py
git commit -m "test(guides): add contract tests for slug sanitization, cache, error handling"
```

---

### Task 8: Integrate GuideGenerator into DocumentationGenerator.run()

**Files:**
- Modify: `codewiki/src/be/documentation_generator.py:1-17,707-712`

**Step 1: Add import**

At line 34 (after the last import), add:

```python
from codewiki.src.be.guide_generator import GuideGenerator
```

**Step 2: Add guide generation call in run()**

After line 707 (`self.create_documentation_metadata(...)`) and before line 709, insert:

```python
            # Generate guide documents (Get Started, Beginner's Guide, etc.)
            logger.info("📖 Starting guide document generation")
            guide_gen = GuideGenerator(
                config=self.config,
                components=components,
                module_tree=module_tree,
                working_dir=working_dir,
            )
            await guide_gen.run()
```

**Step 3: Run existing tests to verify no regressions**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && python -m pytest tests/ -v`
Expected: All 170+ tests PASS

**Step 4: Commit**

```bash
git add codewiki/src/be/documentation_generator.py
git commit -m "feat(guides): integrate GuideGenerator into main pipeline"
```

---

### Task 9: Update static site navigation

**Files:**
- Modify: `codewiki/cli/static_generator.py`

**Step 1: Define guide navigation structure**

Find the section where `nav_html` is built (around line 483). Replace the
existing Overview + module_tree nav with a structured approach:

```python
        # ── Build navigation with guides before module tree ──────────────
        nav_html = ""
        if module_tree:
            # 1. Overview (existing behavior)
            ov_active = ' on' if html_name in ("overview.html", "index.html") else ''
            nav_html += (
                f'  <div class="nav-row">\n'
                f'    <a href="index.html" class="nv{ov_active}">Overview</a>\n'
                f'  </div>\n'
            )

            # 2. Guide pages (fixed order, only if files exist)
            guide_pages = [
                ("guide-getting-started", "Get Started"),
                ("guide-beginners-guide", "Beginner's Guide"),
                ("guide-build-and-organization", "Build & Code Organization"),
                ("guide-core-algorithms", "Core Algorithms"),
            ]
            for slug, label in guide_pages:
                md_file = os.path.join(docs_dir, f"{slug}.md")
                if not os.path.exists(md_file):
                    continue
                guide_html = slug + ".html"
                active = ' on' if html_name == guide_html else ''
                nav_html += (
                    f'  <div class="nav-row">\n'
                    f'    <a href="{guide_html}" class="nv{active}">{label}</a>\n'
                    f'  </div>\n'
                )
                # Sub-pages for multi-page guides
                sub_prefix = slug + "-"
                sub_pages = sorted([
                    f for f in os.listdir(docs_dir)
                    if f.startswith(sub_prefix) and f.endswith(".md")
                ])
                if sub_pages:
                    nav_html += f'  <div class="nvsub" style="display:block">\n'
                    for sub_file in sub_pages:
                        sub_html = sub_file.replace(".md", ".html")
                        sub_label = sub_file[len(sub_prefix):-3].replace("-", " ").title()
                        sub_active = ' on' if html_name == sub_html else ''
                        nav_html += (
                            f'    <div class="nav-row" style="padding-left:24px">\n'
                            f'      <a href="{sub_html}" class="nv{sub_active}">{sub_label}</a>\n'
                            f'    </div>\n'
                        )
                    nav_html += '  </div>\n'

            # 3. Module tree (existing)
            nav_html += _build_nav_html(module_tree, html_name, resolved_hrefs=resolved_hrefs)
```

**Step 2: Add guide .md files to the rendering loop**

Find where markdown files are iterated and converted to HTML. Ensure guide
files (`getting-started.md`, `beginners-guide.md`, `beginners-guide-*.md`,
`build-and-organization.md`, `core-algorithms.md`, `core-algorithms-*.md`)
are included in the conversion loop. They should already be picked up by the
existing `for md_file in ...` loop since they're in the same `docs_dir`, but
verify no filtering excludes them.

**Step 3: Verify static site generator imports and guide nav logic**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && python -c "from codewiki.cli.static_generator import StaticSiteGenerator; print('static gen import OK')"`
Expected: `static gen import OK`

Then run the full test suite to verify no regressions:

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && python -m pytest tests/ -v -k "static or generator"`
Expected: All related tests PASS

**Step 4: Commit**

```bash
git add codewiki/cli/static_generator.py
git commit -m "feat(guides): add guide pages to static site navigation"
```

---

### Task 9b: Static site navigation integration test

**Files:**
- Modify: `tests/test_guide_generator.py`

**Step 1: Add integration test for guide navigation HTML**

Append to `tests/test_guide_generator.py`:

```python
def test_static_site_guide_navigation():
    """Guide pages appear in generated static HTML navigation."""
    from codewiki.cli.static_generator import StaticSiteGenerator

    with tempfile.TemporaryDirectory() as tmp:
        docs_dir = Path(tmp)

        # Minimal module_tree.json so nav is built
        (docs_dir / "module_tree.json").write_text('{"main": {}}', encoding="utf-8")

        # overview.md is required for index.html
        (docs_dir / "overview.md").write_text("# Overview\nHello", encoding="utf-8")

        # Guide .md stubs
        for slug in ("guide-getting-started", "guide-beginners-guide",
                      "guide-build-and-organization", "guide-core-algorithms"):
            (docs_dir / f"{slug}.md").write_text(
                f"# {slug}\nPlaceholder", encoding="utf-8"
            )
        # Sub-page
        (docs_dir / "guide-beginners-guide-setup.md").write_text(
            "# Setup\nPlaceholder", encoding="utf-8"
        )

        # Run static generation (writes .html files)
        gen = StaticSiteGenerator()
        gen.generate(docs_dir)

        # Read the generated index.html and verify guide nav
        index_html = (docs_dir / "index.html").read_text(encoding="utf-8")

        assert 'href="guide-getting-started.html"' in index_html
        assert 'href="guide-beginners-guide.html"' in index_html
        assert 'href="guide-build-and-organization.html"' in index_html
        assert 'href="guide-core-algorithms.html"' in index_html
        assert 'href="guide-beginners-guide-setup.html"' in index_html

        # Fixed ordering: getting-started before core-algorithms
        gs_pos = index_html.index("guide-getting-started.html")
        ca_pos = index_html.index("guide-core-algorithms.html")
        assert gs_pos < ca_pos, "Guide navigation order must follow definition order"
```

**Step 2: Run**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && python -m pytest tests/test_guide_generator.py::test_static_site_guide_navigation -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_guide_generator.py
git commit -m "test(guides): add static site navigation integration test"
```

---

### Task 9c: Add guide navigation to `--github-pages` viewer template

**Files:**
- Modify: `codewiki/cli/html_generator.py`
- Modify: `codewiki/templates/github_pages/viewer_template.html`

**Step 1: Generate GUIDE_PAGES JSON in html_generator.py**

In `html_generator.py`, in the `generate()` method, after computing `docs_base_path`
and before building `replacements`, scan `docs_dir` for guide files and build a
JSON structure:

```python
        # Build guide pages list for navigation
        guide_pages_json = "[]"
        if docs_dir:
            guide_defs = [
                ("guide-getting-started", "Get Started"),
                ("guide-beginners-guide", "Beginner's Guide"),
                ("guide-build-and-organization", "Build & Code Organization"),
                ("guide-core-algorithms", "Core Algorithms"),
            ]
            guide_entries = []
            for slug, label in guide_defs:
                md_path = docs_dir / f"{slug}.md"
                if not md_path.exists():
                    continue
                entry = {"slug": slug, "label": label, "subPages": []}
                # Find sub-pages (e.g. guide-beginners-guide-setup.md)
                sub_prefix = slug + "-"
                for sub_file in sorted(docs_dir.glob(f"{sub_prefix}*.md")):
                    sub_slug = sub_file.stem
                    sub_label = sub_slug[len(sub_prefix):].replace("-", " ").title()
                    entry["subPages"].append({"slug": sub_slug, "label": sub_label})
                guide_entries.append(entry)
            guide_pages_json = json.dumps(guide_entries, indent=2)
```

Add `"{{GUIDE_PAGES_JSON}}"`: `guide_pages_json` to the `replacements` dict.

**Step 2: Update viewer_template.html**

In the `<script>` section after `const MODULE_TREE = ...`, add:

```javascript
        const GUIDE_PAGES = {{GUIDE_PAGES_JSON}};
```

In the `buildNavigation()` function, insert guide nav items BEFORE the module tree
loop (`for (const [key, data] of Object.entries(MODULE_TREE))`):

```javascript
        function buildNavigation() {
            const nav = document.getElementById('navigation');
            let html = '';

            // Guide pages (fixed order, before module tree)
            for (const guide of GUIDE_PAGES) {
                html += `<div class="nav-section">`;
                html += `<div class="nav-item" data-file="${guide.slug}.md">
                    📖 ${guide.label}
                </div>`;
                if (guide.subPages && guide.subPages.length > 0) {
                    for (const sub of guide.subPages) {
                        html += `<div class="nav-subsection">
                            <div class="nav-item" data-file="${sub.slug}.md">
                                ${sub.label}
                            </div>
                        </div>`;
                    }
                }
                html += `</div>`;
            }

            // Module tree (existing)
            for (const [key, data] of Object.entries(MODULE_TREE)) {
                html += buildNavItem(key, data, 0);
            }

            nav.innerHTML = html;
            // ... existing click handlers unchanged
```

**Step 3: Verify**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && python -c "from codewiki.cli.html_generator import HTMLGenerator; print('import OK')"`
Expected: `import OK`

**Step 4: Commit**

```bash
git add codewiki/cli/html_generator.py codewiki/templates/github_pages/viewer_template.html
git commit -m "feat(guides): add guide navigation to --github-pages viewer"
```

---

### Task 10: Final integration test

**Step 1: Run the full test suite**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && python -m pytest tests/ -v`
Expected: All tests PASS (170+ existing + 9 new)

**Step 2: Verify imports are clean**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && python -c "from codewiki.src.be.guide_generator import GuideGenerator; from codewiki.src.be.repo_docs_collector import RepoDocsCollector; print('imports OK')"`
Expected: `imports OK`

**Step 3: Add acceptance test for JSON failure capture**

Append to `tests/test_guide_generator.py`:

```python
@pytest.mark.asyncio
async def test_json_validation_failure_is_reported_as_failed():
    """JSON validation failure must appear as FAILED, not success."""
    with tempfile.TemporaryDirectory() as wd:
        gen = GuideGenerator(
            config=_minimal_config(),
            components={},
            module_tree={},
            working_dir=wd,
        )
        gen.docs_bundle = gen.collector.collect("/tmp", None, {})

        # Mock LLM to return invalid JSON for beginner outline
        async def bad_llm(prompt):
            return "this is not json"

        gen._call_llm_with_fallback = bad_llm

        with patch.object(gen, '_regenerate_overview', new_callable=AsyncMock):
            await gen.run()

        # Beginner guide should be FAILED, not success
        assert "FAILED" in gen._results.get("generate_beginner_guide", "")
```

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && python -m pytest tests/test_guide_generator.py::test_json_validation_failure_is_reported_as_failed -v`
Expected: PASS

**Step 4: Commit any final fixups**

```bash
git add -u
git commit -m "chore: final integration fixups for guide generation"
```
