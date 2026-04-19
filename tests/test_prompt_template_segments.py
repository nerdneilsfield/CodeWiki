from codewiki.src.be.prompt_template import (
    format_parent_child_summary_prompt,
    format_parent_opening_prompt,
    format_parent_overview_prompt,
)


def test_opening_prompt_uses_parent_metadata():
    prompt = format_parent_opening_prompt(
        title="Auth Layer",
        path="auth_layer",
        description="Authentication module.",
        output_language="en",
    )
    assert "Auth Layer" in prompt
    assert "auth_layer" in prompt
    assert "Authentication module." in prompt


def test_overview_prompt_lists_children():
    prompt = format_parent_overview_prompt(
        title="Auth Layer",
        path="auth_layer",
        description="Auth.",
        children=[
            {"title": "Login", "path": "login", "description": "Login flow."},
            {"title": "Logout", "path": "logout", "description": "Logout."},
        ],
        output_language="en",
    )
    assert "Login" in prompt
    assert "Logout" in prompt
    assert "login" in prompt


def test_child_summary_prompt_focuses_on_one_child():
    prompt = format_parent_child_summary_prompt(
        parent_title="Auth Layer",
        child_title="Login",
        child_path="login",
        child_description="Login flow.",
        child_doc_excerpt="The login module handles user authentication tokens...",
        output_language="en",
    )
    assert "Login" in prompt
    assert "Auth Layer" in prompt
    assert "Login flow." in prompt
