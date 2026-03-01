# tests/test_repo_docs_collector.py
import os
import tempfile
from pathlib import Path

from codewiki.src.be.repo_docs_collector import RepoDocsCollector, DocSnippet


def _make_tree(base, files: dict):
    """Create a file tree under base. files maps relative path to content."""
    for rel, content in files.items():
        p = Path(base) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def test_collect_repo_docs_finds_markdown():
    with tempfile.TemporaryDirectory() as repo:
        _make_tree(repo, {
            "README.md": "# Hello\nThis is a readme.",
            "docs/guide.md": "# Guide\nSome guide content.",
            "src/main.py": "# not a doc file",
        })
        collector = RepoDocsCollector()
        bundle = collector.collect(repo_path=repo, working_dir=None, components={})
        md_paths = [s.path for s in bundle.repo_docs]
        assert any("README.md" in p for p in md_paths)
        assert any("guide.md" in p for p in md_paths)
        assert not any("main.py" in p for p in md_paths)


def test_collect_excludes_node_modules_and_git():
    with tempfile.TemporaryDirectory() as repo:
        _make_tree(repo, {
            "README.md": "# Hello",
            "node_modules/pkg/README.md": "# skip me",
            ".git/HEAD": "ref: refs/heads/main",
        })
        collector = RepoDocsCollector()
        bundle = collector.collect(repo_path=repo, working_dir=None, components={})
        paths = [s.path for s in bundle.repo_docs]
        assert not any("node_modules" in p for p in paths)
        assert not any(".git" in p for p in paths)


def test_collect_generated_docs():
    with tempfile.TemporaryDirectory() as repo:
        with tempfile.TemporaryDirectory() as wd:
            _make_tree(wd, {
                "module-a.md": "# Module A\nDeep dive content.",
                "overview.md": "# Overview\nProject overview.",
            })
            collector = RepoDocsCollector()
            bundle = collector.collect(repo_path=repo, working_dir=wd, components={})
            gen_paths = [s.path for s in bundle.generated_docs]
            assert any("module-a.md" in p for p in gen_paths)


def test_collect_excludes_guide_files_from_generated_docs():
    with tempfile.TemporaryDirectory() as repo:
        with tempfile.TemporaryDirectory() as wd:
            _make_tree(wd, {
                "module-a.md": "# Module A\nContent.",
                "guide-getting-started.md": "# Get Started\nGuide content.",
                "_guide_cache.json": "{}",
            })
            collector = RepoDocsCollector()
            bundle = collector.collect(repo_path=repo, working_dir=wd, components={})
            gen_paths = [s.path for s in bundle.generated_docs]
            assert any("module-a.md" in p for p in gen_paths)
            assert not any("guide-" in p for p in gen_paths)
            assert not any("_guide_cache" in p for p in gen_paths)


def test_collect_docstrings_from_components():
    from codewiki.src.be.dependency_analyzer.models.core import Node
    comp = Node(
        id="mod.py::MyClass", name="MyClass", component_type="class",
        file_path="/tmp/mod.py", relative_path="mod.py",
        source_code="class MyClass: pass", start_line=1, end_line=1,
        has_docstring=True, docstring="This class manages user sessions.",
        parameters=None, node_type="class", base_classes=None,
        class_name=None, display_name="MyClass", component_id="mod.py::MyClass",
    )
    collector = RepoDocsCollector()
    bundle = collector.collect(repo_path="/tmp", working_dir=None, components={"mod.py::MyClass": comp})
    assert any("user sessions" in s.content for s in bundle.docstrings)


def test_select_relevant_returns_matching_snippets():
    with tempfile.TemporaryDirectory() as repo:
        _make_tree(repo, {
            "README.md": "# Project\nInstallation instructions for setup.",
            "docs/api.md": "# API\nEndpoint documentation for REST.",
            "docs/auth.md": "# Auth\nAuthentication flow with JWT tokens.",
        })
        collector = RepoDocsCollector()
        bundle = collector.collect(repo_path=repo, working_dir=None, components={})
        results = bundle.select_relevant("installation setup", max_tokens=2000)
        # README should rank higher for "installation setup"
        assert len(results) > 0
        assert "installation" in results[0].content.lower() or "setup" in results[0].content.lower()


def test_select_relevant_respects_max_tokens():
    with tempfile.TemporaryDirectory() as repo:
        _make_tree(repo, {
            f"docs/doc{i}.md": f"# Doc {i}\n{'content ' * 500}" for i in range(20)
        })
        collector = RepoDocsCollector()
        bundle = collector.collect(repo_path=repo, working_dir=None, components={})
        results = bundle.select_relevant("content", max_tokens=500)
        total_chars = sum(len(r.content) for r in results)
        # Rough token estimate: 1 token ≈ 4 chars
        assert total_chars < 500 * 4
