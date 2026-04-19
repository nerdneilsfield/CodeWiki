from codewiki.src.be.prompt_template import (
    REFINEMENT_PROMPT_VERSION,
    format_refinement_prompt,
)


def test_format_refinement_prompt_includes_constraints():
    prompt = format_refinement_prompt(
        parent_title="Auth Layer",
        parent_path="auth_layer",
        components_block="component listing here",
        current_depth=1,
        max_depth=3,
        min_components_for_split=6,
        min_distinct_files_for_split=4,
        output_language="en",
    )
    assert "Auth Layer" in prompt
    assert "auth_layer" in prompt
    assert "component listing here" in prompt
    assert "max_depth" in prompt or "depth 3" in prompt
    assert "6" in prompt
    assert "4" in prompt


def test_format_refinement_prompt_respects_language():
    prompt_en = format_refinement_prompt(
        parent_title="Auth",
        parent_path="auth",
        components_block="x",
        current_depth=1,
        max_depth=2,
        min_components_for_split=6,
        min_distinct_files_for_split=4,
        output_language="en",
    )
    prompt_zh = format_refinement_prompt(
        parent_title="Auth",
        parent_path="auth",
        components_block="x",
        current_depth=1,
        max_depth=2,
        min_components_for_split=6,
        min_distinct_files_for_split=4,
        output_language="zh",
    )
    assert prompt_en != prompt_zh


def test_refinement_prompt_version_constant():
    assert REFINEMENT_PROMPT_VERSION == "refinement-v1"
