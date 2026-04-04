import json
from pathlib import Path

import pytest


def test_load_module_tree_returns_fallback_when_missing(tmp_path):
    from codewiki.cli.html_generator import HTMLGenerator

    generator = HTMLGenerator(template_dir=tmp_path)

    module_tree = generator.load_module_tree(tmp_path)

    assert "Overview" in module_tree
    assert module_tree["Overview"]["children"] == {}


def test_load_module_tree_wraps_invalid_json(tmp_path):
    from codewiki.cli.html_generator import HTMLGenerator
    from codewiki.cli.utils.errors import FileSystemError

    (tmp_path / "module_tree.json").write_text("{bad json", encoding="utf-8")
    generator = HTMLGenerator(template_dir=tmp_path)

    with pytest.raises(FileSystemError, match="Failed to load module tree"):
        generator.load_module_tree(tmp_path)


def test_generate_renders_template_with_loaded_docs_data(tmp_path):
    from codewiki.cli.html_generator import HTMLGenerator

    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "viewer_template.html").write_text(
        "\n".join(
            [
                "<title>{{TITLE}}</title>",
                "{{REPO_LINK}}",
                "{{SHOW_INFO}}",
                "{{INFO_CONTENT}}",
                "{{DOCS_BASE_PATH}}",
                "{{MODULE_TREE_JSON}}",
                "{{METADATA_JSON}}",
                "{{GUIDE_PAGES_JSON}}",
            ]
        ),
        encoding="utf-8",
    )

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "module_tree.json").write_text(
        json.dumps({"Root": {"children": {}}}),
        encoding="utf-8",
    )
    (docs_dir / "metadata.json").write_text(
        json.dumps(
            {
                "generation_info": {
                    "main_model": "openai/gpt-4o",
                    "timestamp": "2026-04-04T00:00:00+00:00",
                    "commit_id": "abcdef1234567890",
                },
                "statistics": {
                    "total_components": 12,
                    "max_depth": 3,
                },
            }
        ),
        encoding="utf-8",
    )
    (docs_dir / "guide-getting-started.md").write_text("# Start", encoding="utf-8")
    (docs_dir / "guide-getting-started-install.md").write_text("# Install", encoding="utf-8")

    output_path = tmp_path / "site" / "index.html"
    generator = HTMLGenerator(template_dir=template_dir)
    generator.generate(
        output_path=output_path,
        title="Repo <Docs>",
        repository_url="https://example.com/repo",
        docs_dir=docs_dir,
        hide_repo_links=True,
    )

    content = output_path.read_text(encoding="utf-8")
    assert "Repo &lt;Docs&gt;" in content
    assert "View Repository" not in content
    assert "openai/gpt-4o" in content
    assert "abcdef12" in content
    assert '"Root"' in content
    assert "guide-getting-started-install" in content


def test_generate_requires_template_file(tmp_path):
    from codewiki.cli.html_generator import HTMLGenerator
    from codewiki.cli.utils.errors import FileSystemError

    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    generator = HTMLGenerator(template_dir=template_dir)

    with pytest.raises(FileSystemError, match="Template not found"):
        generator.generate(output_path=tmp_path / "index.html", title="Docs")
