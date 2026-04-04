from __future__ import annotations


class TestCleanupFormula:
    def test_backspace_beta(self):
        from codewiki.src.be.postprocess.math_validator import cleanup_formula

        assert r"\beta" in cleanup_formula("\x08eta")

    def test_double_superscript(self):
        from codewiki.src.be.postprocess.math_validator import cleanup_formula

        result = cleanup_formula("x^a^b")
        assert "^" in result
        assert result != "x^a^b"

    def test_left_ceil_repair(self):
        from codewiki.src.be.postprocess.math_validator import cleanup_formula

        result = cleanup_formula(r"\left ceil")
        assert r"\left\lceil" in result

    def test_noop_on_valid(self):
        from codewiki.src.be.postprocess.math_validator import cleanup_formula

        valid = r"\frac{1}{2}"
        assert cleanup_formula(valid) == valid


class TestValidateFormula:
    def test_valid_formula_returns_empty(self):
        from codewiki.src.be.postprocess.math_validator import validate_formula

        assert validate_formula(r"\frac{1}{2}", display_mode=True) == []

    def test_unmatched_brace_detected(self):
        from codewiki.src.be.postprocess.math_validator import validate_formula

        errors = validate_formula(r"\frac{1}{", display_mode=True)
        assert len(errors) > 0


class TestExtractMathSpans:
    def test_extracts_display_and_inline(self):
        from codewiki.src.be.postprocess.math_validator import extract_math_spans

        text = "Inline $x^2$ and display $$\\sum_{i=0}^n i$$"
        spans = extract_math_spans(text)
        assert len(spans) == 2
        delimiters = {s.delimiter for s in spans}
        assert delimiters == {"$", "$$"}

    def test_escaped_dollar_not_matched(self):
        from codewiki.src.be.postprocess.math_validator import extract_math_spans

        text = r"Price is \$5 and \$10"
        spans = extract_math_spans(text)
        assert len(spans) == 0

    def test_code_block_not_matched(self):
        from codewiki.src.be.postprocess.math_validator import extract_math_spans

        text = "```\n$x^2$\n```"
        spans = extract_math_spans(text)
        assert len(spans) == 0

    def test_bracket_delimiters(self):
        from codewiki.src.be.postprocess.math_validator import extract_math_spans

        text = r"Display \[x + y\] and inline \(a + b\)"
        spans = extract_math_spans(text)
        assert len(spans) == 2

    def test_escaped_dollar_inside_code_block_no_interaction(self):
        from codewiki.src.be.postprocess.math_validator import extract_math_spans

        text = "```\n\\$5 and $x^2$\n```\nOutside \\$10 and $y^2$"
        spans = extract_math_spans(text)
        assert len(spans) == 1
        assert spans[0].content == "y^2"


class TestBuildRepairPrompt:
    def test_prompt_contains_json(self):
        from codewiki.src.be.postprocess.math_validator import (
            FormulaIssue,
            FormulaSpan,
            build_repair_prompt,
        )

        span = FormulaSpan(start=0, end=10, delimiter="$$", content=r"\frac{1}{", line=1)
        issue = FormulaIssue(
            issue_id="a:0",
            span=span,
            errors=["unmatched brace"],
            cleaned=r"\frac{1}{",
        )
        prompt = build_repair_prompt([issue])
        assert '"id"' in prompt
        assert '"latex"' in prompt
        assert "a:0" in prompt


class TestParseRepairResponse:
    def test_parses_valid_json(self):
        from codewiki.src.be.postprocess.math_validator import parse_repair_response

        response = '{"items": [{"id": "a:0", "latex": "\\\\frac{1}{2}"}]}'
        result = parse_repair_response(response)
        assert result["a:0"] == r"\frac{1}{2}"

    def test_returns_empty_on_bad_json(self):
        from codewiki.src.be.postprocess.math_validator import parse_repair_response

        assert parse_repair_response("not json") == {}


class TestRepairModelChain:
    def test_build_model_chain_uses_main_model_when_repair_empty(self):
        from codewiki.src.be.postprocess.math_validator import _build_model_chain
        from codewiki.src.codewiki_config import PostprocessConfig

        pp = PostprocessConfig(repair_model="", repair_fallback_1="m2", repair_fallback_2="")
        chain = _build_model_chain(pp, main_model="m1")
        assert chain == ["m1", "m2"]

    def test_build_model_chain_full(self):
        from codewiki.src.be.postprocess.math_validator import _build_model_chain
        from codewiki.src.codewiki_config import PostprocessConfig

        pp = PostprocessConfig(repair_model="r", repair_fallback_1="f1", repair_fallback_2="f2")
        chain = _build_model_chain(pp, main_model="m")
        assert chain == ["r", "f1", "f2"]
