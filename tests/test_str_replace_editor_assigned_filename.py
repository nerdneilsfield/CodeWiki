from types import SimpleNamespace

import pytest

from codewiki.src.be.agent_tools.str_replace_editor import str_replace_editor


class _FakeEditTool:
    last_call = None

    def __init__(self, *_args, **_kwargs):
        self.logs = ["ok"]

    def __call__(self, **kwargs):
        _FakeEditTool.last_call = kwargs


@pytest.mark.asyncio
async def test_str_replace_editor_create_autocorrects_to_assigned_filename(monkeypatch, tmp_path):
    async def _fake_validate(*_args, **_kwargs):
        return "ok"

    monkeypatch.setattr("codewiki.src.be.agent_tools.str_replace_editor.EditTool", _FakeEditTool)
    monkeypatch.setattr(
        "codewiki.src.be.agent_tools.str_replace_editor.validate_mermaid_diagrams",
        _fake_validate,
    )

    deps = SimpleNamespace(
        absolute_docs_path=str(tmp_path / "docs"),
        absolute_repo_path=str(tmp_path / "repo"),
        registry={},
        assigned_doc_filename="canonical.md",
    )

    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "repo").mkdir(parents=True)

    ctx = SimpleNamespace(deps=deps)
    await str_replace_editor(
        ctx,
        working_dir="docs",
        command="create",
        path="wrong-name.md",
        file_text="# content",
    )

    assert _FakeEditTool.last_call is not None
    assert _FakeEditTool.last_call["path"].endswith("canonical.md")
