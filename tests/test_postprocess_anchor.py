"""Tests for postprocess.anchor — heading_to_slug and extract_heading_text.

Written BEFORE implementation (TDD RED phase).
"""

import pytest

from codewiki.src.be.postprocess.anchor import heading_to_slug, extract_heading_text


class TestHeadingToSlug:
    def test_basic_heading(self):
        assert heading_to_slug("My Heading") == "my-heading"

    def test_strips_punctuation(self):
        assert heading_to_slug("Hello, World!") == "hello-world"

    def test_collapses_hyphens(self):
        # two spaces become two hyphens which then collapse
        assert heading_to_slug("foo  --  bar") == "foo-bar"

    def test_cjk_preserved(self):
        assert heading_to_slug("中文标题") == "中文标题"

    def test_deterministic(self):
        text = "My Heading with **bold** and `code`"
        results = [heading_to_slug(text) for _ in range(5)]
        assert len(set(results)) == 1

    def test_inline_code_stripped(self):
        assert heading_to_slug("`code` heading") == "code-heading"

    def test_bold_stripped(self):
        assert heading_to_slug("**bold** text") == "bold-text"

    def test_link_stripped(self):
        assert heading_to_slug("[link](url) text") == "link-text"

    def test_empty_string(self):
        assert heading_to_slug("") == ""

    def test_underscore_becomes_hyphen(self):
        assert heading_to_slug("foo_bar") == "foo-bar"

    def test_leading_trailing_hyphens_stripped(self):
        # A heading that starts or ends with special chars
        assert heading_to_slug("!Hello!") == "hello"

    def test_mixed_cjk_and_ascii(self):
        result = heading_to_slug("Hello 世界")
        assert "hello" in result
        assert "世界" in result

    def test_multiple_spaces_collapse(self):
        assert heading_to_slug("foo   bar") == "foo-bar"

    def test_numeric_preserved(self):
        assert heading_to_slug("Section 2") == "section-2"

    def test_italic_stripped(self):
        assert heading_to_slug("*italic* text") == "italic-text"


class TestExtractHeadingText:
    def test_plain_text(self):
        assert extract_heading_text("Plain heading") == "Plain heading"

    def test_bold_double_star(self):
        assert extract_heading_text("**Bold** text") == "Bold text"

    def test_bold_double_underscore(self):
        assert extract_heading_text("__Bold__ text") == "Bold text"

    def test_italic_star(self):
        assert extract_heading_text("*italic* text") == "italic text"

    def test_italic_underscore(self):
        assert extract_heading_text("_italic_ text") == "italic text"

    def test_inline_code(self):
        assert extract_heading_text("`code` heading") == "code heading"

    def test_link(self):
        assert extract_heading_text("[link](url)") == "link"

    def test_combined(self):
        result = extract_heading_text("**Bold** `code` [link](url)")
        assert result == "Bold code link"

    def test_empty_string(self):
        assert extract_heading_text("") == ""

    def test_strips_hash_chars(self):
        # Headings passed with leading # should have them stripped
        assert "#" not in extract_heading_text("# My Heading")

    def test_strips_whitespace(self):
        assert extract_heading_text("  My Heading  ") == "My Heading"
