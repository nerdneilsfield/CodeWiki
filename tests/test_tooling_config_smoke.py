from pathlib import Path
import tomllib


def test_pyproject_contains_tooling_sections():
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    assert "tool" in data
    assert "ruff" in data["tool"]
    assert "lint" in data["tool"]["ruff"]
    assert "format" in data["tool"]["ruff"]
    assert "pytest" in data["tool"]
    assert "ini_options" in data["tool"]["pytest"]
    assert "ty" in data["tool"]
    assert "environment" in data["tool"]["ty"]
    assert "src" in data["tool"]["ty"]
    assert "mypy" not in data["tool"]
