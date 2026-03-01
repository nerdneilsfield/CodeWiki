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
