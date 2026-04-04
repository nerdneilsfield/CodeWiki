import subprocess
from unittest.mock import MagicMock, patch

import pytest


class TestCleanupMermaid:
    def test_smart_quotes_replaced(self):
        from codewiki.src.be.postprocess.mermaid_validator import cleanup_mermaid

        result = cleanup_mermaid("\u201cHello\u201d")
        assert "\u201c" not in result
        assert '"' in result

    def test_escaped_newline_in_label(self):
        from codewiki.src.be.postprocess.mermaid_validator import cleanup_mermaid

        result = cleanup_mermaid('A["line1\\nline2"]')
        assert "<br/>" in result
        assert "\\n" not in result

    def test_escaped_newline_outside_label_becomes_real_newline(self):
        from codewiki.src.be.postprocess.mermaid_validator import cleanup_mermaid

        result = cleanup_mermaid("A -->|step\\nflow| B")
        assert "<br/>" not in result
        assert "step\nflow" in result

    def test_edge_label_repair(self):
        from codewiki.src.be.postprocess.mermaid_validator import cleanup_mermaid

        result = cleanup_mermaid("A -->[label] B")
        assert "-->|label|" in result

    def test_unbalanced_bracket_closed(self):
        from codewiki.src.be.postprocess.mermaid_validator import cleanup_mermaid

        result = cleanup_mermaid("A[open label")
        assert result.count("[") == result.count("]")

    def test_multi_source_expanded(self):
        from codewiki.src.be.postprocess.mermaid_validator import cleanup_mermaid

        result = cleanup_mermaid("A & B --> C")
        assert "A --> C" in result
        assert "B --> C" in result


class TestExtractMermaidSpans:
    def test_extracts_mermaid_blocks(self):
        from codewiki.src.be.postprocess.mermaid_validator import extract_mermaid_spans

        text = "```mermaid\ngraph TD\nA-->B\n```"
        spans = extract_mermaid_spans(text)
        assert len(spans) == 1
        assert "graph TD" in spans[0].content

    def test_no_match_in_other_fences(self):
        from codewiki.src.be.postprocess.mermaid_validator import extract_mermaid_spans

        text = "```python\nprint('hello')\n```"
        assert extract_mermaid_spans(text) == []


class TestValidateMermaid:
    def test_mmdc_valid_returns_none(self):
        from codewiki.src.be.postprocess.mermaid_validator import validate_with_mmdc

        proc = MagicMock(returncode=0)
        with (
            patch(
                "codewiki.src.be.postprocess.mermaid_validator._find_mmdc",
                return_value="/usr/bin/mmdc",
            ),
            patch(
                "codewiki.src.be.postprocess.mermaid_validator.subprocess.run", return_value=proc
            ),
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.stat", return_value=MagicMock(st_size=100)),
        ):
            assert validate_with_mmdc("graph TD\nA-->B") is None

    def test_mmdc_timeout_returns_error(self):
        from codewiki.src.be.postprocess.mermaid_validator import validate_with_mmdc

        with (
            patch(
                "codewiki.src.be.postprocess.mermaid_validator._find_mmdc",
                return_value="/usr/bin/mmdc",
            ),
            patch(
                "codewiki.src.be.postprocess.mermaid_validator.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd=["mmdc"], timeout=30),
            ),
        ):
            assert validate_with_mmdc("graph TD\nA-->B") == "mmdc timed out"

    def test_regex_detects_bad_unicode(self):
        from codewiki.src.be.postprocess.mermaid_validator import validate_with_regex

        issues = validate_with_regex("A[∃x ∈ S] --> B")
        assert any("Unicode" in i for i in issues)


class TestBuildRepairPrompt:
    def test_prompt_contains_mermaid_constraints(self):
        from codewiki.src.be.postprocess.mermaid_validator import (
            MermaidIssue,
            MermaidSpan,
            build_repair_prompt,
        )

        span = MermaidSpan(start=0, end=20, content="graph TD\nA[bad", line=1)
        issue = MermaidIssue(issue_id="a:0", span=span, errors=["unbalanced"])
        prompt = build_repair_prompt([issue])
        assert "graph TD" in prompt
        assert '"id"' in prompt


class TestParseRepairResponse:
    def test_parses_valid_json(self):
        from codewiki.src.be.postprocess.mermaid_validator import parse_repair_response

        response = '{"items": [{"id": "a:0", "mermaid": "graph TD\\nA-->B"}]}'
        result = parse_repair_response(response)
        assert "a:0" in result

    def test_returns_empty_on_bad_json(self):
        from codewiki.src.be.postprocess.mermaid_validator import parse_repair_response

        assert parse_repair_response("not json") == {}


class TestRepairBatchSync:
    def test_accumulates_partial_results_across_models(self):
        from codewiki.src.be.postprocess.mermaid_validator import (
            MermaidIssue,
            MermaidSpan,
            repair_batch_sync,
        )
        from codewiki.src.codewiki_config import CodeWikiConfig, PostprocessConfig

        issues = [
            MermaidIssue("a:0", MermaidSpan(0, 10, "graph TD\nA-->B", 1), ["bad"]),
            MermaidIssue("a:1", MermaidSpan(11, 20, "graph TD\nB-->C", 3), ["bad"]),
        ]
        config = CodeWikiConfig(
            repo_path="/tmp",
            docs_dir="/tmp/docs",
            main_model="openai/gpt-4o-mini",
            postprocess=PostprocessConfig(
                repair_model="openai/gpt-4o-mini",
                repair_fallback_1="openai/gpt-4o",
            ),
        )
        first = MagicMock(content='{"items":[{"id":"a:0","mermaid":"graph TD\\nA-->B"}]}')
        second = MagicMock(content='{"items":[{"id":"a:1","mermaid":"graph TD\\nB-->C"}]}')

        with patch(
            "codewiki.src.be.postprocess.mermaid_validator.with_retry_sync",
            side_effect=[first, second],
        ):
            repaired = repair_batch_sync(issues, config, config.postprocess)

        assert repaired == {
            "a:0": "graph TD\nA-->B",
            "a:1": "graph TD\nB-->C",
        }
