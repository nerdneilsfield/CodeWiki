"""Math extraction, validation, and batch repair helpers."""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from codewiki.src.be.cache_manager import CacheManager
from pylatexenc.latexwalker import LatexWalker

from codewiki.src.be.llm_middleware import LLMMiddleware
from codewiki.src.be.llm_retry import with_retry_sync
from codewiki.src.be.llm_usage import LLMUsageStats
from codewiki.src.codewiki_config import CodeWikiConfig, PostprocessConfig

logger = logging.getLogger(__name__)

_CODE_FENCE_RE = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_DISPLAY_DOLLAR_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_DISPLAY_BRACKET_RE = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)
_INLINE_PAREN_RE = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
_INLINE_DOLLAR_RE = re.compile(r"(?<!\$)\$(?!\s)([^$\n]+?)(?<!\$)\$(?!\$)")

_KATEX_HELPER = Path(__file__).with_name("katex_check.js")
REPAIR_CACHE_DIR = "_repair_cache"


@dataclass(frozen=True)
class FormulaSpan:
    start: int
    end: int
    delimiter: str
    content: str
    line: int

    @property
    def display_mode(self) -> bool:
        return self.delimiter in {"$$", r"\["}


@dataclass
class FormulaIssue:
    issue_id: str
    span: FormulaSpan
    errors: list[str]
    cleaned: str


def cleanup_formula(text: str) -> str:
    """Apply small repair heuristics to common LLM math glitches."""
    cleaned = text
    cleaned = cleaned.replace("\x08eta", r"\beta")
    cleaned = cleaned.replace("\x08", r"\b")
    cleaned = re.sub(r"\\left\s+ceil\b", r"\\left\\lceil", cleaned)
    cleaned = re.sub(r"\\right\s+ceil\b", r"\\right\\rceil", cleaned)
    cleaned = re.sub(r"\\left\s+floor\b", r"\\left\\lfloor", cleaned)
    cleaned = re.sub(r"\\right\s+floor\b", r"\\right\\rfloor", cleaned)
    cleaned = re.sub(r"\^([A-Za-z0-9])\^([A-Za-z0-9])", r"^{\1}^{\2}", cleaned)
    return cleaned


def _validate_braces(text: str) -> list[str]:
    errors: list[str] = []
    depth = 0
    backslash_run = 0
    for ch in text:
        if ch == "\\":
            backslash_run += 1
            continue
        if ch == "{" and backslash_run % 2 == 0:
            depth += 1
        elif ch == "}" and backslash_run % 2 == 0:
            depth -= 1
            if depth < 0:
                errors.append("unmatched closing brace")
                return errors
        backslash_run = 0
    if depth > 0:
        errors.append("unmatched opening brace")
    return errors


def _validate_pylatex(text: str) -> list[str]:
    errors: list[str] = []
    try:
        LatexWalker(text).get_latex_nodes()
    except Exception as exc:  # pragma: no cover - parser exceptions are version-specific
        errors.append(str(exc))
    errors.extend(_validate_braces(text))
    return errors


class NodeKatexValidator:
    """Persistently validate formulas with a Node subprocess."""

    def __init__(self, script_path: Path | None = None):
        self.script_path = script_path or _KATEX_HELPER
        self._lock = RLock()
        self._proc: subprocess.Popen[str] | None = None
        self._unavailable = False
        atexit.register(self.close)

    def close(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            if not proc:
                return
            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=1)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

    def _spawn(self) -> bool:
        if self._unavailable:
            return False
        if self._proc and self._proc.poll() is None:
            return True
        try:
            self._proc = subprocess.Popen(
                ["node", str(self.script_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except Exception as exc:
            logger.debug("KaTeX validator unavailable: %s", exc)
            self._proc = None
            self._unavailable = True
            return False
        return True

    def _call_once(self, latex: str, opts: dict[str, Any]) -> tuple[bool, str | None]:
        if not self._spawn():
            return False, None
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            return False, None
        if proc.poll() is not None:
            self._proc = None
            return False, None
        payload = json.dumps({"latex": latex, "opts": opts}, ensure_ascii=False)
        try:
            proc.stdin.write(payload + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            if not line:
                stderr = ""
                if proc.stderr is not None:
                    stderr = proc.stderr.read() or ""
                logger.debug("KaTeX validator exited: %s", stderr.strip())
                self._proc = None
                return False, None
            data = json.loads(line)
        except Exception as exc:
            logger.debug("KaTeX validator communication failed: %s", exc)
            self._proc = None
            return False, None
        if data.get("ok"):
            return True, None
        return True, str(data.get("error") or "KaTeX validation failed")

    def check(self, latex: str, opts: dict[str, Any]) -> str | None:
        with self._lock:
            ok, error = self._call_once(latex, opts)
            if ok or error is not None or self._unavailable:
                return error
            # If the process died before replying, try one respawn.
            ok, error = self._call_once(latex, opts)
            if ok:
                return error
            return error


_KATEX_VALIDATOR = NodeKatexValidator()


def _validate_katex(text: str, display_mode: bool) -> str | None:
    return _KATEX_VALIDATOR.check(
        text,
        {
            "displayMode": display_mode,
            "strict": "warn",
        },
    )


def validate_formula(text: str, display_mode: bool) -> list[str]:
    """Validate a formula with structural checks plus KaTeX rendering."""
    errors = _validate_pylatex(text)
    katex_error = _validate_katex(text, display_mode)
    if katex_error:
        errors.append(katex_error)
    return errors


def _mask_preserve_length(text: str, pattern: re.Pattern[str]) -> str:
    chars = list(text)
    for match in pattern.finditer(text):
        for idx in range(match.start(), match.end()):
            if chars[idx] != "\n":
                chars[idx] = " "
    return "".join(chars)


def extract_math_spans(text: str) -> list[FormulaSpan]:
    """Extract inline and display math spans while masking code blocks."""
    masked = _mask_preserve_length(text, _CODE_FENCE_RE)
    masked = _mask_preserve_length(masked, _INLINE_CODE_RE)
    escaped_parts = masked.split(r"\$")
    if len(escaped_parts) > 1:
        masked = "  ".join(escaped_parts)

    spans: list[FormulaSpan] = []
    patterns: list[tuple[re.Pattern[str], str]] = [
        (_DISPLAY_DOLLAR_RE, "$$"),
        (_DISPLAY_BRACKET_RE, r"\["),
        (_INLINE_PAREN_RE, r"\("),
        (_INLINE_DOLLAR_RE, "$"),
    ]
    for pattern, delimiter in patterns:
        for match in pattern.finditer(masked):
            start, end = match.span()
            content = text[match.start(1) : match.end(1)]
            spans.append(
                FormulaSpan(
                    start=start,
                    end=end,
                    delimiter=delimiter,
                    content=content,
                    line=text[:start].count("\n") + 1,
                )
            )

    spans.sort(key=lambda span: (span.start, span.end))
    return spans


def build_repair_prompt(issues: list[FormulaIssue]) -> str:
    payload = {
        "instruction": (
            "Repair each LaTeX formula and return JSON only. "
            "Preserve the original math meaning and keep replacements minimal."
        ),
        "items": [
            {
                "id": issue.issue_id,
                "latex": issue.cleaned,
                "errors": issue.errors,
                "delimiter": issue.span.delimiter,
                "line": issue.span.line,
            }
            for issue in issues
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def parse_repair_response(response: str) -> dict[str, str]:
    response = response.strip()
    response = re.sub(r"^```(?:json)?\s*", "", response, flags=re.IGNORECASE)
    response = re.sub(r"\s*```$", "", response)
    try:
        data = json.loads(response)
    except Exception:
        return {}
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            result: dict[str, str] = {}
            for item in data["items"]:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id", "")).strip()
                latex = item.get("latex")
                if item_id and isinstance(latex, str):
                    result[item_id] = latex
            return result
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[str(key)] = value
        return result
    return {}


def _build_model_chain(pp_config: PostprocessConfig, main_model: str) -> list[str]:
    chain = [
        pp_config.repair_model or main_model,
        pp_config.repair_fallback_1,
        pp_config.repair_fallback_2,
    ]
    return [model for model in chain if model]


def repair_batch_sync(
    issues: list[FormulaIssue],
    config: CodeWikiConfig,
    pp_config: PostprocessConfig,
    usage_stats: LLMUsageStats | None = None,
    middleware: LLMMiddleware | None = None,
) -> dict[str, str]:
    if not issues:
        return {}
    best: dict[str, str] = {}
    expected_ids = {issue.issue_id for issue in issues}
    prompt = build_repair_prompt(issues)

    llm = middleware or LLMMiddleware(config, usage_stats=usage_stats)
    for model in _build_model_chain(pp_config, config.main_model):
        try:
            result = with_retry_sync(
                llm.call,
                prompt,
                model=model,
                temperature=0.0,
                max_retries=pp_config.repair_max_retries,
            )
        except Exception as exc:
            logger.debug("math repair call failed for %s: %s", model, exc)
            continue
        if usage_stats and result.usage:
            usage_stats.record(
                result.model or model,
                result.usage.input_tokens,
                result.usage.output_tokens,
            )
        parsed = parse_repair_response(result.content)
        if not parsed:
            continue
        candidate = {key: value for key, value in parsed.items() if key in expected_ids}
        if not candidate:
            continue
        merged = dict(best)
        merged.update(candidate)
        if len(merged) > len(best):
            best = merged
        if expected_ids.issubset(best):
            break
    return best


def _closing_delimiter(delimiter: str) -> str:
    if delimiter == "$$":
        return "$$"
    if delimiter == "$":
        return "$"
    if delimiter == r"\[":
        return r"\]"
    if delimiter == r"\(":
        return r"\)"
    return delimiter


def _repair_cache_path(cache_dir: str, repair_id: str) -> str:
    target_dir = os.path.join(cache_dir, REPAIR_CACHE_DIR)
    os.makedirs(target_dir, exist_ok=True)
    safe_name = repair_id.replace(":", "_").replace("/", "_") + ".txt"
    return os.path.join(target_dir, safe_name)


def _set_stat(stats: Any, name: str, delta: int = 1) -> None:
    if stats is None or not hasattr(stats, name):
        return
    setattr(stats, name, getattr(stats, name) + delta)


def _format_failed_formula(span: FormulaSpan, error_msg: str) -> str:
    if span.display_mode:
        return f"```latex\n{span.content.strip()}\n```\n<!-- math-error: {error_msg} -->"
    return f"`{span.content.strip()}` <!-- math-error: {error_msg} -->"


def fix_math_in_text(
    text: str,
    config: CodeWikiConfig,
    stats: Any,
    usage_stats: LLMUsageStats | None = None,
    middleware: LLMMiddleware | None = None,
    cache_manager: CacheManager | None = None,
    report: Any | None = None,
    filename: str = "",
) -> str:
    spans = extract_math_spans(text)
    if not spans:
        return text

    _set_stat(stats, "math_total", len(spans))

    cleaned_by_id: dict[str, str] = {}
    issues: list[FormulaIssue] = []
    for index, span in enumerate(spans):
        cleaned = cleanup_formula(span.content)
        cleaned_by_id[f"{filename}:{span.line}:{index}"] = cleaned
        errors = validate_formula(cleaned, span.display_mode)
        if errors:
            _set_stat(stats, "math_invalid")
            issue = FormulaIssue(
                issue_id=f"{filename}:{span.line}:{index}",
                span=span,
                errors=errors,
                cleaned=cleaned,
            )
            issues.append(issue)

    repaired: dict[str, str] = {}
    cache_dir = getattr(cache_manager, "_cache_dir", "") if cache_manager else ""
    if issues:
        batch_size = max(1, config.postprocess.repair_batch_size)
        uncached_issues: list[FormulaIssue] = []
        for issue in issues:
            _, line_no, issue_index = issue.issue_id.rsplit(":", 2)
            repair_id = f"postprocess_repair:{filename}:math_{line_no}_{issue_index}"
            formula_hash = hashlib.sha256(
                (issue.cleaned or issue.span.content).encode("utf-8")
            ).hexdigest()
            if cache_manager and cache_dir and cache_manager.is_valid(repair_id, formula_hash):
                cached_path = _repair_cache_path(cache_dir, repair_id)
                if os.path.exists(cached_path):
                    repaired[issue.issue_id] = Path(cached_path).read_text(encoding="utf-8")
                    continue
            uncached_issues.append(issue)
        for start in range(0, len(uncached_issues), batch_size):
            batch = uncached_issues[start : start + batch_size]
            batch_repaired = repair_batch_sync(
                batch, config, config.postprocess, usage_stats, middleware
            )
            repaired.update(batch_repaired)
            if cache_manager and cache_dir:
                issue_lookup = {issue.issue_id: issue for issue in batch}
                for issue_id, repaired_formula in batch_repaired.items():
                    issue = issue_lookup.get(issue_id)
                    if issue is None:
                        continue
                    _, line_no, issue_index = issue.issue_id.rsplit(":", 2)
                    repair_id = f"postprocess_repair:{filename}:math_{line_no}_{issue_index}"
                    formula_hash = hashlib.sha256(
                        (issue.cleaned or issue.span.content).encode("utf-8")
                    ).hexdigest()
                    cached_path = _repair_cache_path(cache_dir, repair_id)
                    Path(cached_path).write_text(repaired_formula, encoding="utf-8")
                    cache_manager.mark_done(
                        repair_id,
                        input_hash=formula_hash,
                        output_path=cached_path,
                        output_file=os.path.basename(cached_path),
                    )

    replacements: list[tuple[int, int, str]] = []
    issue_lookup = {issue.issue_id: issue for issue in issues}
    for index, span in enumerate(spans):
        issue_id = f"{filename}:{span.line}:{index}"
        cleaned = cleaned_by_id[issue_id]
        issue = issue_lookup.get(issue_id)
        if issue is None:
            if cleaned == span.content:
                continue
            replacements.append(
                (
                    span.start,
                    span.end,
                    f"{span.delimiter}{cleaned}{_closing_delimiter(span.delimiter)}",
                )
            )
            continue
        replacement = repaired.get(issue_id)
        if replacement is None:
            _set_stat(stats, "math_failed")
            error_msg = "; ".join(issue.errors)
            if report is not None and hasattr(report, "math_failures"):
                report.math_failures.append(
                    {
                        "file": filename,
                        "expression": span.content[:120],
                        "error": error_msg,
                        "degraded": True,
                    }
                )
            replacements.append((span.start, span.end, _format_failed_formula(span, error_msg)))
            continue
        repaired_errors = validate_formula(replacement, span.display_mode)
        if repaired_errors:
            _set_stat(stats, "math_failed")
            error_msg = "; ".join(repaired_errors)
            if report is not None and hasattr(report, "math_failures"):
                report.math_failures.append(
                    {
                        "file": filename,
                        "expression": span.content[:120],
                        "error": error_msg,
                        "degraded": True,
                    }
                )
            replacements.append((span.start, span.end, _format_failed_formula(span, error_msg)))
            continue
        _set_stat(stats, "math_repaired")
        replacements.append(
            (
                span.start,
                span.end,
                f"{span.delimiter}{replacement}{_closing_delimiter(span.delimiter)}",
            )
        )

    updated = text
    for start, end, replacement in reversed(replacements):
        updated = updated[:start] + replacement + updated[end:]
    return updated
