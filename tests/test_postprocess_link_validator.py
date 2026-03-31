"""Tests for postprocess.link_validator — build_anchor_registry and validate_links.

Written BEFORE implementation (TDD RED phase).
"""
import os
import pytest

from codewiki.src.be.postprocess.link_validator import build_anchor_registry, validate_links, LinkIssue


class TestBuildAnchorRegistry:
    def test_anchor_registry_extracts_headings(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# H1 Heading\n\n## H2 Heading\n\nSome paragraph.\n")
        registry = build_anchor_registry(str(tmp_path))
        assert "doc.md" in registry
        slugs = registry["doc.md"]
        assert "h1-heading" in slugs
        assert "h2-heading" in slugs

    def test_anchor_registry_uses_heading_to_slug(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# Hello, World!\n")
        registry = build_anchor_registry(str(tmp_path))
        assert "hello-world" in registry["doc.md"]

    def test_anchor_registry_ignores_non_md_files(self, tmp_path):
        (tmp_path / "notes.txt").write_text("# Not a heading\n")
        registry = build_anchor_registry(str(tmp_path))
        assert "notes.txt" not in registry

    def test_anchor_registry_empty_dir(self, tmp_path):
        registry = build_anchor_registry(str(tmp_path))
        assert registry == {}

    def test_anchor_registry_nested_dirs(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "nested.md").write_text("## Nested Heading\n")
        registry = build_anchor_registry(str(tmp_path))
        assert "sub/nested.md" in registry
        assert "nested-heading" in registry["sub/nested.md"]

    def test_anchor_registry_bold_heading(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# **Bold** Title\n")
        registry = build_anchor_registry(str(tmp_path))
        assert "bold-title" in registry["doc.md"]

    def test_anchor_registry_skips_empty_slug(self, tmp_path):
        # A heading that produces an empty slug (e.g. only punctuation) should not be added
        f = tmp_path / "doc.md"
        f.write_text("# !!!\n\n# Real Heading\n")
        registry = build_anchor_registry(str(tmp_path))
        # Only the real heading slug should appear
        assert "real-heading" in registry["doc.md"]
        assert "" not in registry["doc.md"]

    def test_anchor_registry_all_heading_levels(self, tmp_path):
        f = tmp_path / "doc.md"
        content = "\n".join(f"{'#' * i} Level {i}" for i in range(1, 7))
        f.write_text(content + "\n")
        registry = build_anchor_registry(str(tmp_path))
        for i in range(1, 7):
            assert f"level-{i}" in registry["doc.md"]


class TestValidateLinks:
    def test_validate_links_file_found(self, tmp_path):
        (tmp_path / "source.md").write_text("[link](other.md)\n")
        (tmp_path / "other.md").write_text("# Other\n")
        issues = validate_links(str(tmp_path))
        assert issues == []

    def test_validate_links_file_not_found(self, tmp_path):
        (tmp_path / "source.md").write_text("[link](missing.md)\n")
        issues = validate_links(str(tmp_path))
        assert len(issues) == 1
        assert issues[0].issue_type == "file_not_found"
        assert issues[0].source_file == "source.md"
        assert issues[0].target == "missing.md"

    def test_validate_links_anchor_found(self, tmp_path):
        (tmp_path / "source.md").write_text("[link](other.md#my-heading)\n")
        (tmp_path / "other.md").write_text("# My Heading\n")
        issues = validate_links(str(tmp_path))
        assert issues == []

    def test_validate_links_anchor_not_found(self, tmp_path):
        (tmp_path / "source.md").write_text("[link](other.md#bad-anchor)\n")
        (tmp_path / "other.md").write_text("# My Heading\n")
        issues = validate_links(str(tmp_path))
        assert len(issues) == 1
        assert issues[0].issue_type == "anchor_not_found"
        assert issues[0].target == "other.md#bad-anchor"

    def test_validate_links_same_file_anchor(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# My Heading\n\n[link](#my-heading)\n")
        issues = validate_links(str(tmp_path))
        assert issues == []

    def test_validate_links_same_file_anchor_not_found(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# My Heading\n\n[link](#nonexistent)\n")
        issues = validate_links(str(tmp_path))
        assert len(issues) == 1
        assert issues[0].issue_type == "anchor_not_found"

    def test_validate_links_skips_external(self, tmp_path):
        (tmp_path / "doc.md").write_text("[ext](https://example.com)\n[mail](mailto:a@b.com)\n")
        issues = validate_links(str(tmp_path))
        assert issues == []

    def test_validate_links_empty_dir(self, tmp_path):
        issues = validate_links(str(tmp_path))
        assert issues == []

    def test_validate_links_skips_code_blocks(self, tmp_path):
        content = "```\n[link](missing.md)\n```\n"
        (tmp_path / "doc.md").write_text(content)
        issues = validate_links(str(tmp_path))
        assert issues == []

    def test_validate_links_empty_link(self, tmp_path):
        (tmp_path / "doc.md").write_text("[text]()\n")
        issues = validate_links(str(tmp_path))
        assert len(issues) == 1
        assert issues[0].issue_type == "empty_link"

    def test_validate_links_multiple_issues(self, tmp_path):
        (tmp_path / "doc.md").write_text(
            "[missing](gone.md)\n[bad anchor](other.md#nope)\n"
        )
        (tmp_path / "other.md").write_text("# Real\n")
        issues = validate_links(str(tmp_path))
        assert len(issues) == 2
        types = {i.issue_type for i in issues}
        assert "file_not_found" in types
        assert "anchor_not_found" in types

    def test_validate_links_returns_line_numbers(self, tmp_path):
        (tmp_path / "doc.md").write_text("Line 1\n[bad](nope.md)\nLine 3\n")
        issues = validate_links(str(tmp_path))
        assert issues[0].line_number == 2

    def test_validate_links_link_text_captured(self, tmp_path):
        (tmp_path / "doc.md").write_text("[my link text](missing.md)\n")
        issues = validate_links(str(tmp_path))
        assert issues[0].link_text == "my link text"

    def test_validate_links_http_skipped(self, tmp_path):
        (tmp_path / "doc.md").write_text("[site](http://example.com)\n")
        issues = validate_links(str(tmp_path))
        assert issues == []

    def test_validate_links_relative_subdir(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "page.md").write_text("[link](../root.md)\n")
        (tmp_path / "root.md").write_text("# Root\n")
        issues = validate_links(str(tmp_path))
        assert issues == []

    def test_link_issue_dataclass_fields(self):
        issue = LinkIssue(
            source_file="a.md",
            line_number=1,
            link_text="text",
            target="b.md",
            issue_type="file_not_found",
        )
        assert issue.source_file == "a.md"
        assert issue.line_number == 1
        assert issue.link_text == "text"
        assert issue.target == "b.md"
        assert issue.issue_type == "file_not_found"
