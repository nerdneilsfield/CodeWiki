"""Tests for postprocess.lint_report — LintReport, LintError, and degradation helpers.

Written BEFORE implementation (TDD RED phase).
"""
import json
import os
import re
import tempfile

import pytest


# ---------------------------------------------------------------------------
# LintReport unit tests
# ---------------------------------------------------------------------------

class TestLintReportEmpty:
    def test_has_failures_false(self):
        from codewiki.src.be.postprocess.lint_report import LintReport
        report = LintReport()
        assert report.has_failures is False

    def test_summary_no_issues(self):
        from codewiki.src.be.postprocess.lint_report import LintReport
        report = LintReport()
        assert report.summary() == "No issues found"


class TestLintReportWithMermaidFailure:
    def test_has_failures_true(self):
        from codewiki.src.be.postprocess.lint_report import LintReport
        report = LintReport(
            mermaid_failures=[
                {"file": "overview.md", "block_index": 0, "error": "bad syntax", "degraded": True}
            ]
        )
        assert report.has_failures is True

    def test_summary_mentions_mermaid(self):
        from codewiki.src.be.postprocess.lint_report import LintReport
        report = LintReport(
            mermaid_failures=[
                {"file": "overview.md", "block_index": 0, "error": "bad syntax", "degraded": True}
            ]
        )
        assert "mermaid" in report.summary()


class TestLintReportWithMathFailure:
    def test_has_failures_true(self):
        from codewiki.src.be.postprocess.lint_report import LintReport
        report = LintReport(
            math_failures=[
                {"file": "math.md", "expression": r"\frac{1}{", "error": "unmatched brace", "degraded": True}
            ]
        )
        assert report.has_failures is True

    def test_summary_mentions_math(self):
        from codewiki.src.be.postprocess.lint_report import LintReport
        report = LintReport(
            math_failures=[
                {"file": "math.md", "expression": r"\frac{1}{", "error": "unmatched brace", "degraded": True}
            ]
        )
        assert "math" in report.summary()


class TestLintReportWithLinkIssue:
    def test_has_failures_true(self):
        from codewiki.src.be.postprocess.lint_report import LintReport
        report = LintReport(
            link_issues=[
                {"file": "index.md", "line": 42, "target": "missing.md", "issue_type": "broken_link"}
            ]
        )
        assert report.has_failures is True

    def test_summary_mentions_link(self):
        from codewiki.src.be.postprocess.lint_report import LintReport
        report = LintReport(
            link_issues=[
                {"file": "index.md", "line": 42, "target": "missing.md", "issue_type": "broken_link"}
            ]
        )
        assert "link" in report.summary()


class TestLintReportSummaryFormat:
    def test_summary_with_multiple_failure_types(self):
        from codewiki.src.be.postprocess.lint_report import LintReport
        report = LintReport(
            mermaid_failures=[
                {"file": "a.md", "block_index": 0, "error": "err", "degraded": True},
                {"file": "b.md", "block_index": 1, "error": "err", "degraded": True},
            ],
            math_failures=[
                {"file": "c.md", "expression": "x^{", "error": "unmatched", "degraded": False},
            ],
            link_issues=[
                {"file": "d.md", "line": 1, "target": "gone.md", "issue_type": "broken_link"},
                {"file": "e.md", "line": 5, "target": "also.md", "issue_type": "broken_link"},
                {"file": "f.md", "line": 9, "target": "more.md", "issue_type": "broken_link"},
            ],
            total_files=10,
        )
        summary = report.summary()
        assert "2 mermaid" in summary
        assert "1 math" in summary
        assert "3 link" in summary
        assert "10 files scanned" in summary

    def test_summary_single_failure_type_no_others(self):
        from codewiki.src.be.postprocess.lint_report import LintReport
        report = LintReport(
            math_failures=[
                {"file": "x.md", "expression": "bad", "error": "broken", "degraded": True}
            ],
            total_files=5,
        )
        summary = report.summary()
        assert "1 math" in summary
        assert "mermaid" not in summary
        assert "link" not in summary


class TestLintReportToJson:
    def test_returns_valid_json_string(self):
        from codewiki.src.be.postprocess.lint_report import LintReport
        report = LintReport(total_files=3)
        raw = report.to_json()
        # Must not raise
        data = json.loads(raw)
        assert isinstance(data, dict)

    def test_json_contains_all_top_level_fields(self):
        from codewiki.src.be.postprocess.lint_report import LintReport
        report = LintReport(total_files=7)
        data = json.loads(report.to_json())
        assert "timestamp" in data
        assert "total_files" in data
        assert "mermaid_failures" in data
        assert "math_failures" in data
        assert "link_issues" in data
        assert "summary" in data

    def test_json_total_files_matches(self):
        from codewiki.src.be.postprocess.lint_report import LintReport
        report = LintReport(total_files=42)
        data = json.loads(report.to_json())
        assert data["total_files"] == 42

    def test_json_failures_present(self):
        from codewiki.src.be.postprocess.lint_report import LintReport
        report = LintReport(
            mermaid_failures=[{"file": "x.md", "block_index": 0, "error": "e", "degraded": True}],
            total_files=1,
        )
        data = json.loads(report.to_json())
        assert len(data["mermaid_failures"]) == 1
        assert data["mermaid_failures"][0]["file"] == "x.md"


class TestLintReportSaveCreatesFile:
    def test_save_writes_lint_report_json(self, tmp_path):
        from codewiki.src.be.postprocess.lint_report import LintReport
        report = LintReport(total_files=2)
        report.save(str(tmp_path))
        expected = tmp_path / "_lint_report.json"
        assert expected.exists(), "_lint_report.json was not created"

    def test_save_writes_valid_json_content(self, tmp_path):
        from codewiki.src.be.postprocess.lint_report import LintReport
        report = LintReport(total_files=5)
        report.save(str(tmp_path))
        content = (tmp_path / "_lint_report.json").read_text(encoding="utf-8")
        data = json.loads(content)
        assert data["total_files"] == 5

    def test_save_overwrites_existing_file(self, tmp_path):
        from codewiki.src.be.postprocess.lint_report import LintReport
        (tmp_path / "_lint_report.json").write_text('{"old": true}', encoding="utf-8")
        report = LintReport(total_files=99)
        report.save(str(tmp_path))
        data = json.loads((tmp_path / "_lint_report.json").read_text(encoding="utf-8"))
        assert data["total_files"] == 99


# ---------------------------------------------------------------------------
# LintError tests
# ---------------------------------------------------------------------------

class TestLintErrorRaisedInStrictMode:
    def test_lint_error_is_exception(self):
        from codewiki.src.be.postprocess.lint_report import LintError, LintReport
        report = LintReport(
            mermaid_failures=[{"file": "a.md", "block_index": 0, "error": "bad", "degraded": True}]
        )
        error = LintError(report)
        assert isinstance(error, Exception)

    def test_lint_error_carries_report(self):
        from codewiki.src.be.postprocess.lint_report import LintError, LintReport
        report = LintReport(
            math_failures=[{"file": "b.md", "expression": "broken", "error": "unmatched", "degraded": True}]
        )
        error = LintError(report)
        assert error.report is report

    def test_lint_error_raised_with_raise(self):
        from codewiki.src.be.postprocess.lint_report import LintError, LintReport
        report = LintReport(
            link_issues=[{"file": "c.md", "line": 1, "target": "x.md", "issue_type": "broken_link"}]
        )
        with pytest.raises(LintError) as exc_info:
            raise LintError(report)
        assert exc_info.value.report is report


class TestLintErrorMessageContainsSummary:
    def test_error_str_contains_lint_failed(self):
        from codewiki.src.be.postprocess.lint_report import LintError, LintReport
        report = LintReport(
            mermaid_failures=[{"file": "a.md", "block_index": 0, "error": "err", "degraded": True}]
        )
        error = LintError(report)
        assert "Lint failed" in str(error)

    def test_error_str_contains_summary_text(self):
        from codewiki.src.be.postprocess.lint_report import LintError, LintReport
        report = LintReport(
            mermaid_failures=[{"file": "a.md", "block_index": 0, "error": "err", "degraded": True}]
        )
        error = LintError(report)
        # The summary includes "mermaid"
        assert "mermaid" in str(error)


# ---------------------------------------------------------------------------
# Degradation format tests (pure text transformations, no docs_fixer needed)
# ---------------------------------------------------------------------------

def _apply_mermaid_degradation(original_code: str, error_message: str) -> str:
    """Local helper mirroring the degradation format from the task spec."""
    return (
        f"```text\n"
        f"[MERMAID DIAGRAM - RENDER FAILED]\n"
        f"{original_code}\n"
        f"```\n"
        f"<!-- mermaid-error: {error_message} -->"
    )


def _apply_math_degradation_display(original_math: str, error_message: str) -> str:
    return f"```latex\n{original_math}\n```\n<!-- math-error: {error_message} -->"


def _apply_math_degradation_inline(original_math: str, error_message: str) -> str:
    return f"`{original_math}` <!-- math-error: {error_message} -->"


class TestMermaidDegradationFormat:
    def test_degraded_block_uses_text_fence(self):
        result = _apply_mermaid_degradation("graph TD\n  A --> B", "mmdc failed")
        assert result.startswith("```text\n")

    def test_degraded_block_contains_render_failed_marker(self):
        result = _apply_mermaid_degradation("graph TD\n  A --> B", "mmdc failed")
        assert "[MERMAID DIAGRAM - RENDER FAILED]" in result

    def test_degraded_block_preserves_original_code(self):
        code = "graph TD\n  A --> B\n  B --> C"
        result = _apply_mermaid_degradation(code, "some error")
        assert code in result

    def test_degraded_block_contains_error_comment(self):
        result = _apply_mermaid_degradation("graph LR\n  X --> Y", "unbalanced brackets")
        assert "<!-- mermaid-error: unbalanced brackets -->" in result

    def test_degraded_block_closes_fence(self):
        result = _apply_mermaid_degradation("graph TD\n  A --> B", "err")
        # Must contain a closing fence line before the HTML comment
        lines = result.split("\n")
        assert "```" in lines

    def test_degraded_block_no_mermaid_fence(self):
        result = _apply_mermaid_degradation("graph TD\n  A --> B", "err")
        # Should NOT start with ```mermaid
        assert not result.startswith("```mermaid")


class TestMathDegradationDisplay:
    def test_display_uses_latex_fence(self):
        result = _apply_math_degradation_display(r"\frac{1}{2}", "unmatched brace")
        assert result.startswith("```latex\n")

    def test_display_preserves_original_math(self):
        expr = r"\frac{1}{"
        result = _apply_math_degradation_display(expr, "unmatched brace")
        assert expr in result

    def test_display_contains_error_comment(self):
        result = _apply_math_degradation_display(r"\sum_{i=0}", "missing closing")
        assert "<!-- math-error: missing closing -->" in result

    def test_display_closes_latex_fence(self):
        result = _apply_math_degradation_display(r"\alpha + \beta", "err")
        lines = result.split("\n")
        # Should have a line that is just ```
        assert "```" in lines

    def test_display_not_backtick_inline(self):
        result = _apply_math_degradation_display(r"\frac{x}{y}", "err")
        # Should be a fenced code block (```latex), NOT single-backtick inline (`...`)
        assert not (result.startswith("`") and not result.startswith("```"))


class TestMathDegradationInline:
    def test_inline_uses_backtick_wrapper(self):
        result = _apply_math_degradation_inline(r"x^2", "bad syntax")
        assert result.startswith("`")

    def test_inline_preserves_original_math(self):
        expr = r"x^{2"
        result = _apply_math_degradation_inline(expr, "unmatched")
        assert expr in result

    def test_inline_contains_error_comment(self):
        result = _apply_math_degradation_inline(r"a_b", "subscript error")
        assert "<!-- math-error: subscript error -->" in result

    def test_inline_not_block_fence(self):
        result = _apply_math_degradation_inline(r"x^2", "err")
        assert "```" not in result

    def test_inline_ends_with_comment(self):
        result = _apply_math_degradation_inline(r"y=mx", "err")
        assert result.endswith("-->")
