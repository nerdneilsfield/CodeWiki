from pathlib import Path

from codewiki.cli.static_generator import (
    _extract_math_blocks,
    _fix_markdown_links,
    _resolve_nav_hrefs,
    _rewrite_md_to_html_links,
)


def test_rewrite_md_to_html_links_preserves_anchor_fragment():
    html = '<p><a href="guide.md#intro">Guide</a></p>'

    result = _rewrite_md_to_html_links(html)

    assert result == '<p><a href="guide.html#intro">Guide</a></p>'


def test_rewrite_md_to_html_links_preserves_query_string():
    html = '<p><a href="guide.md?view=full">Guide</a></p>'

    result = _rewrite_md_to_html_links(html)

    assert result == '<p><a href="guide.html?view=full">Guide</a></p>'


def test_rewrite_md_to_html_links_leaves_external_markdown_urls_untouched():
    html = '<p><a href="https://example.com/guide.md#intro">Guide</a></p>'

    result = _rewrite_md_to_html_links(html)

    assert result == html


def test_rewrite_md_to_html_links_leaves_hash_only_links_untouched():
    html = '<p><a href="#intro">Jump</a></p>'

    result = _rewrite_md_to_html_links(html)

    assert result == html


def test_fix_markdown_links_encodes_spaces_only_inside_url():
    content = "See [Guide](Guide File.md) and [External](https://example.com/no spaces)."

    result = _fix_markdown_links(content)

    assert "[Guide](Guide%20File.md)" in result
    assert "[External](https://example.com/no%20spaces)" in result


def test_extract_math_blocks_preserves_cjk_currency_like_text():
    content = "价格是 $100$ 元，不是公式。"

    rendered, protected = _extract_math_blocks(content)

    assert rendered == content
    assert protected == []


def test_extract_math_blocks_extracts_backslash_delimited_inline_math():
    content = r"Use \(a+b\) inside prose."

    rendered, protected = _extract_math_blocks(content)

    assert rendered != content
    assert len(protected) == 1
    assert protected[0][0].startswith("CWIKIMI")
    assert r"\(a+b\)" in protected[0][1]


def test_extract_math_blocks_keeps_display_math_and_inline_math_separate():
    content = "$$a+b$$ and $c+d$"

    rendered, protected = _extract_math_blocks(content)

    assert rendered.count("CWIKIMD") == 1
    assert rendered.count("CWIKIMI") == 1
    assert len(protected) == 2


def test_resolve_nav_hrefs_prefers_frozen_filename_when_present(tmp_path):
    (tmp_path / "cli.md").write_text("# CLI", encoding="utf-8")
    tree = {"CLI Transport": {"_doc_filename": "cli.md", "children": {}}}

    resolved = _resolve_nav_hrefs(tree, str(tmp_path))

    assert resolved == {"CLI Transport": "cli.html"}


def test_resolve_nav_hrefs_falls_back_to_fuzzy_existing_doc(tmp_path):
    (tmp_path / "cli_transports.md").write_text("# CLI", encoding="utf-8")
    tree = {"CLI Transports": {"children": {}}}

    resolved = _resolve_nav_hrefs(tree, str(tmp_path))

    assert resolved == {"CLI Transports": "cli_transports.html"}


def test_static_generator_module_compiles_without_syntaxwarning(tmp_path):
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [
            sys.executable,
            "-Werror::SyntaxWarning",
            "-m",
            "py_compile",
            "codewiki/cli/static_generator.py",
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
