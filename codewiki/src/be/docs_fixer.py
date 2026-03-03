"""
Post-processing fix phase for generated documentation.

Scans all .md files in the docs directory and repairs broken Mermaid diagrams
using the same validate → LLM-repair loop as deepresearch_flow/recognize/mermaid.py.

Validation strategy (in priority order):
  1. mmdc (Mermaid CLI) — accurate, requires `npm i -g @mermaid-js/mermaid-cli`
  2. Regex heuristics   — catches the patterns we know break the Mermaid lexer
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from codewiki.src.be.llm_services import call_llm
from codewiki.src.config import Config

logger = logging.getLogger(__name__)

# ── Mermaid block extraction ───────────────────────────────────────────────────
_MERMAID_BLOCK_RE = re.compile(r"(```\s*mermaid\s*\n)([\s\S]*?)(```)", re.IGNORECASE)

# Unicode math operators that break the Mermaid lexer
_MERMAID_BAD_UNICODE_RE = re.compile(
    r"[∃∀∈∉⊂⊆⊇⊃⊄∧∨∩∪≡≈≠→⇒⇔←⇐≤≥∞∂∇√∫∑∏±×÷]"
)

# Single quote inside a node label bracket  e.g.  A["it's"]  or  B['text']
_MERMAID_SINGLE_QUOTE_RE = re.compile(r'\[["\'](?:[^"\']*\'[^"\']*)+["\'][^]]*]')


# ── mmdc detection ─────────────────────────────────────────────────────────────
_MMDC_PATH: str | None = None
_MMDC_CHECKED = False


def _find_mmdc() -> str | None:
    global _MMDC_PATH, _MMDC_CHECKED
    if _MMDC_CHECKED:
        return _MMDC_PATH
    _MMDC_CHECKED = True
    local = Path.cwd() / "node_modules" / ".bin" / "mmdc"
    if local.exists():
        _MMDC_PATH = str(local)
        return _MMDC_PATH
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
        import shutil as _shutil
        _shutil.rmtree(base, ignore_errors=True)


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
    if abs(open_sq) > 1:
        issues.append(f"Unbalanced square brackets ({open_sq:+d})")
    return issues


# ── LLM repair ─────────────────────────────────────────────────────────────────
_REPAIR_SYSTEM = (
    "You are a Mermaid diagram syntax expert. "
    "Fix the supplied diagram so it parses without errors. "
    "Preserve the logical meaning and structure exactly. "
    "Output ONLY the corrected diagram content — no fences, no explanation."
)

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


def _llm_repair(content: str, issues: list[str], config: Config) -> str:
    """Ask the LLM to fix a broken Mermaid diagram. Returns the fixed content."""
    prompt = _REPAIR_USER.format(
        issues="\n".join(f"- {i}" for i in issues),
        diagram=content.strip(),
    )
    try:
        fixed = call_llm(prompt, config, temperature=0.0)
        # Strip any fences the LLM might have wrapped around its answer
        fixed = re.sub(r"^```\s*mermaid\s*\n?", "", fixed.strip(), flags=re.IGNORECASE)
        fixed = re.sub(r"\n?```\s*$", "", fixed)
        return fixed.strip()
    except Exception as exc:
        logger.warning(f"Mermaid LLM repair failed: {exc}")
        return content


# ── Per-file fixer ──────────────────────────────────────────────────────────────
@dataclass
class FixStats:
    files_scanned: int = 0
    files_with_issues: int = 0
    diagrams_total: int = 0
    diagrams_invalid: int = 0
    diagrams_repaired: int = 0
    diagrams_failed: int = 0


def fix_mermaid_in_file(path: Path, config: Config, stats: FixStats) -> bool:
    """Scan one markdown file and repair broken Mermaid diagrams in-place.

    Returns True if the file was modified.
    """
    text = path.read_text(encoding="utf-8")
    matches = list(_MERMAID_BLOCK_RE.finditer(text))
    if not matches:
        return False

    stats.files_scanned += 1

    replacements: list[tuple[int, int, str]] = []  # (start, end, replacement)
    file_has_issues = False

    for m in matches:
        stats.diagrams_total += 1
        open_fence, content, close_fence = m.group(1), m.group(2), m.group(3)
        start, end = m.start(), m.end()

        # 1. Try mmdc first (authoritative)
        mmdc_err = _validate_with_mmdc(content)
        if mmdc_err is not None:
            issues = [mmdc_err]
        else:
            # 2. Fallback to regex heuristics when mmdc unavailable or passes
            issues = _validate_with_regex(content)

        if not issues:
            continue

        stats.diagrams_invalid += 1
        file_has_issues = True
        approx_line = text[:start].count("\n") + 1
        logger.info(
            f"  🔧 {path.name}:{approx_line} — Mermaid issues: {'; '.join(issues)}"
        )

        fixed = _llm_repair(content, issues, config)

        # Verify the fix with mmdc (if available)
        recheck = _validate_with_mmdc(fixed)
        if recheck is not None and _find_mmdc():
            logger.warning(
                f"  ✗ {path.name}:{approx_line} — repaired diagram still invalid: {recheck}"
            )
            stats.diagrams_failed += 1
            continue

        if fixed == content.strip():
            logger.debug(f"  ⊘ {path.name}:{approx_line} — LLM returned unchanged content")
            stats.diagrams_failed += 1
            continue

        replacements.append((start, end, f"{open_fence}{fixed}\n{close_fence}"))
        stats.diagrams_repaired += 1

    if not replacements:
        return False

    stats.files_with_issues += 1
    # Apply replacements from end → start to preserve offsets
    for start, end, new_block in sorted(replacements, key=lambda x: x[0], reverse=True):
        text = text[:start] + new_block + text[end:]

    path.write_text(text, encoding="utf-8")
    return True


# ── Directory-level entry point ─────────────────────────────────────────────────
def fix_docs(working_dir: str, config: Config) -> FixStats:
    """Scan every .md file in *working_dir* and fix broken Mermaid diagrams.

    This is the hook called at the end of the documentation generation pipeline.
    """
    docs_path = Path(working_dir)
    md_files = sorted(docs_path.glob("*.md"))

    if not md_files:
        return FixStats()

    mmdc_available = _find_mmdc() is not None
    validation_mode = "mmdc" if mmdc_available else "regex heuristics"
    logger.info(
        f"🔧 Mermaid fix phase — {len(md_files)} file(s), validation: {validation_mode}"
    )

    stats = FixStats()
    for md_file in md_files:
        try:
            fix_mermaid_in_file(md_file, config, stats)
        except Exception as exc:
            logger.warning(f"  ✗ Failed to process {md_file.name}: {exc}")

    return stats
