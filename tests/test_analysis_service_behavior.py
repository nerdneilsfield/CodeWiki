from pathlib import Path
from unittest.mock import MagicMock

import pytest

from codewiki.src.be.dependency_analyzer.analysis.analysis_service import (
    AnalysisService,
    analyze_repository,
    analyze_repository_structure_only,
)


def test_analyze_local_repository_filters_languages_and_applies_max_files(monkeypatch, tmp_path):
    service = AnalysisService()
    structure = {"file_tree": {"kind": "dir"}, "summary": {"total_files": 3}}

    monkeypatch.setattr(
        "codewiki.src.be.dependency_analyzer.analysis.analysis_service.RepoAnalyzer.analyze_repository_structure",
        lambda self, repo_path: structure,
    )
    service.call_graph_analyzer.extract_code_files = MagicMock(
        return_value=[
            {"path": "a.py", "language": "python"},
            {"path": "b.js", "language": "javascript"},
            {"path": "c.java", "language": "java"},
        ]
    )
    service.call_graph_analyzer.analyze_code_files = MagicMock(
        return_value={
            "functions": [{"name": "f1"}],
            "relationships": [{"caller": "a", "callee": "b"}],
        }
    )

    result = service.analyze_local_repository(
        str(tmp_path), max_files=1, languages=["python", "javascript"]
    )

    analyzed_files = service.call_graph_analyzer.analyze_code_files.call_args[0][0]
    assert len(analyzed_files) == 1
    assert analyzed_files[0]["language"] == "python"
    assert result["summary"]["total_files"] == 1
    assert result["summary"]["total_nodes"] == 1
    assert result["summary"]["total_relationships"] == 1


def test_analyze_repository_full_builds_result_and_cleans_up(monkeypatch, tmp_path):
    service = AnalysisService()
    repo_dir = str(tmp_path / "repo")
    Path(repo_dir).mkdir()

    service._clone_repository = MagicMock(return_value=repo_dir)
    service._parse_repository_info = MagicMock(
        return_value={"url": "https://github.com/acme/demo", "name": "demo", "owner": "acme"}
    )
    service._analyze_structure = MagicMock(
        return_value={"file_tree": {"root": []}, "summary": {"total_files": 2}}
    )
    service._analyze_call_graph = MagicMock(
        return_value={
            "functions": [],
            "relationships": [],
            "visualization": {"nodes": [], "edges": []},
            "call_graph": {"total_functions": 0, "languages_found": ["python"]},
        }
    )
    service._read_readme_file = MagicMock(return_value="# Demo")
    service._cleanup_repository = MagicMock()

    result = service.analyze_repository_full("https://github.com/acme/demo")

    assert result.repository.name == "demo"
    assert result.summary["analysis_type"] == "full"
    assert result.summary["languages_analyzed"] == ["python"]
    assert result.readme_content == "# Demo"
    service._cleanup_repository.assert_called_once_with(repo_dir)


def test_analyze_repository_full_cleans_up_and_wraps_errors(monkeypatch, tmp_path):
    service = AnalysisService()
    repo_dir = str(tmp_path / "repo")
    Path(repo_dir).mkdir()

    service._clone_repository = MagicMock(return_value=repo_dir)
    service._parse_repository_info = MagicMock(side_effect=RuntimeError("boom"))
    service._cleanup_repository = MagicMock()

    with pytest.raises(RuntimeError, match="Repository analysis failed: boom"):
        service.analyze_repository_full("https://github.com/acme/demo")

    service._cleanup_repository.assert_called_once_with(repo_dir)


def test_analysis_service_supported_language_helpers():
    service = AnalysisService()
    files = [
        {"path": "a.py", "language": "python"},
        {"path": "b.unknown", "language": "unknown"},
    ]

    filtered = service._filter_supported_languages(files)

    assert filtered == [{"path": "a.py", "language": "python"}]
    assert "python" in service._get_supported_languages()
    assert service._get_supported_languages() == sorted(service._get_supported_languages())


def test_analysis_service_wrapper_functions(monkeypatch):
    full_service = MagicMock()
    full_service.analyze_repository_full.return_value = "full-result"
    structure_service = MagicMock()
    structure_service.analyze_repository_structure_only.return_value = {"file_tree": {}}

    monkeypatch.setattr(
        "codewiki.src.be.dependency_analyzer.analysis.analysis_service.AnalysisService",
        lambda: full_service,
    )
    result, temp_dir = analyze_repository("https://github.com/acme/demo")
    assert result == "full-result"
    assert temp_dir is None

    monkeypatch.setattr(
        "codewiki.src.be.dependency_analyzer.analysis.analysis_service.AnalysisService",
        lambda: structure_service,
    )
    result, temp_dir = analyze_repository_structure_only("https://github.com/acme/demo")
    assert result == {"file_tree": {}}
    assert temp_dir is None
