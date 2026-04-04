import json
from pathlib import Path
from unittest.mock import MagicMock, patch


def test_load_hash_cache_returns_empty_on_invalid_json(tmp_path):
    from codewiki.src.be.docs_fixer import _load_hash_cache

    (tmp_path / ".fix_docs_cache.json").write_text("{bad json", encoding="utf-8")

    assert _load_hash_cache(tmp_path) == {}


def test_save_hash_cache_ignores_write_failures(tmp_path):
    from codewiki.src.be.docs_fixer import _save_hash_cache

    with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
        _save_hash_cache(tmp_path, {"overview.md": "abc"})

    assert not (tmp_path / ".fix_docs_cache.json").exists()


def test_fix_math_in_text_degrades_when_repair_is_unchanged():
    from codewiki.src.be.docs_fixer import FixStats, _fix_math_in_text
    from codewiki.src.be.postprocess.lint_report import LintReport

    stats = FixStats()
    report = LintReport()
    config = MagicMock()

    with patch(
        "codewiki.src.be.docs_fixer._llm_repair_math",
        return_value=r"\frac{1}{",
    ):
        result = _fix_math_in_text(
            "Broken $$\\frac{1}{$$ formula",
            config,
            stats,
            report=report,
            filename="math.md",
        )

    assert "```latex" in result
    assert "math-error:" in result
    assert stats.math_invalid == 1
    assert stats.math_failed == 1
    assert report.math_failures[0]["file"] == "math.md"


def test_fix_mermaid_in_text_degrades_when_repair_is_unchanged():
    from codewiki.src.be.docs_fixer import FixStats, _fix_mermaid_in_text
    from codewiki.src.be.postprocess.lint_report import LintReport

    stats = FixStats()
    report = LintReport()
    config = MagicMock()
    text = '```mermaid\nA["it\'s bad"] --> B\n```'

    with (
        patch("codewiki.src.be.docs_fixer._find_mmdc", return_value=None),
        patch("codewiki.src.be.docs_fixer._llm_repair", return_value='A["it\'s bad"] --> B'),
    ):
        result = _fix_mermaid_in_text(
            text,
            config,
            stats,
            report=report,
            filename="diagram.md",
        )

    assert "[MERMAID DIAGRAM - RENDER FAILED]" in result
    assert "mermaid-error:" in result
    assert stats.diagrams_invalid == 1
    assert stats.diagrams_failed == 1
    assert report.mermaid_failures[0]["file"] == "diagram.md"


def test_fix_docs_strict_mode_raises_lint_error(tmp_path):
    from codewiki.src.be.docs_fixer import fix_docs
    from codewiki.src.be.postprocess.lint_report import LintError

    (tmp_path / "overview.md").write_text("Hello", encoding="utf-8")
    config = MagicMock()
    config.postprocess_fix_links = False
    config.postprocess_strict = True

    with patch(
        "codewiki.src.be.docs_fixer._fix_mermaid_in_text",
        side_effect=lambda text,
        *args,
        report=None,
        filename="",
        **kwargs: report.link_issues.append(
            {
                "file": filename or "overview.md",
                "line": 1,
                "target": "missing.md",
                "issue_type": "broken_link",
            }
        )
        or text,
    ):
        try:
            fix_docs(str(tmp_path), config)
        except LintError as exc:
            payload = json.loads((tmp_path / "_lint_report.json").read_text(encoding="utf-8"))
            assert exc.report.has_failures is True
            assert payload["link_issues"][0]["target"] == "missing.md"
        else:
            raise AssertionError("Expected LintError in strict mode")
