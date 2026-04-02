"""Post-generation link rewriter.

Rewrites broken internal links before link validation runs so the lint report
reflects the post-fix state.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from codewiki.src.utils import _normalize_for_match

logger = logging.getLogger(__name__)

_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_AMBIGUOUS = object()

def _normalize_stem(name: str) -> str:
    """Normalize a filename stem for suffix matching."""
    stem = os.path.splitext(name)[0]
    return _normalize_for_match(stem)


def _target_candidate_name(file_part: str) -> str:
    """Flatten a relative link target into a canonical filename candidate."""
    parts: list[str] = []
    for part in PurePosixPath(file_part).parts:
        if part in ("", ".", ".."):
            continue
        stem = os.path.splitext(part)[0]
        normalized = _normalize_for_match(stem)
        if normalized:
            parts.append(normalized)
    if not parts:
        return ""
    return f"{'-'.join(parts)}.md"


def _build_indexes(docs_dir: str) -> tuple[set[str], dict[str, object], dict[str, object]]:
    """Build exact, normalized, and suffix indexes for markdown files."""
    exact_files: set[str] = set()
    normalized_index: dict[str, object] = {}
    suffix_index: dict[str, object] = {}

    for fname in sorted(os.listdir(docs_dir)):
        if not fname.endswith(".md"):
            continue
        exact_files.add(fname)

        normalized = _normalize_for_match(fname)
        if normalized in normalized_index:
            normalized_index[normalized] = _AMBIGUOUS
        else:
            normalized_index[normalized] = fname

        stem = _normalize_stem(fname)
        if stem in suffix_index:
            suffix_index[stem] = _AMBIGUOUS
        else:
            suffix_index[stem] = fname

    return exact_files, normalized_index, suffix_index


def _rewrite_target(
    target: str,
    exact_files: set[str],
    normalized_index: dict[str, object],
    suffix_index: dict[str, object],
) -> tuple[str, bool, bool]:
    """Return (new_target, rewritten, removed)."""
    if not target:
        return target, False, False
    if target.startswith(("http://", "https://", "mailto:", "#")):
        return target, False, False

    anchor = ""
    if "#" in target:
        file_part, anchor = target.rsplit("#", 1)
        anchor = "#" + anchor
    else:
        file_part = target

    file_part = file_part.strip()
    if not file_part:
        return target, False, False

    basename = os.path.basename(unquote(file_part))
    if not basename:
        return target, False, False

    candidate = _target_candidate_name(file_part)
    candidate_norm = _normalize_for_match(candidate or basename)
    if candidate in exact_files:
        if normalized_index.get(candidate_norm) is _AMBIGUOUS:
            return target, False, True
        new_target = f"{candidate}{anchor}"
        return new_target, new_target != target, False

    normalized = candidate_norm
    matched = normalized_index.get(normalized)
    if matched is not None and matched is not _AMBIGUOUS:
        new_target = f"{matched}{anchor}"
        return new_target, new_target != target, False

    stem = _normalize_stem(candidate or basename)
    suffix_candidate = suffix_index.get(stem)
    if suffix_candidate is not None and suffix_candidate is not _AMBIGUOUS:
        new_target = f"{suffix_candidate}{anchor}"
        return new_target, new_target != target, False

    return target, False, True


def rewrite_broken_links(docs_dir: str) -> dict[str, int]:
    """Rewrite or remove broken markdown links in all docs under *docs_dir*."""
    stats = {"rewritten": 0, "removed": 0, "total_scanned": 0}
    exact_files, normalized_index, suffix_index = _build_indexes(docs_dir)

    for fname in sorted(exact_files):
        filepath = os.path.join(docs_dir, fname)
        try:
            content = Path(filepath).read_text(encoding="utf-8")
        except OSError:
            continue

        stats["total_scanned"] += 1
        lines = content.splitlines()
        new_lines: list[str] = []
        in_code_block = False
        changed = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                new_lines.append(line)
                continue
            if in_code_block:
                new_lines.append(line)
                continue

            code_spans: dict[str, str] = {}

            def _stash_code(match: re.Match[str]) -> str:
                key = f"CWCODE{len(code_spans):04d}"
                code_spans[key] = match.group(0)
                return key

            protected_line = re.sub(r"`[^`]*`", _stash_code, line)

            def _replace(match: re.Match[str]) -> str:
                nonlocal changed
                link_text = match.group(1)
                target = match.group(2).strip()
                new_target, rewritten, removed = _rewrite_target(
                    target, exact_files, normalized_index, suffix_index
                )
                if rewritten:
                    changed = True
                    stats["rewritten"] += 1
                    return f"[{link_text}]({new_target})"
                if removed:
                    changed = True
                    stats["removed"] += 1
                    return link_text
                return match.group(0)

            new_line = _LINK_RE.sub(_replace, protected_line)
            for key, original in code_spans.items():
                new_line = new_line.replace(key, original)
            new_lines.append(new_line)
            if new_line != line:
                changed = True

        if changed:
            rendered = "\n".join(new_lines)
            if content.endswith("\n"):
                rendered += "\n"
            Path(filepath).write_text(rendered, encoding="utf-8")

    return stats
