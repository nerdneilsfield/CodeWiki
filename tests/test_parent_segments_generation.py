import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from codewiki.src.be.cache_manager import CacheManager
from codewiki.src.be.llm_usage import LLMCallResult
from codewiki.src.be.parent_segments import (
    force_invalidate_parent_segments,
    generate_or_assemble_parent_doc,
    generate_segment,
    parent_segment_path,
)


@pytest.fixture
def cache_dir(tmp_path):
    path = tmp_path / ".codewiki"
    path.mkdir()
    return str(path)


def _make_node(title, path, description, doc_filename, components, children):
    return {
        "module_id": path,
        "title": title,
        "path": path,
        "description": description,
        "_doc_filename": doc_filename,
        "components": components,
        "children": children,
    }


@pytest.mark.asyncio
async def test_generate_segment_writes_file_and_marks_cache_done(cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    middleware = MagicMock()
    middleware.call = AsyncMock(
        return_value=LLMCallResult(content="Generated opening text.", usage=None, model="fake")
    )
    output_path = parent_segment_path(cache_dir, "auth", "opening")
    await generate_segment(
        artifact_id="module:auth:segment:opening",
        input_hash="h1",
        prompt="Write an opening.",
        model="m",
        middleware=middleware,
        cache_manager=cache,
        output_path=output_path,
    )
    assert os.path.exists(output_path)
    assert cache.get_entry("module:auth:segment:opening").status == "valid"


@pytest.mark.asyncio
async def test_generate_segment_marks_failed_on_error(cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    middleware = MagicMock()
    middleware.call = AsyncMock(side_effect=RuntimeError("boom"))
    output_path = parent_segment_path(cache_dir, "auth", "opening")
    with pytest.raises(RuntimeError):
        await generate_segment(
            artifact_id="module:auth:segment:opening",
            input_hash="h1",
            prompt="x",
            model="m",
            middleware=middleware,
            cache_manager=cache,
            output_path=output_path,
        )
    assert cache.get_entry("module:auth:segment:opening").status == "failed"


@pytest.mark.asyncio
async def test_generate_or_assemble_parent_doc_writes_assembled(tmp_path, cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    parent = _make_node(
        title="Auth Layer",
        path="auth_layer",
        description="Authentication.",
        doc_filename="auth_layer.md",
        components=[],
        children={
            "Login": _make_node("Login", "login", "Login.", "auth_layer-login.md", ["a.py::A"], {}),
            "Logout": _make_node(
                "Logout", "logout", "Logout.", "auth_layer-logout.md", ["b.py::B"], {}
            ),
        },
    )
    (docs_dir / "auth_layer-login.md").write_text(
        "# Login\n\nLogin flow content.", encoding="utf-8"
    )
    (docs_dir / "auth_layer-logout.md").write_text(
        "# Logout\n\nLogout flow content.", encoding="utf-8"
    )
    cache.plan_task("module:login", output_file="auth_layer-login.md")
    cache.mark_done("module:login", input_hash="h_login", output_path="x", model="m")
    cache.plan_task("module:logout", output_file="auth_layer-logout.md")
    cache.mark_done("module:logout", input_hash="h_logout", output_path="x", model="m")

    async def fake_call(prompt, model=None, temperature=0.0, **_):
        if "opening paragraph" in prompt:
            return LLMCallResult(content="OPENING TEXT", usage=None, model="fake")
        if "architecture overview" in prompt:
            return LLMCallResult(content="OVERVIEW TEXT", usage=None, model="fake")
        if "Login" in prompt:
            return LLMCallResult(content="LOGIN SUMMARY", usage=None, model="fake")
        if "Logout" in prompt:
            return LLMCallResult(content="LOGOUT SUMMARY", usage=None, model="fake")
        return LLMCallResult(content="UNKNOWN", usage=None, model="fake")

    middleware = MagicMock()
    middleware.call = fake_call
    result = await generate_or_assemble_parent_doc(
        parent_doc_id="auth_layer",
        parent_node=parent,
        working_dir=str(docs_dir),
        cache_dir=cache_dir,
        cache_manager=cache,
        middleware=middleware,
        cluster_model="m",
        output_language="en",
    )
    assert result.output_path == str(docs_dir / "auth_layer.md")
    assembled = (docs_dir / "auth_layer.md").read_text(encoding="utf-8")
    assert "OPENING TEXT" in assembled
    assert "OVERVIEW TEXT" in assembled
    assert "LOGIN SUMMARY" in assembled
    parent_entry = cache.get_entry("module:auth_layer")
    assert parent_entry is None or parent_entry.status != "valid"


@pytest.mark.asyncio
async def test_generate_or_assemble_parent_doc_reuses_cached_segments(tmp_path, cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    parent = _make_node(
        title="Auth Layer",
        path="auth_layer",
        description="Authentication.",
        doc_filename="auth_layer.md",
        components=[],
        children={
            "Login": _make_node("Login", "login", "Login.", "auth_layer-login.md", [], {}),
        },
    )
    (docs_dir / "auth_layer-login.md").write_text("# Login\n\n", encoding="utf-8")
    cache.plan_task("module:login", output_file="auth_layer-login.md")
    cache.mark_done("module:login", input_hash="h_login", output_path="x", model="m")
    middleware = MagicMock()
    middleware.call = AsyncMock(return_value=LLMCallResult(content="X", usage=None, model="fake"))
    await generate_or_assemble_parent_doc(
        parent_doc_id="auth_layer",
        parent_node=parent,
        working_dir=str(docs_dir),
        cache_dir=cache_dir,
        cache_manager=cache,
        middleware=middleware,
        cluster_model="m",
        output_language="en",
    )
    first = middleware.call.await_count
    await generate_or_assemble_parent_doc(
        parent_doc_id="auth_layer",
        parent_node=parent,
        working_dir=str(docs_dir),
        cache_dir=cache_dir,
        cache_manager=cache,
        middleware=middleware,
        cluster_model="m",
        output_language="en",
    )
    assert middleware.call.await_count == first


def test_force_invalidate_marks_all_segments_stale(cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    parent = _make_node(
        title="Auth",
        path="auth",
        description=".",
        doc_filename="auth.md",
        components=[],
        children={
            "Login": _make_node("Login", "login", ".", "auth-login.md", [], {}),
            "Logout": _make_node("Logout", "logout", ".", "auth-logout.md", [], {}),
        },
    )
    for aid in (
        "module:auth:segment:opening",
        "module:auth:segment:overview",
        "module:auth:segment:child:login",
        "module:auth:segment:child:logout",
    ):
        cache.plan_task(aid, output_file=f"{aid.replace(':', '_')}.md")
        cache.mark_done(aid, input_hash="x", output_path="/tmp/x", model="m")
    force_invalidate_parent_segments(parent_doc_id="auth", parent_node=parent, cache_manager=cache)
    for aid in (
        "module:auth:segment:opening",
        "module:auth:segment:overview",
        "module:auth:segment:child:login",
        "module:auth:segment:child:logout",
    ):
        assert cache.get_entry(aid).status == "stale"
