from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from codewiki.src.be.pipeline import ModuleSummary, PipelineContext
from codewiki.src.be.stages.module_generation import ModuleGenerationStage
from codewiki.src.codewiki_config import CodeWikiConfig


def _make_context(tmp_path: Path) -> PipelineContext:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    config = CodeWikiConfig(
        repo_path=str(tmp_path / "repo"),
        docs_dir=str(docs_dir),
        llm_base_url="http://localhost",
        llm_api_key="x",
        main_model="m",
        cluster_model="c",
    )
    ctx = PipelineContext(config=config, working_dir=str(docs_dir))
    ctx.generator = MagicMock()
    ctx.module_tree = {
        "Top": {
            "module_id": "top",
            "_doc_filename": "top_new.md",
            "children": {},
        }
    }
    return ctx


@pytest.mark.asyncio
async def test_module_generation_stage_cleans_renamed_docs_and_updates_stamps(tmp_path):
    from codewiki.src.be.orphan_cleanup import update_mtime_stamps

    ctx = _make_context(tmp_path)
    docs_dir = Path(ctx.working_dir)
    old_md = docs_dir / "top_old.md"
    old_html = docs_dir / "top_old.html"
    old_md.write_text("old", encoding="utf-8")
    old_html.write_text("<html>old</html>", encoding="utf-8")
    update_mtime_stamps(str(docs_dir), ["top_old.md", "top_old.html"])

    new_md = docs_dir / "top_new.md"
    overview = docs_dir / "overview.md"

    async def fake_generate(*_args):
        new_md.write_text("new", encoding="utf-8")
        overview.write_text("overview", encoding="utf-8")
        return str(docs_dir), ModuleSummary(total=1)

    ctx.generator._generate_docs_from_tree = AsyncMock(side_effect=fake_generate)
    ctx.components = {"a": SimpleNamespace()}
    ctx.leaf_nodes = ["a"]
    ctx.rename_map = {"top_old.md": "top_new.md"}

    stage = ModuleGenerationStage()
    await stage.execute(ctx)

    assert not old_md.exists()
    assert not old_html.exists()
    stamp_path = docs_dir / ".codewiki_mtime_stamps.json"
    stamps = json.loads(stamp_path.read_text(encoding="utf-8"))
    assert "top_new.md" in stamps
    assert "overview.md" in stamps


@pytest.mark.asyncio
async def test_module_generation_stage_warns_when_renamed_doc_user_modified(tmp_path):
    from codewiki.src.be.orphan_cleanup import update_mtime_stamps

    ctx = _make_context(tmp_path)
    docs_dir = Path(ctx.working_dir)
    old_md = docs_dir / "top_old.md"
    old_md.write_text("old", encoding="utf-8")
    update_mtime_stamps(str(docs_dir), ["top_old.md"])
    old_md.write_text("user edit", encoding="utf-8")
    current = os.path.getmtime(old_md)
    os.utime(old_md, (current + 5.0, current + 5.0))
    new_md = docs_dir / "top_new.md"

    async def fake_generate(*_args):
        new_md.write_text("new", encoding="utf-8")
        return str(docs_dir), ModuleSummary(total=1)

    ctx.generator._generate_docs_from_tree = AsyncMock(side_effect=fake_generate)
    ctx.components = {"a": SimpleNamespace()}
    ctx.leaf_nodes = ["a"]
    ctx.rename_map = {"top_old.md": "top_new.md"}

    stage = ModuleGenerationStage()
    await stage.execute(ctx)

    assert old_md.exists()
    assert ctx.result.status == "degraded"
    assert any("user-modified" in warning for warning in ctx.result.warnings)
