"""Stable heading anchor generation — single source of truth.

Both the renderer (visualise_docs.py) and the link validator use this
function to ensure generated heading IDs match validated anchors.
"""

import re
import unicodedata


def heading_to_slug(text: str) -> str:
    """Convert heading text to a stable anchor slug.

    Rules (deterministic):
    - Strip inline markdown formatting (bold, italic, code, links)
    - Lowercase
    - Replace spaces and underscores with hyphens
    - Remove non-alphanumeric chars except hyphens and CJK (U+4E00-U+9FFF, U+3400-U+4DBF)
    - Collapse consecutive hyphens
    - Strip leading/trailing hyphens
    """
    # Strip inline markdown: **bold**, *italic*, `code`, [text](url)
    clean = extract_heading_text(text)
    # Lowercase
    clean = clean.lower()
    # Replace spaces and underscores with hyphens
    clean = re.sub(r"[\s_]+", "-", clean)
    # Keep alphanumeric, hyphens, and CJK characters
    clean = re.sub(r"[^\w\u4e00-\u9fff\u3400-\u4dbf-]", "", clean)
    # Collapse consecutive hyphens
    clean = re.sub(r"-{2,}", "-", clean)
    # Strip leading/trailing hyphens
    clean = clean.strip("-")
    return clean


def extract_heading_text(markdown_text: str) -> str:
    """Extract visible text from a markdown heading, stripping inline formatting.

    Used by BOTH the anchor registry AND the renderer to ensure they
    compute the same slug from the same heading.

    Examples:
    - "**Bold** text" -> "Bold text"
    - "`code` and [link](url)" -> "code and link"
    - "Plain heading" -> "Plain heading"
    """
    text = markdown_text.strip()
    # Strip markdown links: [text](url) -> text
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    # Strip inline code: `code` -> code
    text = re.sub(r"`([^`]*)`", r"\1", text)
    # Strip bold: **text** or __text__ -> text
    text = re.sub(r"\*\*([^*]*)\*\*", r"\1", text)
    text = re.sub(r"__([^_]*)__", r"\1", text)
    # Strip italic: *text* or _text_ -> text
    text = re.sub(r"\*([^*]*)\*", r"\1", text)
    text = re.sub(r"_([^_]*)_", r"\1", text)
    # Strip remaining markdown artifacts
    text = re.sub(r"[#>]", "", text)
    return text.strip()
