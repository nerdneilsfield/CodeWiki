from pathlib import Path


def test_readme_and_development_document_ruff_ty_workflow():
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    development = (root / "DEVELOPMENT.md").read_text(encoding="utf-8")

    assert "uv run ruff check ." in readme
    assert "uv run ruff format --check ." in readme
    assert "uv run ty check" in readme
    assert "mypy" not in readme.lower()

    assert "pre-commit install" in development
    assert "uv run ruff check ." in development
    assert "uv run ruff format --check ." in development
    assert "uv run ty check" in development
