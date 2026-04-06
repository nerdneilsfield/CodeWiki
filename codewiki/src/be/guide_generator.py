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

from codewiki.src.be.cache_manager import CacheManager
from codewiki.src.be.dependency_analyzer.utils.security import assert_safe_path
from codewiki.src.be.cancellation import CancellationToken
from codewiki.src.be.errors import CancellationError, LLMError
from codewiki.src.be.llm_middleware import LLMMiddleware
from codewiki.src.be.llm_retry import LLMRetryExhausted, with_retry
from codewiki.src.be.llm_usage import LLMUsageStats
from codewiki.src.be.repo_docs_collector import RepoDocsCollector, DocsBundle
from codewiki.src.codewiki_config import CodeWikiConfig
from codewiki.src.config import internal_file_path
from codewiki.src.config_loader import resolve_model_ref
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
_GUIDE_PREFIXES = (
    "guide-",
    "_guide_cache",
    "_parent_doc_hashes",
    "_tree_cache_meta",
)

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


class GuideGenerator:
    """Orchestrates generation of all guide document types."""

    def __init__(
        self,
        config: CodeWikiConfig,
        components: Dict[str, Any],
        module_tree: Dict[str, Any],
        working_dir: str,
        usage_stats: LLMUsageStats | None = None,
        cancel_token: CancellationToken | None = None,
        middleware: LLMMiddleware | None = None,
        cache_manager: CacheManager | None = None,
    ):
        self.config = config
        self.components = components
        self.module_tree = module_tree
        self.working_dir = working_dir
        self.collector = RepoDocsCollector()
        self.docs_bundle: Optional[DocsBundle] = None
        self.cache = self._load_cache()
        self.usage_stats = usage_stats
        self.cancel_token = cancel_token
        self._middleware = middleware or LLMMiddleware(config, usage_stats=usage_stats)
        self._cache_manager = cache_manager

    # ── Cache management ──────────────────────────────────────────────

    def _cache_path(self) -> str:
        return internal_file_path(self.working_dir, GUIDE_CACHE_FILENAME)

    def _load_cache(self) -> Dict[str, Any]:
        p = self._cache_path()
        if os.path.exists(p):
            return file_manager.load_json(p) or {}
        return {}

    def _save_cache(self):
        file_manager.save_json(self.cache, self._cache_path())
        logger.debug("💾 Guide cache saved")

    @staticmethod
    def _compute_combined_hash(file_paths: List[str], extra: str = "") -> str:
        h = hashlib.md5()
        if extra:
            h.update(extra.encode())
        for fp in sorted(file_paths):
            try:
                with open(fp, "rb") as f:
                    while chunk := f.read(8192):
                        h.update(chunk)
            except OSError:
                h.update(fp.encode())
        return h.hexdigest()

    @staticmethod
    def _sanitize_slug(raw: str, index: int = 0) -> str:
        """Sanitize an LLM-generated slug to [a-z0-9-] only.

        Falls back to "part-{index}" when the slug becomes empty after
        sanitization (e.g. pure Chinese titles), preventing filename collisions.
        """
        slug = re.sub(r"[^a-z0-9-]", "", raw.lower().strip())
        slug = re.sub(r"-+", "-", slug).strip("-")
        return slug or f"part-{index}"

    def _unique_slug(self, raw: str, index: int = 0, used: Optional[set] = None) -> str:
        """Sanitize slug and deduplicate.

        Pass an explicit ``used`` set to keep deduplication scoped to one guide.
        Each generate_* should create its own ``used: set = set()`` and pass it
        here, so slugs from one guide cannot pollute another.
        """
        base = self._sanitize_slug(raw, index)
        if used is None:
            # Fallback: use instance-level set (kept for backward compat only)
            if not hasattr(self, "_used_slugs"):
                self._used_slugs: set = set()
            used = self._used_slugs
        slug = base
        counter = 2
        while slug in used:
            slug = f"{base}-{counter}"
            counter += 1
        used.add(slug)
        return slug

    def _safe_output_path(self, filename: str) -> str:
        """Build output path and validate it doesn't escape working_dir."""
        out = os.path.join(self.working_dir, filename)
        assert_safe_path(Path(self.working_dir), Path(out))
        return out

    def _model_supports_stream(self, model_ref: str) -> bool:
        if not self.config.providers:
            return False
        try:
            return resolve_model_ref(model_ref, self.config.providers).stream
        except Exception:
            return False

    def _should_regenerate_legacy(
        self, guide_type: str, input_files: List[str], extra_salt: str = ""
    ) -> bool:
        version = _PROMPT_VERSIONS.get(guide_type, "v1")
        lang = self.config.output_language or "en"
        extra = f"{version}:{lang}:{extra_salt}" if extra_salt else f"{version}:{lang}"
        current_hash = self._compute_combined_hash(input_files, extra=extra)
        cached = self.cache.get(guide_type, {})
        if cached.get("input_hash") == current_hash:
            # output_files are relative filenames — resolve against working_dir
            outputs = cached.get("output_files", [])
            return not all(
                os.path.exists(os.path.join(self.working_dir, f))
                and os.path.getsize(os.path.join(self.working_dir, f)) > 100
                for f in outputs
            )
        return True

    def _update_cache_legacy(
        self,
        guide_type: str,
        input_files: List[str],
        output_files: List[str],
        extra_salt: str = "",
    ):
        version = _PROMPT_VERSIONS.get(guide_type, "v1")
        lang = self.config.output_language or "en"
        extra = f"{version}:{lang}:{extra_salt}" if extra_salt else f"{version}:{lang}"
        # Store relative filenames (not absolute paths) for cross-environment portability
        rel_names = [os.path.basename(f) for f in output_files]
        self.cache[guide_type] = {
            "input_hash": self._compute_combined_hash(input_files, extra=extra),
            "output_files": rel_names,
        }

    def _compute_guide_input_hash(
        self, input_files: List[str], guide_type: str, extra_salt: str = ""
    ) -> str:
        version = _PROMPT_VERSIONS.get(guide_type, "v1")
        lang = self.config.output_language or "en"
        extra = f"{version}:{lang}:{extra_salt}" if extra_salt else f"{version}:{lang}"
        return self._compute_combined_hash(input_files, extra=extra)

    def _should_regenerate(
        self, guide_type: str, input_files: List[str], extra_salt: str = ""
    ) -> bool:
        if self._cache_manager:
            artifact_id = f"guide:{guide_type}"
            input_hash = self._compute_guide_input_hash(input_files, guide_type, extra_salt)
            output_file = self._cache_manager.get_output_file(artifact_id)
            if not self._cache_manager.is_valid(artifact_id, input_hash):
                return True
            if not output_file:
                return True
            output_path = os.path.join(self.working_dir, output_file)
            return not (os.path.exists(output_path) and os.path.getsize(output_path) > 100)
        return self._should_regenerate_legacy(guide_type, input_files, extra_salt)

    def _update_cache(
        self,
        guide_type: str,
        input_files: List[str],
        output_files: List[str],
        extra_salt: str = "",
    ):
        if self._cache_manager:
            artifact_id = f"guide:{guide_type}"
            input_hash = self._compute_guide_input_hash(input_files, guide_type, extra_salt)
            output_path = output_files[0] if output_files else ""
            self._cache_manager.mark_done(
                artifact_id,
                input_hash=input_hash,
                output_path=output_path,
                output_file=os.path.basename(output_path) if output_path else "",
            )
            return
        self._update_cache_legacy(guide_type, input_files, output_files, extra_salt)

    # ── LLM calling with full resilience chain ─────────────────────────

    async def _call_llm_with_fallback(self, prompt: str) -> str:
        """Call LLM with: long-context pre-select → model fallback chain.

        Mirrors the agent framework's resilience pattern:
        1. Pre-select long-context model when prompt exceeds threshold
        2. Otherwise try models in order: main → fallback(s) → long_context
        3. Each model is tried once; retry ownership lives outside the middleware
        """
        from codewiki.src.be.utils import count_tokens

        if self.cancel_token:
            self.cancel_token.check()

        prompt_tokens = count_tokens(prompt)

        # Pre-select: skip straight to long-context model for oversized prompts
        if self.config.long_context_model and prompt_tokens > self.config.long_context_threshold:
            logger.info(
                f"Pre-selecting long-context model {self.config.long_context_model} "
                f"(prompt {prompt_tokens} tokens > threshold {self.config.long_context_threshold})"
            )
            async with self._semaphore:
                result = await with_retry(
                    asyncio.to_thread,
                    self._middleware.call,
                    prompt,
                    model=self.config.long_context_model,
                    max_retries=2,
                    cancel_token=self.cancel_token,
                    on_timeout_use_stream=self._model_supports_stream(
                        self.config.long_context_model
                    ),
                )
                return result.content

        # Build fallback chain: main → fallback(s) → long_context (last resort)
        models = [self.config.main_model]
        if self.config.fallback_model:
            models.extend(n.strip() for n in self.config.fallback_model if n.strip())
        if self.config.long_context_model and self.config.long_context_model not in models:
            models.append(self.config.long_context_model)

        last_exc: Exception | None = None
        for model_name in models:
            try:
                async with self._semaphore:
                    if self.cancel_token:
                        self.cancel_token.check()
                    result = await with_retry(
                        asyncio.to_thread,
                        self._middleware.call,
                        prompt,
                        model=model_name,
                        max_retries=2,
                        cancel_token=self.cancel_token,
                        on_timeout_use_stream=self._model_supports_stream(model_name),
                    )
                    return result.content
            except CancellationError:
                raise
            except LLMRetryExhausted as e:
                logger.warning(f"Guide LLM call exhausted retries with model {model_name}: {e}")
                last_exc = e
                continue
            except LLMError as e:
                logger.warning(f"Guide LLM call failed with model {model_name}: {e}")
                last_exc = e
                if not e.is_retryable:
                    continue
                raise
            except Exception as e:
                logger.warning(f"Guide LLM call failed with model {model_name}: {e}")
                last_exc = e
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Guide LLM fallback chain exhausted without attempting any model")

    # ── File helpers ──────────────────────────────────────────────────

    def _read_file_safe(self, path: str) -> str:
        """Read a file, returning empty string on error."""
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

    @staticmethod
    def _parse_guide_response(response: str) -> str:
        """Extract content from <GUIDE>...</GUIDE> tags."""
        if "<GUIDE>" in response and "</GUIDE>" in response:
            return response.split("<GUIDE>")[1].split("</GUIDE>")[0].strip()
        return response.strip()

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

    # ── Context assembly helpers ──────────────────────────────────────

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
            "requirements.txt",
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "package.json",
            "Cargo.toml",
            "go.mod",
            "pom.xml",
            "build.gradle",
            "Makefile",
            "CMakeLists.txt",
            "Dockerfile",
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
            "codewiki/src/be/main.py",
            "src/main.py",
            "main.py",
            "cli/main.py",
            "app.py",
            "manage.py",
            "__main__.py",
            "src/__main__.py",
            "src/index.ts",
            "src/index.js",
            "index.js",
            "cmd/main.go",
            "main.go",
        ]
        for name in candidates:
            p = os.path.join(self.config.repo_path, name)
            if os.path.exists(p):
                return f"--- {name} ---\n{self._read_file_safe(p)}"
        return ""

    def _find_config_source(self) -> str:
        """Find configuration file/class source."""
        candidates = [
            "codewiki/src/config.py",
            "src/config.py",
            "config.py",
            "src/config.ts",
            "config/settings.py",
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
        parts = ["<RELEVANT_DOCS>"]
        for s in snippets:
            parts.append(f"--- {s.path} ({s.source}) ---\n{s.content}")
        parts.append("</RELEVANT_DOCS>")
        return "\n\n".join(parts)

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

    def _read_component_source(self, comp_ids: List[str], max_tokens: int = 30000) -> str:
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
                            content = content[: remaining * 4] + "\n... (truncated)"
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

    def _find_test_files(self, comp_ids: List[str], max_tokens: int = 15000) -> str:
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
        lines = []
        for cid in comp_ids:
            node = self.components.get(cid)
            if not node:
                continue
            deps = getattr(node, "depends_on", set()) or set()
            for dep in sorted(deps):
                lines.append(f"  {cid} → {dep}")
        return "\n".join(lines) if lines else "(no edges)"

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
    def _build_directory_tree(root: str, max_depth: int = 2) -> str:
        """Build a simple directory tree string."""
        lines = []
        root_p = Path(root)
        excluded = {
            "node_modules",
            ".git",
            "__pycache__",
            ".venv",
            "venv",
            ".tox",
            ".mypy_cache",
            ".pytest_cache",
            "dist",
            "build",
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
        self._results: Dict[str, str] = {}  # guide name → "success" | "FAILED: ..."

        # Collect all available documentation context
        self.docs_bundle = self.collector.collect(
            self.config.repo_path, self.working_dir, self.components
        )

        # Layer 3 quality gate: warn if no MODULE docs were generated
        gen_docs = [
            f
            for f in os.listdir(self.working_dir)
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
        except CancellationError:
            raise
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
            OVERVIEW_AUGMENT_PROMPT,
            format_language_instruction,
        )

        overview_path = os.path.join(self.working_dir, "overview.md")
        existing = self._read_file_safe(overview_path)
        if not existing:
            logger.warning("No overview.md found, skipping augmentation")
            return

        # Build list of successfully generated guides
        guides_list = []
        guide_files = [
            (
                "guide-getting-started.md",
                "Get Started",
                "Quick installation and first-run tutorial",
            ),
            (
                "guide-beginners-guide.md",
                "Beginner's Guide",
                "Accessible multi-chapter walkthrough",
            ),
            (
                "guide-build-and-organization.md",
                "Build & Code Organization",
                "Build pipeline and project structure",
            ),
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

    # ── Guide generators ─────────────────────────────────────────────

    async def generate_getting_started(self):
        """Generate getting-started.md."""
        from codewiki.src.be.prompt_template import (
            GETTING_STARTED_PROMPT,
            format_language_instruction,
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
            "requirements.txt",
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "package.json",
            "Cargo.toml",
            "go.mod",
            "pom.xml",
            "build.gradle",
            "Makefile",
            "CMakeLists.txt",
            "Dockerfile",
        ]
        overview_path = os.path.join(self.working_dir, "overview.md")
        input_files = [p for p in [readme_path] if p]
        input_files.extend(
            os.path.join(self.config.repo_path, n)
            for n in setup_file_names
            if os.path.exists(os.path.join(self.config.repo_path, n))
        )
        # overview.md is excluded from the hash: it is modified by
        # _regenerate_overview() *after* guides are cached, which would cause
        # a hash mismatch on every subsequent run (circular dependency).
        # README + setup files are the real invalidation signals.

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

    async def generate_beginner_guide(self):
        """Generate beginner's guide: outline → serial sections → parent page."""
        from pydantic import BaseModel, Field, ValidationError
        from codewiki.src.be.prompt_template import (
            BEGINNER_OUTLINE_PROMPT,
            BEGINNER_SECTION_PROMPT,
            BEGINNER_PARENT_PROMPT,
            format_language_instruction,
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
        module_tree_str = json.dumps(self._strip_tree_for_display(self.module_tree), indent=2)

        # Hash check: generated module docs + module_tree structure.
        # overview.md is excluded: it is rewritten by _regenerate_overview()
        # after guides are cached, causing a spurious hash mismatch next run.
        # Individual module .md files already cover the same signal.
        gen_doc_files = [
            os.path.join(self.working_dir, f)
            for f in sorted(os.listdir(self.working_dir))
            if f.endswith(".md") and not f.startswith(_GUIDE_PREFIXES) and f != "overview.md"
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
        lang_inst = format_language_instruction(self.config.output_language)
        outline_prompt = BEGINNER_OUTLINE_PROMPT.format(
            repo_name=repo_name,
            module_tree=module_tree_str,
            module_summaries=self._build_module_summaries(),
            language_instruction=lang_inst,
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
        # Pre-build slug map with a fresh local set so Phase C links are consistent
        _used: set = set()
        section_slugs = [
            self._unique_slug(s.id, index=i, used=_used) for i, s in enumerate(sections)
        ]

        output_files = []
        carry_forward = ""

        for i, section in enumerate(sections):
            section_id = section_slugs[i]
            section_title = section.title or f"Part {i + 1}"
            section_summary = section.summary
            focus_modules = section.focus_modules

            # Gather focus module docs
            focus_docs = "\n\n".join(
                self._read_module_doc(m) for m in focus_modules if self._read_module_doc(m)
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

            logger.info(f"  ⌛ Section {i + 1}/{len(sections)}: {section_title}")
            response = await self._call_llm_with_fallback(section_prompt)
            content = self._parse_guide_response(response)

            out_path = self._safe_output_path(f"guide-beginners-guide-{i + 1:02d}-{section_id}.md")
            file_manager.save_text(content, out_path)
            output_files.append(out_path)

            # Build carry-forward summary (first complete paragraph, max ~400 chars)
            paras = content.split("\n\n")
            summary_line = paras[0].replace("\n", " ")
            if len(summary_line) > 400:
                summary_line = summary_line[:400].rsplit(" ", 1)[0] + "..."
            elif len(paras) > 1:
                summary_line += "..."
            carry_forward += f"\n\n### Chapter {i + 1}: {section_title}\n{summary_line}"

            logger.info(f"  ✓ Section {i + 1}/{len(sections)}: {section_title}")

        # ── Phase C: parent page ──────────────────────────────────────
        logger.info("📝 Beginner's Guide — Phase C: parent page")
        # Reuse pre-built section_slugs — no second _unique_slug call here
        chapters_list = "\n".join(
            f"- [{s.title}](guide-beginners-guide-{i + 1:02d}-{section_slugs[i]}.md): {s.summary}"
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
            "beginner_guide",
            gen_doc_files,
            output_files,
            extra_salt=module_tree_hash,
        )
        logger.info(f"✓ Beginner's Guide complete: {len(sections)} sections")

    async def generate_build_analysis(self):
        """Generate build-and-organization.md (multi-language adaptive)."""
        from codewiki.src.be.prompt_template import (
            BUILD_ANALYSIS_PROMPT,
            format_language_instruction,
        )

        output_path = self._safe_output_path("guide-build-and-organization.md")
        repo_name = os.path.basename(os.path.normpath(self.config.repo_path))

        # Collect build files
        build_file_names = [
            "Makefile",
            "GNUmakefile",
            "makefile",
            "CMakeLists.txt",
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "requirements.txt",
            "package.json",
            "tsconfig.json",
            "webpack.config.js",
            "vite.config.ts",
            "Cargo.toml",
            "go.mod",
            "pom.xml",
            "build.gradle",
            "build.gradle.kts",
            "Dockerfile",
            "docker-compose.yml",
            "docker-compose.yaml",
            ".github/workflows/ci.yml",
            ".github/workflows/ci.yaml",
        ]
        # Lock files are auto-generated and bloat the prompt — exclude them
        _LOCK_FILES = {
            "package-lock.json",
            "yarn.lock",
            "pnpm-lock.yaml",
            "Cargo.lock",
            "poetry.lock",
            "Pipfile.lock",
            "go.sum",
        }
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
        component_source_files = sorted(
            {
                getattr(n, "file_path", "")
                for n in self.components.values()
                if getattr(n, "file_path", "")
            }
        )
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
                ".py": "python",
                ".js": "javascript",
                ".mjs": "javascript",
                ".ts": "typescript",
                ".tsx": "typescript",
                ".java": "java",
                ".go": "go",
                ".rs": "rust",
                ".c": "c",
                ".h": "c",
                ".cpp": "cpp",
                ".cc": "cpp",
                ".hpp": "cpp",
                ".cs": "csharp",
                ".php": "php",
            }.get(ext)
            if lang:
                detected.add(lang)

        lang_guides = "\n\n".join(
            _LANG_BUILD_GUIDES[lang] for lang in sorted(detected) if lang in _LANG_BUILD_GUIDES
        )

        # Directory tree (2-level)
        dir_tree = self._build_directory_tree(self.config.repo_path, max_depth=2)

        module_tree_str = json.dumps(self._strip_tree_for_display(self.module_tree), indent=2)

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

    async def generate_algorithm_deepdive(self):
        """Generate core algorithm pages: identify → per-algorithm → parent."""
        from pydantic import BaseModel, Field, ValidationError
        from codewiki.src.be.prompt_template import (
            ALGORITHM_IDENTIFY_PROMPT,
            ALGORITHM_DEEPDIVE_PROMPT,
            ALGORITHM_PARENT_PROMPT,
            format_language_instruction,
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
        source_files = sorted(
            {
                getattr(n, "file_path", "")
                for n in self.components.values()
                if getattr(n, "file_path", "")
            }
        )
        test_files = sorted(
            {t for comp_id in self.components for t in self._find_test_file_paths(comp_id)}
        )
        if not self._should_regenerate("algorithm_deepdive", source_files + test_files):
            logger.info("✓ Core Algorithms pages are up to date (cache hit)")
            return

        logger.info("📝 Generating Core Algorithms — Phase A: identification")

        # ── Phase A: identify algorithms ──────────────────────────────
        lang_inst = format_language_instruction(self.config.output_language)
        id_prompt = ALGORITHM_IDENTIFY_PROMPT.format(
            repo_name=repo_name,
            components_summary=self._build_components_summary(),
            dependency_summary=self._build_dependency_summary(),
            module_summaries=self._build_module_summaries(max_chars_per_module=200),
            language_instruction=lang_inst,
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

        logger.info(f"📝 Core Algorithms — Phase B: generating {len(algorithms)} deep-dives")

        # ── Phase B: per-algorithm deep-dives (parallel, Semaphore) ───
        # Pre-build slug map with a fresh local set so Phase C links are consistent
        _used: set = set()
        algo_slugs = [
            self._unique_slug(a.id, index=i, used=_used) for i, a in enumerate(algorithms)
        ]

        lang_inst = format_language_instruction(self.config.output_language)
        output_files: List[Optional[str]] = [None] * len(algorithms)  # preserve order

        async def _generate_one_algo(idx: int, algo: AlgorithmEntry):
            algo_id = algo_slugs[idx]
            algo_title = algo.title or f"Algorithm {idx + 1}"
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

            logger.info(f"  ⌛ Algorithm {idx + 1}/{len(algorithms)}: {algo_title}")
            response = await self._call_llm_with_fallback(dd_prompt)
            content = self._parse_guide_response(response)
            out_path = self._safe_output_path(f"guide-core-algorithms-{idx + 1:02d}-{algo_id}.md")
            file_manager.save_text(content, out_path)
            output_files[idx] = out_path
            logger.info(f"  ✓ Algorithm {idx + 1}/{len(algorithms)}: {algo_title}")

        await asyncio.gather(*[_generate_one_algo(i, algo) for i, algo in enumerate(algorithms)])
        output_files_filtered = [p for p in output_files if p]

        # ── Phase C: parent page ──────────────────────────────────────
        logger.info("📝 Core Algorithms — Phase C: parent page")
        # Reuse pre-built algo_slugs — no second _unique_slug call here
        algos_list = "\n".join(
            f"- [{a.title}](guide-core-algorithms-{i + 1:02d}-{algo_slugs[i]}.md): {a.summary}"
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
        output_files_filtered.insert(0, parent_path)

        self._update_cache("algorithm_deepdive", source_files + test_files, output_files_filtered)
        logger.info(f"✓ Core Algorithms complete: {len(algorithms)} deep-dives")
