"""
Post-processing fix phase for generated documentation.

Scans all .md files in the docs directory and applies three repair phases:

  Phase 1 — Markdown formatting (mdformat, mechanical, no LLM)
  Phase 2 — Math repair (LaTeX validation + LLM repair)
  Phase 3 — Mermaid repair (mmdc → regex heuristics → LLM repair)

Validation strategy for Mermaid (in priority order):
  1. mmdc (Mermaid CLI) — accurate, requires `npm i -g @mermaid-js/mermaid-cli`
  2. Regex heuristics   — catches the patterns we know break the Mermaid lexer
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import mdformat

from codewiki.src.be.llm_usage import LLMUsageStats
from codewiki.src.be.postprocess.lint_report import LintError, LintReport
from codewiki.src.be.postprocess.math_validator import fix_math_in_text as fix_math
from codewiki.src.be.postprocess.mermaid_validator import (
    _find_mmdc,
    fix_mermaid_in_text as fix_mermaid,
)
from codewiki.src.codewiki_config import CodeWikiConfig

logger = logging.getLogger(__name__)


# ── FixStats ────────────────────────────────────────────────────────────────────
@dataclass
class FixStats:
    # Markdown formatting
    md_files_formatted: int = 0
    # Mermaid
    files_with_mermaid: int = 0
    files_with_issues: int = 0
    diagrams_total: int = 0
    diagrams_invalid: int = 0
    diagrams_repaired: int = 0
    diagrams_failed: int = 0
    # Math
    math_total: int = 0
    math_invalid: int = 0
    math_repaired: int = 0
    math_failed: int = 0


# ── Incremental cache ─────────────────────────────────────────────────────────

_FIX_CACHE_FILENAME = ".fix_docs_cache.json"


def _file_hash(text: str) -> str:
    """Return a 16-char hex digest of the text (SHA-256 truncated)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _load_hash_cache(docs_path: Path) -> dict[str, str]:
    """Load the hash cache from disk; return empty dict on any error."""
    cache_file = docs_path / _FIX_CACHE_FILENAME
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_hash_cache(docs_path: Path, cache: dict[str, str]) -> None:
    """Persist the hash cache to disk."""
    cache_file = docs_path / _FIX_CACHE_FILENAME
    try:
        cache_file.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Could not save fix_docs cache: {exc}")


# ── Phase 1: Markdown formatting ────────────────────────────────────────────────


def _format_markdown(text: str, stats: FixStats) -> str:
    try:
        formatted = mdformat.text(text)
        if formatted != text:
            stats.md_files_formatted += 1
        return formatted
    except Exception as exc:
        logger.debug(f"mdformat skipped: {exc}")
        return text


def _fix_math_in_text(
    text: str,
    config: CodeWikiConfig,
    stats: FixStats,
    usage_stats: LLMUsageStats | None = None,
    report: LintReport | None = None,
    filename: str = "",
) -> str:
    return fix_math(
        text,
        config,
        stats,
        usage_stats,
        report=report,
        filename=filename,
    )


def _fix_mermaid_in_text(
    text: str,
    config: CodeWikiConfig,
    stats: FixStats,
    usage_stats: LLMUsageStats | None = None,
    report: LintReport | None = None,
    filename: str = "",
) -> str:
    return fix_mermaid(
        text,
        config,
        stats,
        usage_stats,
        report=report,
        filename=filename,
    )


# ── Backward-compat wrapper ─────────────────────────────────────────────────────


def fix_mermaid_in_file(
    path: Path,
    config: CodeWikiConfig,
    stats: FixStats,
    usage_stats: LLMUsageStats | None = None,
) -> bool:
    """Scan one markdown file and repair broken Mermaid diagrams in-place.

    Returns True if the file was modified.
    """
    text = path.read_text(encoding="utf-8")
    new_text = _fix_mermaid_in_text(
        text, config, stats, usage_stats, report=None, filename=path.name
    )
    if new_text == text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


# ── Directory-level entry point ─────────────────────────────────────────────────


def fix_docs(
    working_dir: str, config: CodeWikiConfig, usage_stats: LLMUsageStats | None = None
) -> FixStats:
    """Apply all post-processing fix phases to every .md file in *working_dir*.

    Phase 1 — Markdown formatting (mdformat, mechanical) — run in parallel
    Phase 2 — Math repair (LaTeX validation + LLM)
    Phase 3 — Mermaid repair (mmdc/regex + LLM)
    Phase 4 — Link validation (internal links + anchor checks)

    Hash-based incremental skip: if a file's content after Phase 1 matches the
    hash stored from the last successful run, Phases 2 and 3 are skipped.

    A LintReport is populated during phases 2-4 and saved to
    ``_lint_report.json`` in *working_dir*.  When ``config.postprocess.strict``
    is True and the report contains any failures, a :exc:`LintError` is raised
    after the report is saved.
    """
    docs_path = Path(working_dir)
    md_files = sorted(docs_path.glob("*.md"))

    if not md_files:
        return FixStats()

    mmdc_available = _find_mmdc() is not None
    validation_mode = "mmdc" if mmdc_available else "regex heuristics"
    logger.info(
        f"\U0001f527 Post-processing {len(md_files)} file(s): markdown + math + mermaid "
        f"(mermaid validation: {validation_mode})"
    )

    stats = FixStats()
    report = LintReport(total_files=len(md_files))
    hash_cache = _load_hash_cache(docs_path)
    updated_cache: dict[str, str] = {}

    # ── Phase 1: parallel mdformat ────────────────────────────────────────────
    def _phase1(md_file: Path) -> tuple[Path, str, int]:
        """Format one file; returns (path, formatted_text, files_formatted_delta).

        Uses a thread-local FixStats to avoid shared-state races.
        """
        try:
            text = md_file.read_text(encoding="utf-8")
            local_stats = FixStats()
            formatted = _format_markdown(text, local_stats)
            return md_file, formatted, local_stats.md_files_formatted
        except Exception as exc:
            logger.warning(f"  \u2717 Phase 1 failed for {md_file.name}: {exc}")
            try:
                return md_file, md_file.read_text(encoding="utf-8"), 0
            except Exception:
                return md_file, "", 0

    max_workers = min(32, (os.cpu_count() or 1) + 4)
    phase1_results: dict[Path, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for md_file, formatted, delta in executor.map(_phase1, md_files):
            phase1_results[md_file] = formatted
            stats.md_files_formatted += delta  # safe: single-threaded merge

    # ── Phases 2+3: serial, with hash-based skip ─────────────────────────────
    for md_file in md_files:
        try:
            text = phase1_results.get(md_file, "")
            if not text:
                continue

            original_raw = md_file.read_text(encoding="utf-8")

            # Write Phase 1 result if changed
            if text != original_raw:
                md_file.write_text(text, encoding="utf-8")

            # Check incremental cache: skip LLM phases if content unchanged
            cache_key = md_file.name
            current_hash = _file_hash(text)
            if hash_cache.get(cache_key) == current_hash:
                updated_cache[cache_key] = current_hash
                continue  # skip Phases 2+3

            # Phase 2 — Math repair
            text = _fix_math_in_text(
                text, config, stats, usage_stats, report=report, filename=md_file.name
            )

            # Phase 3 — Mermaid repair
            text = _fix_mermaid_in_text(
                text, config, stats, usage_stats, report=report, filename=md_file.name
            )

            if text != original_raw:
                md_file.write_text(text, encoding="utf-8")

            # Store hash of final output so next run can skip
            updated_cache[cache_key] = _file_hash(text)

        except Exception as exc:
            logger.warning(f"  \u2717 Failed to process {md_file.name}: {exc}")

    _save_hash_cache(docs_path, updated_cache)

    # ── Phase 4a: Link rewriting ──────────────────────────────────────────────
    if config.postprocess.fix_links:
        try:
            from codewiki.src.be.postprocess.link_rewriter import rewrite_broken_links

            rewrite_stats = rewrite_broken_links(working_dir)
            if rewrite_stats["rewritten"] or rewrite_stats["removed"]:
                logger.info(
                    f"  \U0001f517 Links: rewrote {rewrite_stats['rewritten']}, "
                    f"removed {rewrite_stats['removed']} broken link(s)"
                )
        except Exception as exc:
            logger.warning(f"Link rewriting failed: {exc}")

    # ── Phase 4b: Link validation ──────────────────────────────────────────────
    try:
        from codewiki.src.be.postprocess.link_validator import validate_links

        link_issues = validate_links(working_dir)
        for issue in link_issues:
            report.link_issues.append(
                {
                    "file": issue.source_file,
                    "line": issue.line_number,
                    "target": issue.target,
                    "issue_type": issue.issue_type,
                }
            )
    except Exception as exc:
        logger.warning(f"Link validation failed: {exc}")

    # ── Save lint report ──────────────────────────────────────────────────────
    try:
        report.save(working_dir)
    except Exception as exc:
        logger.warning(f"Could not save lint report: {exc}")

    if stats.md_files_formatted:
        logger.info(f"  \U0001f4dd Formatted {stats.md_files_formatted} file(s) with mdformat")
    if stats.math_repaired:
        logger.info(
            f"  \u2713 Math: repaired {stats.math_repaired}/{stats.math_invalid} formula(s)"
        )
    if stats.diagrams_repaired:
        logger.info(
            f"  \u2713 Mermaid: repaired {stats.diagrams_repaired}/{stats.diagrams_invalid} diagram(s)"
        )

    # ── Strict gate ───────────────────────────────────────────────────────────
    if config.postprocess.strict and report.has_failures:
        raise LintError(report)

    return stats
