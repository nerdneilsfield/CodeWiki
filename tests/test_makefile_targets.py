from pathlib import Path
import subprocess


def test_makefile_exposes_quality_targets():
    root = Path(__file__).resolve().parents[1]

    help_result = subprocess.run(
        ["make", "help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert help_result.returncode == 0
    help_output = help_result.stdout
    for target in (
        "install",
        "lint",
        "format",
        "format-check",
        "typecheck",
        "test",
        "test-v7",
        "hooks",
        "check",
        "quality",
    ):
        assert target in help_output


def test_makefile_targets_expand_to_expected_commands():
    root = Path(__file__).resolve().parents[1]

    expected = {
        "install": "uv pip install -e .",
        "lint": "uv run ruff check . --output-format concise",
        "format": "uv run ruff format .",
        "format-check": "uv run ruff format --check .",
        "typecheck": "uv run ty check",
        "test": "uv run python -m pytest tests/ --cov=codewiki --cov-report=term-missing -q",
        "hooks": "uv run pre-commit run --all-files",
    }

    for target, command in expected.items():
        result = subprocess.run(
            ["make", "-n", target],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert command in result.stdout
