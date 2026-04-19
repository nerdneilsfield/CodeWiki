"""The legacy sub-module tool must not mutate the frozen tree."""

import asyncio
import json
from unittest.mock import MagicMock

from codewiki.src.be.agent_tools.generate_sub_module_documentations import (
    generate_sub_module_documentation,
)
from codewiki.src.config import MODULE_TREE_FILENAME


def _make_deps(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    frozen = {"Top": {"module_id": "top", "components": [], "children": {}}}
    (docs / MODULE_TREE_FILENAME).write_text(json.dumps(frozen), encoding="utf-8")
    deps = MagicMock()
    deps.absolute_docs_path = str(docs)
    deps.module_tree = frozen
    deps.path_to_current_module = ["Top"]
    return deps, docs


def test_tool_does_not_overwrite_module_tree_json(tmp_path):
    deps, docs = _make_deps(tmp_path)
    ctx = MagicMock()
    ctx.deps = deps
    before = (docs / MODULE_TREE_FILENAME).read_text(encoding="utf-8")
    asyncio.run(
        generate_sub_module_documentation(
            ctx,
            sub_module_specs={"new_child": ["x.py::X"]},
        )
    )
    after = (docs / MODULE_TREE_FILENAME).read_text(encoding="utf-8")
    assert before == after


def test_tool_returns_helpful_message(tmp_path):
    deps, _ = _make_deps(tmp_path)
    ctx = MagicMock()
    ctx.deps = deps
    result = asyncio.run(
        generate_sub_module_documentation(
            ctx,
            sub_module_specs={"new_child": ["x.py::X"]},
        )
    )
    assert isinstance(result, str)
    assert "frozen" in result.lower() or "refinement" in result.lower()
