"""Mermaid cleanup, validation, and repair helpers for post-processing."""

from __future__ import annotations

import atexit
import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codewiki.src.be.llm_retry import with_retry_sync
from codewiki.src.be.llm_services import call_llm
from codewiki.src.be.llm_usage import LLMUsageStats
from codewiki.src.codewiki_config import CodeWikiConfig, PostprocessConfig

logger = logging.getLogger(__name__)

_MERMAID_BLOCK_RE = re.compile(r"```mermaid\s*\n([\s\S]*?)```", re.IGNORECASE)
_MERMAID_BAD_UNICODE_RE = re.compile(r"[∃∀∈∉⊂⊆⊇⊃⊄∧∨∩∪≡≈≠→⇒⇔←⇐≤≥∞∂∇√∫∑∏±×÷]")
_MERMAID_SINGLE_QUOTE_RE = re.compile(r'\[(?:"|\'|)(?:[^"\']*\'[^"\']*)+(?:"|\'|)[^]]*]')
_NON_ASCII_RE = re.compile(r"[^\x00-\x7F]")

_MMDC_PATH: str | None = None
_MMDC_CHECKED = False

_SMART_QUOTES = str.maketrans(
    {
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": '"',
        "\u2019": '"',
        "\u00ab": '"',
        "\u00bb": '"',
    }
)


@dataclass(frozen=True)
class MermaidSpan:
    start: int
    end: int
    content: str
    line: int
    delimiter: str = "```mermaid"


@dataclass
class MermaidIssue:
    issue_id: str
    span: MermaidSpan
    errors: list[str] = field(default_factory=list)
    cleaned: str = ""


def _bump_stat(stats: Any, name: str, delta: int = 1) -> None:
    current = getattr(stats, name, 0)
    try:
        setattr(stats, name, current + delta)
    except Exception:
        pass


def _record_failure(
    report: Any | None,
    *,
    filename: str,
    block_index: int,
    error: str,
    degraded: bool,
) -> None:
    if report is None:
        return
    failures = getattr(report, "mermaid_failures", None)
    if isinstance(failures, list):
        failures.append(
            {
                "file": filename,
                "block_index": block_index,
                "error": error,
                "degraded": degraded,
            }
        )


def _expand_escaped_newlines(text: str) -> str:
    """Expand literal ``\\n`` with minimal Mermaid awareness."""

    out: list[str] = []
    i = 0
    bracket_depth = 0
    while i < len(text):
        ch = text[i]
        if ch == "[":
            bracket_depth += 1
        elif ch == "]" and bracket_depth > 0:
            bracket_depth -= 1

        if ch == "\\" and i + 1 < len(text) and text[i + 1] == "n":
            out.append("<br/>" if bracket_depth > 0 else "\n")
            i += 2
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def cleanup_mermaid(text: str) -> str:
    """Apply lightweight cleanup rules before validation/repair."""

    cleaned = text.translate(_SMART_QUOTES)
    cleaned = _expand_escaped_newlines(cleaned)

    # Normalize compacted statements first so later rewrites operate line by line.
    cleaned = re.sub(r";\s*(?=[A-Za-z_][A-Za-z0-9_]*\s*(?:-->|---|-.->|==>))", "\n", cleaned)

    # Repair edge labels like A -->[label] B -> A -->|label| B.
    cleaned = re.sub(
        r"(?P<edge>(?:-->|---|-.->|==>))\s*\[(?P<label>[^\]]+)\]",
        lambda m: f"{m.group('edge')}|{m.group('label').strip()}|",
        cleaned,
    )

    # Expand chained edges like A --> B --> C into two statements.
    cleaned = re.sub(
        r"(?P<a>[^\n;]+?)\s*(?P<edge>(?:-->|---|-.->|==>))\s*(?P<b>[^\n;]+?)\s*(?P=edge)\s*(?P<c>[^\n;]+)",
        lambda m: f"{m.group('a').strip()} {m.group('edge')} {m.group('b').strip()}\n{m.group('b').strip()} {m.group('edge')} {m.group('c').strip()}",
        cleaned,
    )

    # Expand multi-source edges like A & B --> C into separate statements.
    def _expand_sources(match: re.Match[str]) -> str:
        sources = [part.strip() for part in match.group("sources").split("&") if part.strip()]
        edge = match.group("edge")
        target = match.group("target").strip()
        if len(sources) <= 1:
            return match.group(0)
        return "\n".join(f"{source} {edge} {target}" for source in sources)

    cleaned = re.sub(
        r"(?P<sources>[^\n;]+?)\s*(?P<edge>(?:-->|---|-.->|==>))\s*(?P<target>[^\n;]+)",
        _expand_sources,
        cleaned,
    )

    # Keep square brackets balanced at a minimum.
    open_sq = cleaned.count("[")
    close_sq = cleaned.count("]")
    if open_sq > close_sq:
        cleaned += "]" * (open_sq - close_sq)

    # Normalize simple HTML-ish labels and cylinder labels.
    cleaned = cleaned.replace("<<", "<").replace(">>", ">")
    cleaned = re.sub(r"\(\s*\[(.*?)\]\s*\)", lambda m: f"([{m.group(1).strip()}])", cleaned)

    # Collapse excessive blank lines introduced by rewrites.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _validate_structure(text: str) -> list[str]:
    """Lightweight structural validation for mermaid text."""
    issues: list[str] = []
    open_sq = 0
    for ch in text:
        if ch == "[":
            open_sq += 1
        elif ch == "]":
            open_sq -= 1
            if open_sq < 0:
                issues.append("Unbalanced square brackets")
                break
    if open_sq > 0:
        issues.append(f"Unbalanced square brackets (+{open_sq})")
    return issues


def extract_mermaid_spans(text: str) -> list[MermaidSpan]:
    spans: list[MermaidSpan] = []
    for match in _MERMAID_BLOCK_RE.finditer(text):
        start = match.start()
        end = match.end()
        line = text.count("\n", 0, start) + 1
        spans.append(MermaidSpan(start=start, end=end, content=match.group(1), line=line))
    return spans


def _find_mmdc() -> str | None:
    global _MMDC_PATH, _MMDC_CHECKED
    if _MMDC_CHECKED:
        return _MMDC_PATH
    _MMDC_CHECKED = True
    _MMDC_PATH = shutil.which("mmdc")
    return _MMDC_PATH


def is_mmdc_available() -> bool:
    return _find_mmdc() is not None


def _cleanup_temp_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def validate_with_mmdc(mmd_text: str) -> str | None:
    """Return None when Mermaid CLI accepts the diagram, else an error string."""

    mmdc = _find_mmdc()
    if not mmdc:
        return None

    base = Path(tempfile.mkdtemp(prefix="codewiki-mmdc-"))
    atexit.register(_cleanup_temp_dir, base)
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
        _cleanup_temp_dir(base)


def _has_unquoted_nonascii(content: str) -> bool:
    cleaned = re.sub(r'"[^"]*"', '""', content)
    cleaned = re.sub(r"'[^']*'", "''", cleaned)
    return bool(_NON_ASCII_RE.search(cleaned))


def validate_with_regex(content: str) -> list[str]:
    issues = _validate_structure(content)
    if _MERMAID_BAD_UNICODE_RE.search(content):
        issues.append("Unicode math operators in labels")
    if _MERMAID_SINGLE_QUOTE_RE.search(content):
        issues.append("Single-quote character inside a node label bracket")
    if _has_unquoted_nonascii(content):
        issues.append("Non-ASCII characters outside quoted strings")
    return issues


def build_repair_prompt(issues: list[MermaidIssue]) -> str:
    payload = {
        "items": [
            {
                "id": issue.issue_id,
                "line": issue.span.line,
                "mermaid": issue.cleaned or issue.span.content,
                "errors": issue.errors,
            }
            for issue in issues
        ]
    }
    return (
        "You are a Mermaid diagram expert.\n"
        "Fix each item so it stays within Mermaid-safe syntax and preserve meaning.\n"
        'Return JSON only with shape {"items": [{"id": "...", "mermaid": "..."}]}.\n'
        "Do not add commentary or fences.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def parse_repair_response(response: str) -> dict[str, str]:
    try:
        data = json.loads(response)
    except Exception:
        return {}

    items = data.get("items")
    if isinstance(items, list):
        result: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            mermaid = item.get("mermaid")
            if isinstance(item_id, str) and isinstance(mermaid, str):
                result[item_id] = mermaid
        return result

    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if isinstance(key, str) and isinstance(value, str):
                result[key] = value
        return result
    return {}


def _build_model_chain(pp_config: PostprocessConfig, main_model: str) -> list[str]:
    chain = [
        (pp_config.repair_model or main_model).strip(),
        pp_config.repair_fallback_1.strip(),
        pp_config.repair_fallback_2.strip(),
    ]
    return [name for name in chain if name]


def repair_batch_sync(
    issues: list[MermaidIssue],
    config: CodeWikiConfig,
    pp_config: PostprocessConfig,
    usage_stats: LLMUsageStats | None = None,
) -> dict[str, str]:
    if not issues:
        return {}

    prompt = build_repair_prompt(issues)
    expected_ids = {issue.issue_id for issue in issues}
    merged: dict[str, str] = {}
    for model_name in _build_model_chain(pp_config, config.main_model):
        try:
            result = with_retry_sync(
                call_llm,
                prompt,
                config,
                model=model_name,
                temperature=0.0,
                max_retries=pp_config.repair_max_retries,
            )
            content = getattr(result, "content", result)
            if usage_stats and getattr(result, "usage", None):
                usage = result.usage
                usage_stats.record(
                    getattr(result, "model", model_name),
                    getattr(usage, "input_tokens", 0) or 0,
                    getattr(usage, "output_tokens", 0) or 0,
                )
            parsed = parse_repair_response(content if isinstance(content, str) else str(content))
            if not parsed:
                continue
            merged.update({key: value for key, value in parsed.items() if key in expected_ids})
            if expected_ids.issubset(merged):
                break
        except Exception as exc:
            logger.warning("Mermaid batch repair failed with %s: %s", model_name, exc)
            continue
    return merged


def _apply_replacements(text: str, replacements: list[tuple[int, int, str]]) -> str:
    for start, end, new_text in sorted(replacements, key=lambda item: item[0], reverse=True):
        text = text[:start] + new_text + text[end:]
    return text


def _chunked(items: list[MermaidIssue], size: int) -> list[list[MermaidIssue]]:
    if size <= 0:
        return [items]
    return [items[i : i + size] for i in range(0, len(items), size)]


def fix_mermaid_in_text(
    text: str,
    config: CodeWikiConfig,
    stats: Any,
    usage_stats: LLMUsageStats | None = None,
    report: Any | None = None,
    filename: str = "",
) -> str:
    """Validate and repair Mermaid blocks embedded in markdown text."""

    spans = extract_mermaid_spans(text)
    if not spans:
        return text

    pp_config = getattr(config, "postprocess", PostprocessConfig())
    _bump_stat(stats, "files_with_mermaid")

    mmdc_available = _find_mmdc() is not None
    issues: list[MermaidIssue] = []
    replacements: list[tuple[int, int, str]] = []
    file_has_issues = False

    for block_index, span in enumerate(spans):
        _bump_stat(stats, "diagrams_total")
        raw = span.content.rstrip("\n")
        cleaned = cleanup_mermaid(raw)

        if mmdc_available:
            validation_error = validate_with_mmdc(cleaned)
            validation_issues = [validation_error] if validation_error else []
        else:
            validation_issues = validate_with_regex(cleaned)

        if not validation_issues:
            continue

        file_has_issues = True
        _bump_stat(stats, "diagrams_invalid")
        issue = MermaidIssue(
            issue_id=f"{filename or 'mermaid'}:{block_index}",
            span=MermaidSpan(
                start=span.start,
                end=span.end,
                content=raw,
                line=span.line,
                delimiter=span.delimiter,
            ),
            errors=validation_issues,
            cleaned=cleaned,
        )
        issues.append(issue)

    if file_has_issues:
        _bump_stat(stats, "files_with_issues")

    if not issues:
        return text

    repaired: dict[str, str] = {}
    try:
        batch_size = int(getattr(pp_config, "repair_batch_size", 0) or 0)
    except Exception:
        batch_size = 0
    for batch in _chunked(issues, batch_size or len(issues)):
        repaired.update(repair_batch_sync(batch, config, pp_config, usage_stats))

    for issue in issues:
        span = issue.span
        candidate = repaired.get(issue.issue_id, issue.cleaned or span.content)
        candidate = cleanup_mermaid(candidate)

        if mmdc_available:
            recheck = validate_with_mmdc(candidate)
            still_bad = recheck is not None
            error_msg = recheck or "; ".join(issue.errors)
        else:
            recheck = validate_with_regex(candidate)
            still_bad = bool(recheck)
            error_msg = "; ".join(recheck or issue.errors)

        if still_bad or candidate.strip() == (issue.cleaned or span.content).strip():
            _bump_stat(stats, "diagrams_failed")
            _record_failure(
                report,
                filename=filename,
                block_index=int(issue.issue_id.rsplit(":", 1)[-1]),
                error=error_msg,
                degraded=config.postprocess.degrade_mermaid,
            )
            if config.postprocess.degrade_mermaid:
                degraded = (
                    "```text\n"
                    "[MERMAID DIAGRAM - RENDER FAILED]\n"
                    f"{span.content.strip()}\n"
                    "```\n"
                    f"<!-- mermaid-error: {error_msg} -->"
                )
                replacements.append((span.start, span.end, degraded))
            # else: keep original mermaid block, let browser-side renderer try
            continue

        _bump_stat(stats, "diagrams_repaired")
        replacements.append((span.start, span.end, f"```mermaid\n{candidate}\n```"))

    return _apply_replacements(text, replacements)
