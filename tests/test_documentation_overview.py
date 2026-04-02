import json
from pathlib import Path
from types import SimpleNamespace
import warnings

import pytest

from codewiki.src.be.generation_state import DocTask, GenerationState
from codewiki.src.config import Config


def _make_config(tmp_path):
    return Config(
        repo_path=str(tmp_path / "repo"),
        output_dir=str(tmp_path / "out"),
        dependency_graph_dir=str(tmp_path / "graphs"),
        docs_dir=str(tmp_path / "docs"),
        max_depth=2,
        llm_base_url="http://localhost",
        llm_api_key="x",
        main_model="test/main",
        cluster_model="test/cluster",
        output_language="zh",
    )


def test_collect_child_doc_hashes_prefers_ledger_hashes(tmp_path):
    from codewiki.src.be.documentation_overview import OverviewContext, collect_child_doc_hashes

    tree = {
        "CLI Transport": {
            "module_id": "mod-cli",
            "children": {
                "io_abstractions": {
                    "module_id": "mod-io",
                    "children": {},
                }
            },
        }
    }
    state = GenerationState()
    state._add_task(
        DocTask(
            doc_id="module:mod-io",
            kind="module",
            module_path=["CLI Transport", "io_abstractions"],
            output_file="cli-io.md",
            status="completed",
            content_hash="hash-from-ledger",
        )
    )
    ctx = OverviewContext(
        config=_make_config(tmp_path),
        module_tree=tree,
        working_dir=str(tmp_path),
        gen_state=state,
    )

    hashes = collect_child_doc_hashes(ctx, ["CLI Transport"])

    assert hashes == {"io_abstractions": "hash-from-ledger"}


def test_build_overview_structure_uses_child_docs_from_ledger(tmp_path):
    from codewiki.src.be.documentation_overview import OverviewContext, build_overview_structure

    tree = {
        "CLI Transport": {
            "module_id": "mod-cli",
            "children": {
                "io_abstractions": {
                    "module_id": "mod-io",
                    "children": {},
                }
            },
        }
    }
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "cli-io.md").write_text("# IO\nDetails", encoding="utf-8")
    state = GenerationState()
    state._add_task(
        DocTask(
            doc_id="module:mod-io",
            kind="module",
            module_path=["CLI Transport", "io_abstractions"],
            output_file="cli-io.md",
            status="completed",
        )
    )
    ctx = OverviewContext(
        config=_make_config(tmp_path),
        module_tree=tree,
        working_dir=str(docs_dir),
        gen_state=state,
    )

    result = build_overview_structure(ctx, ["CLI Transport"])

    assert result["CLI Transport"]["children"]["io_abstractions"]["docs"] == "# IO\nDetails"


@pytest.mark.asyncio
async def test_generate_parent_module_docs_skips_when_input_hash_matches(tmp_path, monkeypatch):
    from codewiki.src.be.documentation_overview import OverviewContext, generate_parent_module_docs

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    output_path = docs_dir / "overview.md"
    output_path.write_text("existing overview\n" + ("x" * 200), encoding="utf-8")

    tree = {"CLI Transport": {"module_id": "mod-cli", "children": {}}}
    state = GenerationState()
    state._add_task(
        DocTask(
            doc_id="overview:root",
            kind="overview",
            module_path=[],
            output_file="overview.md",
            status="completed",
            input_hash="same-hash",
        )
    )

    ctx = OverviewContext(
        config=_make_config(tmp_path),
        module_tree=tree,
        working_dir=str(docs_dir),
        gen_state=state,
        call_llm=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not call llm")
        ),
    )

    monkeypatch.setattr(
        "codewiki.src.be.documentation_overview.hash_mapping",
        lambda mapping, extra=None: "same-hash",
    )

    result = await generate_parent_module_docs(ctx, [])

    assert result == tree
    assert output_path.read_text(encoding="utf-8").startswith("existing overview")


@pytest.mark.asyncio
async def test_generate_parent_module_docs_marks_completed_after_write(tmp_path):
    from codewiki.src.be.documentation_overview import OverviewContext, generate_parent_module_docs
    from codewiki.src.be.generation_state import GenerationStateManager

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    tree = {"CLI Transport": {"module_id": "mod-cli", "children": {}}}
    state = GenerationState()
    state._add_task(
        DocTask(
            doc_id="overview:root",
            kind="overview",
            module_path=[],
            output_file="overview.md",
            status="ready",
        )
    )
    manager = GenerationStateManager(state, str(docs_dir / "generation_state.json"))

    async def _call_llm(_prompt, _config):
        return "<OVERVIEW>\nGenerated content\n</OVERVIEW>"

    ctx = OverviewContext(
        config=_make_config(tmp_path),
        module_tree=tree,
        working_dir=str(docs_dir),
        gen_state=state,
        state_mgr=manager,
        call_llm=_call_llm,
    )

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        result = await generate_parent_module_docs(ctx, [])

    assert result == tree
    assert (docs_dir / "overview.md").read_text(encoding="utf-8") == "Generated content"
    assert state.get_task("overview:root").status == "completed"
    deprecations = [w for w in captured if issubclass(w.category, DeprecationWarning)]
    assert deprecations == []
