"""Tests for overview prompt language hardening."""


def test_format_overview_prompt_uses_output_language_block_for_non_english():
    from codewiki.src.be.prompt_template import format_overview_prompt

    prompt = format_overview_prompt(
        name="demo-repo",
        repo_structure="{}",
        is_repo=True,
        output_language="zh",
    )

    assert "<OUTPUT_LANGUAGE>" in prompt
    assert "Write ALL documentation content in Chinese (Simplified)." in prompt
    assert "IMPORTANT: Write the overview content in Chinese (Simplified)." not in prompt

