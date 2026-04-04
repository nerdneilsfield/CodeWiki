from pathlib import Path


def test_repo_analyzer_applies_include_patterns(tmp_path):
    from codewiki.src.be.dependency_analyzer.analysis.repo_analyzer import RepoAnalyzer

    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "b.md").write_text("x", encoding="utf-8")

    analyzer = RepoAnalyzer(include_patterns=["*.py"], exclude_patterns=[])
    result = analyzer.analyze_repository_structure(str(tmp_path))

    children = result["file_tree"]["children"]
    assert [child["name"] for child in children] == ["a.py"]
    assert result["summary"]["total_files"] == 1


def test_repo_analyzer_skips_symlinks_and_ignores_permission_errors(tmp_path):
    from codewiki.src.be.dependency_analyzer.analysis.repo_analyzer import RepoAnalyzer

    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "a.py").write_text("print('x')", encoding="utf-8")
    symlink = tmp_path / "link"
    symlink.symlink_to(real_dir, target_is_directory=True)

    blocked = tmp_path / "blocked"
    blocked.mkdir()

    analyzer = RepoAnalyzer(include_patterns=["*.py"], exclude_patterns=[])

    original_iterdir = Path.iterdir

    def fake_iterdir(self):
        if self == blocked:
            raise PermissionError("denied")
        yield from original_iterdir(self)

    with __import__("unittest").mock.patch.object(Path, "iterdir", fake_iterdir):
        result = analyzer.analyze_repository_structure(str(tmp_path))

    names = {child["name"] for child in result["file_tree"]["children"]}
    assert "link" not in names
    assert "blocked" not in names
    assert "real" in names


def test_repo_analyzer_prunes_excluded_directories(tmp_path):
    from codewiki.src.be.dependency_analyzer.analysis.repo_analyzer import RepoAnalyzer

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "skip.py").write_text("x", encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "keep.py").write_text("x", encoding="utf-8")

    analyzer = RepoAnalyzer(include_patterns=["*.py"], exclude_patterns=["tests"])
    result = analyzer.analyze_repository_structure(str(tmp_path))

    children = {child["name"]: child for child in result["file_tree"]["children"]}
    assert "tests" not in children
    assert "src" in children


def test_repo_analyzer_should_exclude_matches_directory_suffix():
    from codewiki.src.be.dependency_analyzer.analysis.repo_analyzer import RepoAnalyzer

    analyzer = RepoAnalyzer(include_patterns=["*.py"], exclude_patterns=["build/"])
    assert analyzer._should_exclude_path("build/generated.py", "generated.py") is True
