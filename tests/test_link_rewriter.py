import pytest

from types import SimpleNamespace

from codewiki.src.be.docs_fixer import fix_docs
from codewiki.src.be.postprocess.link_rewriter import rewrite_broken_links


@pytest.fixture
def docs_dir(tmp_path):
    (tmp_path / "auth_manager.md").write_text("# Auth Manager\nContent.", encoding="utf-8")
    (tmp_path / "cli.md").write_text("# CLI\nContent.", encoding="utf-8")
    (tmp_path / "cli-io_abstractions.md").write_text("# IO\nContent.", encoding="utf-8")
    return tmp_path


class TestRewriteBrokenLinks:
    def test_correct_link_unchanged(self, docs_dir):
        md = docs_dir / "test.md"
        md.write_text("See [Auth](auth_manager.md).", encoding="utf-8")

        stats = rewrite_broken_links(str(docs_dir))

        assert md.read_text(encoding="utf-8") == "See [Auth](auth_manager.md)."
        assert stats["rewritten"] == 0
        assert stats["removed"] == 0

    def test_fuzzy_match_rewrites(self, docs_dir):
        md = docs_dir / "test.md"
        md.write_text("See [Auth](Auth-Manager.md).", encoding="utf-8")

        stats = rewrite_broken_links(str(docs_dir))

        assert md.read_text(encoding="utf-8") == "See [Auth](auth_manager.md)."
        assert stats["rewritten"] == 1

    def test_relative_path_stripped(self, docs_dir):
        md = docs_dir / "test.md"
        md.write_text("See [IO](../cli/io_abstractions.md).", encoding="utf-8")

        rewrite_broken_links(str(docs_dir))

        assert md.read_text(encoding="utf-8") == "See [IO](cli-io_abstractions.md)."

    def test_anchor_is_preserved_when_target_is_rewritten(self, docs_dir):
        md = docs_dir / "test.md"
        md.write_text("See [Auth](Auth-Manager.md#usage).", encoding="utf-8")

        stats = rewrite_broken_links(str(docs_dir))

        assert md.read_text(encoding="utf-8") == "See [Auth](auth_manager.md#usage)."
        assert stats["rewritten"] == 1

    def test_inline_code_link_snippet_is_left_untouched(self, docs_dir):
        md = docs_dir / "test.md"
        original = "Use `[Auth](broken.md)` as an example in prose."
        md.write_text(original, encoding="utf-8")

        stats = rewrite_broken_links(str(docs_dir))

        assert md.read_text(encoding="utf-8") == original
        assert stats["rewritten"] == 0
        assert stats["removed"] == 0

    def test_trailing_newline_is_preserved_after_rewrite(self, docs_dir):
        md = docs_dir / "test.md"
        md.write_text("See [Auth](Auth-Manager.md).\n", encoding="utf-8")

        rewrite_broken_links(str(docs_dir))

        assert md.read_text(encoding="utf-8").endswith("\n")

    def test_nonexistent_becomes_plain_text(self, docs_dir):
        md = docs_dir / "test.md"
        md.write_text("See [Ghost](nonexistent.md).", encoding="utf-8")

        stats = rewrite_broken_links(str(docs_dir))

        assert md.read_text(encoding="utf-8") == "See Ghost."
        assert stats["removed"] == 1

    def test_external_untouched(self, docs_dir):
        md = docs_dir / "test.md"
        original = "See [X](https://example.com)."
        md.write_text(original, encoding="utf-8")

        rewrite_broken_links(str(docs_dir))

        assert md.read_text(encoding="utf-8") == original

    def test_code_block_untouched(self, docs_dir):
        md = docs_dir / "test.md"
        original = "```\n[link](broken.md)\n```"
        md.write_text(original, encoding="utf-8")

        rewrite_broken_links(str(docs_dir))

        assert md.read_text(encoding="utf-8") == original

    def test_collision_in_index_skips(self, docs_dir):
        """When two files normalize to same name, don't rewrite to either."""
        (docs_dir / "A-B.md").write_text("content1", encoding="utf-8")
        (docs_dir / "a_b.md").write_text("content2", encoding="utf-8")
        md = docs_dir / "test.md"
        md.write_text("See [X](A--B.md).", encoding="utf-8")

        stats = rewrite_broken_links(str(docs_dir))

        assert md.read_text(encoding="utf-8") == "See X."
        assert stats["removed"] == 1
        assert stats["rewritten"] == 0

    def test_fix_docs_rewrites_before_validation(self, docs_dir, monkeypatch):
        md = docs_dir / "test.md"
        md.write_text("See [IO](../cli/io_abstractions.md).", encoding="utf-8")

        seen = {}

        def fake_validate_links(_docs_dir):
            seen["content"] = md.read_text(encoding="utf-8")
            return []

        monkeypatch.setattr(
            "codewiki.src.be.postprocess.link_validator.validate_links",
            fake_validate_links,
        )

        fix_docs(
            str(docs_dir),
            SimpleNamespace(postprocess=SimpleNamespace(strict=False, fix_links=True)),
        )

        assert seen["content"].strip() == "See [IO](cli-io_abstractions.md)."
        assert md.read_text(encoding="utf-8").strip() == "See [IO](cli-io_abstractions.md)."
