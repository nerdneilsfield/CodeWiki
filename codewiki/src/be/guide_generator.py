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
_GUIDE_PREFIXES = (
    "guide-",
    "_guide_cache", "_parent_doc_hashes", "_tree_cache_meta",
)


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

    # ── File helpers ──────────────────────────────────────────────────

    def _read_file_safe(self, path: str) -> str:
        """Read a file, returning empty string on error."""
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

    @staticmethod
    def _parse_guide_response(response: str) -> str:
        """Extract markdown content from LLM response."""
        if not response:
            return ""
        # Accept content with or without markdown code fence
        if "```markdown" in response:
            parts = response.split("```markdown", 1)
            if len(parts) > 1:
                content = parts[1].split("```", 1)[0]
                return content.strip()
        return response.strip()

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
