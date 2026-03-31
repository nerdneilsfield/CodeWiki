"""Tests for frontend heading id injection consistency."""

from codewiki.src.fe.visualise_docs import markdown_to_html


class TestMarkdownToHtmlHeadingIds:
    def test_duplicate_headings_get_unique_ids(self):
        html = markdown_to_html("## Repeat\n\ntext\n\n## Repeat\n")
        assert 'id="repeat"' in html
        assert 'id="repeat-1"' in html

    def test_setext_heading_gets_id(self):
        html = markdown_to_html("Title\n=====\n")
        assert 'id="title"' in html
