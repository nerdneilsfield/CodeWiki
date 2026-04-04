from pathlib import Path

from codewiki.src.be.agent_tools.str_replace_editor import EditTool, WindowExpander


def test_window_expander_prefers_python_definition_boundaries():
    lines = [
        "x = 1",
        "",
        "@decorator",
        "def run():",
        "    pass",
        "",
        "y = 2",
    ]

    start, stop = WindowExpander(".py").expand_window(lines, 5, 5, max_added_lines=3)

    assert start == 4
    assert stop >= 5


def test_window_expander_rejects_invalid_viewport():
    expander = WindowExpander(".py")

    try:
        expander.expand_window(["a", "b"], 2, 1, 1)
    except ValueError as exc:
        assert "invalid viewport" in str(exc)
    else:
        raise AssertionError("expected ValueError for invalid viewport")


def test_edit_tool_validate_path_rejects_relative_and_outside(tmp_path):
    registry = {}
    tool = EditTool(registry, absolute_docs_path=tmp_path, allowed_base_path=tmp_path)

    assert tool.validate_path("view", Path("relative.md")) is False
    assert "not an absolute path" in tool.logs[-1]

    outside = Path("/tmp/outside.md")
    assert tool.validate_path("view", outside) is False
    assert "outside the allowed directory" in tool.logs[-1]


def test_edit_tool_create_file_requires_existing_parent(tmp_path):
    registry = {}
    tool = EditTool(registry, absolute_docs_path=tmp_path, allowed_base_path=tmp_path)
    target = tmp_path / "missing" / "doc.md"

    tool.create_file(target, "hello")

    assert "does not exist" in tool.logs[-1]


def test_edit_tool_str_replace_reports_multiple_occurrences(tmp_path):
    registry = {}
    tool = EditTool(registry, absolute_docs_path=tmp_path, allowed_base_path=tmp_path)
    target = tmp_path / "doc.md"
    target.write_text("alpha\nbeta\nalpha\n", encoding="utf-8")

    tool.str_replace(target, "alpha", "gamma")

    assert "Multiple occurrences of old_str" in tool.logs[-1]
