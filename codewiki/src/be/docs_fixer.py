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
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import mdformat

from codewiki.src.be.llm_services import call_llm
from codewiki.src.be.llm_retry import with_retry_sync
from codewiki.src.be.llm_usage import LLMUsageStats
from codewiki.src.be.postprocess.lint_report import LintError, LintReport
from codewiki.src.codewiki_config import CodeWikiConfig

logger = logging.getLogger(__name__)


# ── Math block extraction ───────────────────────────────────────────────────────
_MATH_DISPLAY_RE = re.compile(r"(\$\$)([\s\S]+?)(\$\$)", re.DOTALL)
_MATH_INLINE_RE = re.compile(r"(?<!\$)(\$)(?!\s)([^$\n]+?)(\$)(?!\$)")
_MATH_BK_DISP_RE = re.compile(r"(\\\[)([\s\S]+?)(\\\])", re.DOTALL)
_MATH_BK_INLN_RE = re.compile(r"(\\\()(.+?)(\\\))")

# ── Math validation helpers ────────────────────────────────────────────────────
# Combined begin/end pattern for stack-based nesting validation
_MATH_ENV_RE = re.compile(r"\\(begin|end)\{([^}]+)\}")

# ── Code block masking (protect fenced/inline code from math regex) ───────────
# Covers backtick fences (``` and ~~~) and inline code.
# Indented code blocks (4-space) are not covered — too complex without full MD parsing.
_CODE_FENCE_RE = re.compile(r"(~~~[\s\S]*?~~~|```[\s\S]*?```|`[^`\n]+`)", re.DOTALL)

# ── Mermaid block extraction ───────────────────────────────────────────────────
_MERMAID_BLOCK_RE = re.compile(r"(```\s*mermaid\s*\n)([\s\S]*?)(```)", re.IGNORECASE)

# Unicode math operators that break the Mermaid lexer
_MERMAID_BAD_UNICODE_RE = re.compile(r"[∃∀∈∉⊂⊆⊇⊃⊄∧∨∩∪≡≈≠→⇒⇔←⇐≤≥∞∂∇√∫∑∏±×÷]")

# Single quote inside a node label bracket  e.g.  A["it's"]  or  B['text']
_MERMAID_SINGLE_QUOTE_RE = re.compile(r'\[["\'](?:[^"\']*\'[^"\']*)+["\'][^]]*]')


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


# ── Phase 2: Math repair ────────────────────────────────────────────────────────


def _validate_math(content: str) -> list[str]:
    """Check a LaTeX formula for structural errors. Returns list of issue strings."""
    issues: list[str] = []
    # Unmatched curly braces.
    # A brace is escaped only when preceded by an ODD number of backslashes,
    # e.g. \{ is escaped but \\{ is a real brace (escaped backslash + real brace).
    depth = 0
    backslash_run = 0
    for ch in content:
        if ch == "\\":
            backslash_run += 1
            continue
        if ch == "{" and backslash_run % 2 == 0:
            depth += 1
        elif ch == "}" and backslash_run % 2 == 0:
            depth -= 1
        backslash_run = 0
        if depth < 0:
            issues.append("Unmatched closing brace '}'")
            break
    if depth > 0:
        issues.append(f"Unmatched opening brace(s) ({depth})")
    # Mismatched \begin/\end environments — stack-based to handle nesting
    env_stack: list[str] = []
    for m in _MATH_ENV_RE.finditer(content):
        tag, env = m.group(1), m.group(2)
        if tag == "begin":
            env_stack.append(env)
        else:
            if not env_stack:
                issues.append(f"Unmatched \\end{{{env}}}")
            elif env_stack[-1] != env:
                issues.append(f"Mismatched \\end{{{env}}} (expected \\end{{{env_stack[-1]}}})")
                env_stack.pop()
            else:
                env_stack.pop()
    if env_stack:
        issues.append(f"Unclosed environments: {', '.join(env_stack)}")
    return issues


_MATH_REPAIR_USER = """\
You are a LaTeX math expert. Fix the supplied formula so it parses without errors.
Preserve the mathematical meaning exactly.
Output ONLY the corrected formula — no delimiters, no explanation.

The following LaTeX formula contains syntax errors.

Issues:
{issues}

Formula to fix:
{formula}

Return ONLY the corrected formula content (no $, $$, \\[, \\( delimiters, no commentary).
"""


def _llm_repair_math(
    content: str,
    issues: list[str],
    config: CodeWikiConfig,
    usage_stats: LLMUsageStats | None = None,
) -> str:
    """Ask the LLM to fix a broken LaTeX formula. Returns the fixed content."""
    prompt = _MATH_REPAIR_USER.format(
        issues="\n".join(f"- {i}" for i in issues),
        formula=content.strip(),
    )
    try:
        result = with_retry_sync(call_llm, prompt, config, temperature=0.0, max_retries=1)
        if usage_stats and result.usage:
            usage_stats.record(
                result.model,
                result.usage.input_tokens,
                result.usage.output_tokens,
            )
        return result.content.strip()
    except Exception as exc:
        logger.warning(f"Math LLM repair failed: {exc}")
        return content


def _fix_math_in_text(
    text: str,
    config: CodeWikiConfig,
    stats: FixStats,
    usage_stats: LLMUsageStats | None = None,
    report: LintReport | None = None,
    filename: str = "",
) -> str:
    """Validate and repair LaTeX math blocks in *text*. Returns updated text."""
    # Step 1: Mask fenced/inline code blocks so math regex never fires inside them
    code_blocks: list[str] = []

    def _mask_code(m: re.Match) -> str:
        code_blocks.append(m.group(0))
        return f"\x00CODE{len(code_blocks) - 1}\x00"

    masked = _CODE_FENCE_RE.sub(_mask_code, text)

    # Step 2: Mask escaped dollar signs (\$) so they are never treated as math
    # delimiters.  We use str.split instead of a regex because Python's re
    # module treats \$ as a literal-dollar pattern (not backslash+dollar),
    # making regex-based matching of the 2-char sequence unreliable.
    _ESC_DOLLAR = "\\$"  # the 2-char sequence: backslash then dollar
    esc_dollar_parts = masked.split(_ESC_DOLLAR)
    esc_dollars_count = len(esc_dollar_parts) - 1
    if esc_dollars_count:
        new_parts = [esc_dollar_parts[0]]
        for i in range(esc_dollars_count):
            new_parts.append(f"\x00ESCD{i}\x00")
            new_parts.append(esc_dollar_parts[i + 1])
        masked = "".join(new_parts)

    # Display-math patterns use group(1) as open delimiter ($$, \[)
    _DISPLAY_PATTERNS = {id(_MATH_DISPLAY_RE), id(_MATH_BK_DISP_RE)}

    def _try_fix(m: re.Match, pattern: re.Pattern) -> str:
        open_delim = m.group(1)
        content = m.group(2)
        close_delim = m.group(3)
        stats.math_total += 1
        issues = _validate_math(content)
        if not issues:
            return m.group(0)
        stats.math_invalid += 1
        fixed = _llm_repair_math(content, issues, config, usage_stats)
        if fixed == content.strip():
            stats.math_failed += 1
            error_msg = "; ".join(issues)
            if report is not None:
                report.math_failures.append(
                    {
                        "file": filename,
                        "expression": content.strip()[:120],
                        "error": error_msg,
                        "degraded": True,
                    }
                )
            is_display = id(pattern) in _DISPLAY_PATTERNS
            if is_display:
                return f"```latex\n{content.strip()}\n```\n<!-- math-error: {error_msg} -->"
            return f"`{content.strip()}` <!-- math-error: {error_msg} -->"
        recheck = _validate_math(fixed)
        if recheck:
            logger.warning(f"    \u2717 Math repair introduced new issues: {'; '.join(recheck)}")
            stats.math_failed += 1
            error_msg = "; ".join(recheck)
            if report is not None:
                report.math_failures.append(
                    {
                        "file": filename,
                        "expression": content.strip()[:120],
                        "error": error_msg,
                        "degraded": True,
                    }
                )
            is_display = id(pattern) in _DISPLAY_PATTERNS
            if is_display:
                return f"```latex\n{content.strip()}\n```\n<!-- math-error: {error_msg} -->"
            return f"`{content.strip()}` <!-- math-error: {error_msg} -->"
        stats.math_repaired += 1
        return f"{open_delim}{fixed}{close_delim}"

    for pattern in (_MATH_DISPLAY_RE, _MATH_BK_DISP_RE, _MATH_INLINE_RE, _MATH_BK_INLN_RE):
        masked = pattern.sub(lambda m, p=pattern: _try_fix(m, p), masked)

    # Restore escaped dollar signs, then code blocks
    if esc_dollars_count:
        for i in range(esc_dollars_count):
            masked = masked.replace(f"\x00ESCD{i}\x00", _ESC_DOLLAR)
    return re.sub(r"\x00CODE(\d+)\x00", lambda m: code_blocks[int(m.group(1))], masked)


# ── Phase 3: Mermaid repair ─────────────────────────────────────────────────────

# ── mmdc detection ─────────────────────────────────────────────────────────────
_MMDC_PATH: str | None = None
_MMDC_CHECKED = False


def _find_mmdc() -> str | None:
    global _MMDC_PATH, _MMDC_CHECKED
    if _MMDC_CHECKED:
        return _MMDC_PATH
    _MMDC_CHECKED = True
    _MMDC_PATH = shutil.which("mmdc")
    return _MMDC_PATH


def _validate_with_mmdc(mmd_text: str) -> str | None:
    """Return None if valid, or an error message string."""
    mmdc = _find_mmdc()
    if not mmdc:
        return None  # can't validate — treat as OK
    base = Path(tempfile.mkdtemp(prefix="cwiki-mmdc-"))
    try:
        in_file = base / "diagram.mmd"
        out_file = base / "diagram.svg"
        in_file.write_text(mmd_text, encoding="utf-8")
        proc = subprocess.run(
            [mmdc, "-i", str(in_file), "-o", str(out_file), "--quiet"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        if proc.returncode == 0 and out_file.exists() and out_file.stat().st_size > 0:
            return None
        msg = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
        return msg or f"mmdc failed (code {proc.returncode})"
    except subprocess.TimeoutExpired:
        return "mmdc timed out"
    except Exception as exc:
        return str(exc)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _has_unquoted_nonascii(content: str) -> bool:
    """Return True if *content* has non-ASCII characters outside of quoted strings."""
    # Temporarily strip all double/single-quoted strings, then look for non-ASCII
    cleaned = re.sub(r'"[^"]*"', '""', content)
    cleaned = re.sub(r"'[^']*'", "''", cleaned)
    return bool(re.search(r"[^\x00-\x7F]", cleaned))


def _validate_with_regex(content: str) -> list[str]:
    """Regex-based heuristics — catches known Mermaid-breaking patterns."""
    issues: list[str] = []
    if _MERMAID_BAD_UNICODE_RE.search(content):
        issues.append(
            "Unicode math operators in labels (∃ ∀ ∈ ⊂ ∧ ∨ …) — "
            "use plain-text equivalents (exists, forall, in, subset, …)"
        )
    if _MERMAID_SINGLE_QUOTE_RE.search(content):
        issues.append("Single-quote character inside a node label bracket")
    open_sq = content.count("[") - content.count("]")
    if open_sq != 0:
        issues.append(f"Unbalanced square brackets ({open_sq:+d})")
    if _has_unquoted_nonascii(content):
        issues.append(
            "Non-ASCII characters (e.g. Chinese/CJK) outside quoted strings — "
            'wrap node labels in double quotes, e.g. A["中文"]'
        )
    return issues


_REPAIR_USER = """\
The following Mermaid diagram contains syntax errors.

Issues:
{issues}

Diagram to fix:
```mermaid
{diagram}
```

Return ONLY the corrected diagram content (no ``` fences, no commentary).
"""


def _llm_repair(
    content: str,
    issues: list[str],
    config: CodeWikiConfig,
    usage_stats: LLMUsageStats | None = None,
) -> str:
    """Ask the LLM to fix a broken Mermaid diagram. Returns the fixed content."""
    prompt = _REPAIR_USER.format(
        issues="\n".join(f"- {i}" for i in issues),
        diagram=content.strip(),
    )
    try:
        result = with_retry_sync(call_llm, prompt, config, temperature=0.0, max_retries=1)
        if usage_stats and result.usage:
            usage_stats.record(
                result.model,
                result.usage.input_tokens,
                result.usage.output_tokens,
            )
        fixed = result.content
        # Strip any fences the LLM might have wrapped around its answer
        fixed = re.sub(r"^```\s*mermaid\s*\n?", "", fixed.strip(), flags=re.IGNORECASE)
        fixed = re.sub(r"\n?```\s*$", "", fixed)
        return fixed.strip()
    except Exception as exc:
        logger.warning(f"Mermaid LLM repair failed: {exc}")
        return content


def _fix_mermaid_in_text(
    text: str,
    config: CodeWikiConfig,
    stats: FixStats,
    usage_stats: LLMUsageStats | None = None,
    report: LintReport | None = None,
    filename: str = "",
) -> str:
    """Validate and repair Mermaid diagrams embedded in *text*. Returns updated text."""
    matches = list(_MERMAID_BLOCK_RE.finditer(text))
    if not matches:
        return text

    stats.files_with_mermaid += 1

    # Cache mmdc availability once per call to avoid redundant shutil.which lookups
    mmdc_available = _find_mmdc() is not None

    replacements: list[tuple[int, int, str]] = []
    file_has_issues = False  # any diagram in this file was invalid

    for block_index, m in enumerate(matches):
        stats.diagrams_total += 1
        open_fence, content, close_fence = m.group(1), m.group(2), m.group(3)
        start, end = m.start(), m.end()

        # Validation: mmdc is authoritative when available; regex is the fallback.
        # _validate_with_mmdc returns None for BOTH "unavailable" and "valid",
        # so we must gate on mmdc_available to avoid running regex on valid diagrams.
        if mmdc_available:
            mmdc_err = _validate_with_mmdc(content)
            issues = [mmdc_err] if mmdc_err is not None else []
        else:
            issues = _validate_with_regex(content)

        if not issues:
            continue

        stats.diagrams_invalid += 1
        file_has_issues = True
        approx_line = text[:start].count("\n") + 1
        logger.info(f"    \U0001f527 Mermaid line ~{approx_line} \u2014 {'; '.join(issues)}")

        fixed = _llm_repair(content, issues, config, usage_stats)

        # Verify the fix with the same validator used for detection
        repair_failed = False
        error_msg = "; ".join(issues)
        if mmdc_available:
            recheck = _validate_with_mmdc(fixed)
            if recheck is not None:
                logger.warning(
                    f"    \u2717 Mermaid line ~{approx_line} \u2014 repaired diagram still invalid: {recheck}"
                )
                stats.diagrams_failed += 1
                error_msg = recheck
                repair_failed = True
        else:
            regex_recheck = _validate_with_regex(fixed)
            if regex_recheck:
                logger.warning(
                    f"    \u2717 Mermaid line ~{approx_line} \u2014 regex recheck failed after repair: "
                    f"{'; '.join(regex_recheck)}"
                )
                stats.diagrams_failed += 1
                error_msg = "; ".join(regex_recheck)
                repair_failed = True

        if not repair_failed and fixed == content.strip():
            logger.debug(
                f"    \u2298 Mermaid line ~{approx_line} \u2014 LLM returned unchanged content"
            )
            stats.diagrams_failed += 1
            repair_failed = True

        if repair_failed:
            if report is not None:
                report.mermaid_failures.append(
                    {
                        "file": filename,
                        "block_index": block_index,
                        "error": error_msg,
                        "degraded": True,
                    }
                )
            degraded_block = (
                f"```text\n"
                f"[MERMAID DIAGRAM - RENDER FAILED]\n"
                f"{content.strip()}\n"
                f"```\n"
                f"<!-- mermaid-error: {error_msg} -->"
            )
            replacements.append((start, end, degraded_block))
            continue

        replacements.append((start, end, f"{open_fence}{fixed}\n{close_fence}"))
        stats.diagrams_repaired += 1

    # files_with_issues counts files where any invalid diagram was FOUND,
    # regardless of whether repair succeeded (so failed repairs are also counted).
    if file_has_issues:
        stats.files_with_issues += 1

    if not replacements:
        return text

    # Apply replacements from end → start to preserve offsets
    for start, end, new_block in sorted(replacements, key=lambda x: x[0], reverse=True):
        text = text[:start] + new_block + text[end:]

    return text


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
    ``_lint_report.json`` in *working_dir*.  When ``config.postprocess_strict``
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
    if getattr(config, "postprocess_fix_links", True):
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
    if getattr(config, "postprocess_strict", False) and report.has_failures:
        raise LintError(report)

    return stats
