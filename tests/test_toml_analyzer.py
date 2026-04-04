from codewiki.src.be.dependency_analyzer.analyzers.toml import analyze_toml_file


def test_toml_analyzer_extracts_top_level_tables_and_arrays(tmp_path):
    content = """
[tool.poetry]
name = "demo"

[[package.source]]
name = "pypi"

[build-system]
requires = ["setuptools"]
""".strip()

    nodes, calls = analyze_toml_file(
        str(tmp_path / "pyproject.toml"),
        content,
        repo_path=str(tmp_path),
    )

    names = {node.name for node in nodes}
    component_types = {node.name: node.component_type for node in nodes}

    assert names == {"tool", "package", "build-system"}
    assert component_types["tool"] == "table"
    assert component_types["package"] == "table_array"
    assert component_types["build-system"] == "table"
    assert calls == []


def test_toml_analyzer_deduplicates_same_top_level_section(tmp_path):
    content = """
[tool.alpha]
enabled = true

[tool.beta]
enabled = false
""".strip()

    nodes, _ = analyze_toml_file(
        str(tmp_path / "pyproject.toml"),
        content,
        repo_path=str(tmp_path),
    )

    assert [node.name for node in nodes] == ["tool"]
