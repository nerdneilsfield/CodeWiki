from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_submodule_completion_flushes_state(tmp_path):
    from codewiki.src.be.agent_tools.deps import CodeWikiDeps
    from codewiki.src.be.agent_tools.generate_sub_module_documentations import (
        generate_sub_module_documentation,
    )
    from codewiki.src.utils import module_doc_filename

    sub_name = "sub_module"
    assigned_filename = module_doc_filename([sub_name])
    (tmp_path / assigned_filename).write_text("# done\n", encoding="utf-8")

    state_mgr = AsyncMock()
    gen_state = SimpleNamespace(get_task=lambda _doc_id: None)

    deps = CodeWikiDeps(
        absolute_docs_path=str(tmp_path),
        absolute_repo_path=str(tmp_path),
        registry={},
        components={
            "component_a": SimpleNamespace(
                file_path="src/a.py",
                relative_path="src/a.py",
                source_code="def a(): pass\n",
                component_type="function",
            )
        },
        path_to_current_module=[],
        current_module_name="parent",
        module_tree={},
        max_depth=2,
        current_depth=0,
        config=SimpleNamespace(
            output_language="en",
            max_token_per_leaf_module=16_000,
            long_context_threshold=200_000,
        ),
        custom_instructions=None,
        module_tree_manager=None,
        fallback_models=object(),
        long_context_model=None,
        gen_state=gen_state,
        state_mgr=state_mgr,
    )

    class FakeResult:
        def all_messages(self):
            return []

        def usage(self):
            return None

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            return None

        async def run(self, *args, **kwargs):
            return FakeResult()

    ctx = SimpleNamespace(deps=deps)

    with (
        patch("codewiki.src.be.agent_tools.generate_sub_module_documentations.Agent", FakeAgent),
        patch(
            "codewiki.src.be.agent_tools.generate_sub_module_documentations.format_user_prompt",
            return_value="prompt",
        ),
    ):
        await generate_sub_module_documentation(ctx, {sub_name: ["component_a"]})

    assert state_mgr.register_discovered_task.await_count == 1
    assert state_mgr.mark_completed.await_count == 1
    assert state_mgr.flush.await_count == 2
