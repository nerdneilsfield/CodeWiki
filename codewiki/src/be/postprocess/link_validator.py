"""Link validation: internal link + anchor checking for generated docs."""
import os
import re
from dataclasses import dataclass

from codewiki.src.be.postprocess.anchor import heading_to_slug


@dataclass
class LinkIssue:
    source_file: str
    line_number: int
    link_text: str
    target: str
    issue_type: str  # "file_not_found", "anchor_not_found", "empty_link"


def build_anchor_registry(docs_dir: str) -> dict[str, set[str]]:
    """Scan all .md files, extract headings and compute anchor slugs.

    Uses heading_to_slug() — the same function used by the renderer.
    Returns: {relative_filename: {slug1, slug2, ...}}
    """
    registry = {}
    for root, _, files in os.walk(docs_dir):
        for fname in sorted(files):
            if not fname.endswith('.md'):
                continue
            filepath = os.path.join(root, fname)
            rel_path = os.path.relpath(filepath, docs_dir).replace('\\', '/')
            anchors = set()
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    # Match ATX headings: # Heading, ## Heading, etc.
                    match = re.match(r'^(#{1,6})\s+(.+)$', line.strip())
                    if match:
                        heading_text = match.group(2).strip()
                        slug = heading_to_slug(heading_text)
                        if slug:
                            anchors.add(slug)
            registry[rel_path] = anchors
    return registry


def validate_links(docs_dir: str) -> list[LinkIssue]:
    """Scan all .md files for internal links, validate each.

    Checks:
    1. [text](file.md) — file exists
    2. [text](file.md#anchor) — file exists AND anchor exists
    3. [text](#anchor) — same-file anchor exists
    4. Empty/malformed links

    Skips external links (http://, https://, mailto:).
    """
    anchor_registry = build_anchor_registry(docs_dir)
    issues = []

    for root, _, files in os.walk(docs_dir):
        for fname in sorted(files):
            if not fname.endswith('.md'):
                continue
            filepath = os.path.join(root, fname)
            rel_path = os.path.relpath(filepath, docs_dir).replace('\\', '/')

            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                in_code_block = False
                for line_num, line in enumerate(f, 1):
                    # Skip code blocks
                    if line.strip().startswith('```'):
                        in_code_block = not in_code_block
                        continue
                    if in_code_block:
                        continue

                    # Find markdown links: [text](target)
                    for match in re.finditer(r'\[([^\]]*)\]\(([^)]*)\)', line):
                        link_text = match.group(1)
                        target = match.group(2).strip()

                        # Skip external links
                        if target.startswith(('http://', 'https://', 'mailto:')):
                            continue

                        # Skip empty
                        if not target:
                            issues.append(LinkIssue(
                                source_file=rel_path,
                                line_number=line_num,
                                link_text=link_text,
                                target=target,
                                issue_type="empty_link",
                            ))
                            continue

                        # Parse target into file and anchor
                        if '#' in target:
                            file_part, anchor_part = target.split('#', 1)
                        else:
                            file_part, anchor_part = target, None

                        if file_part:
                            # Resolve relative to source file's directory
                            source_dir = os.path.dirname(rel_path)
                            resolved = os.path.normpath(
                                os.path.join(source_dir, file_part)
                            ).replace('\\', '/')

                            if resolved not in anchor_registry:
                                issues.append(LinkIssue(
                                    source_file=rel_path,
                                    line_number=line_num,
                                    link_text=link_text,
                                    target=target,
                                    issue_type="file_not_found",
                                ))
                                continue

                            if anchor_part and anchor_part not in anchor_registry.get(resolved, set()):
                                issues.append(LinkIssue(
                                    source_file=rel_path,
                                    line_number=line_num,
                                    link_text=link_text,
                                    target=target,
                                    issue_type="anchor_not_found",
                                ))
                        else:
                            # Same-file anchor: #anchor
                            if anchor_part and anchor_part not in anchor_registry.get(rel_path, set()):
                                issues.append(LinkIssue(
                                    source_file=rel_path,
                                    line_number=line_num,
                                    link_text=link_text,
                                    target=target,
                                    issue_type="anchor_not_found",
                                ))

    return issues
