# tests/test_guide_generator.py
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

from codewiki.src.be.cache_manager import CacheManager
from codewiki.src.be.guide_generator import GuideGenerator, _PROMPT_VERSIONS


def _minimal_config():
    """Return a minimal Config-like object for testing."""
    from codewiki.src.codewiki_config import CodeWikiConfig

    return CodeWikiConfig(
        repo_path="/tmp/fake-repo",
        output_dir="/tmp/output",
        dependency_graph_dir="/tmp/dg",
        docs_dir="/tmp/docs",
        max_depth=2,
        llm_base_url="http://localhost:4000/",
        llm_api_key="sk-test",
        main_model="test-model",
        cluster_model="test-model",
    )


def test_should_regenerate_when_no_cache():
    with tempfile.TemporaryDirectory() as wd:
        gen = GuideGenerator(
            config=_minimal_config(),
            components={},
            module_tree={},
            working_dir=wd,
        )
        assert gen._should_regenerate("getting_started", []) is True


def test_guide_cache_path_uses_internal_subdir():
    with tempfile.TemporaryDirectory() as wd:
        gen = GuideGenerator(
            config=_minimal_config(),
            components={},
            module_tree={},
            working_dir=wd,
        )
        assert gen._cache_path().endswith(".codewiki/_guide_cache.json")


def test_should_not_regenerate_when_hash_matches():
    with tempfile.TemporaryDirectory() as wd:
        # Create a fake input file
        inp = os.path.join(wd, "input.md")
        Path(inp).write_text("hello", encoding="utf-8")
        out = os.path.join(wd, "guide-getting-started.md")
        Path(out).write_text("# Getting Started\n" + "Content here.\n" * 10, encoding="utf-8")

        gen = GuideGenerator(
            config=_minimal_config(),
            components={},
            module_tree={},
            working_dir=wd,
        )
        # Simulate a cache entry
        gen._update_cache("getting_started", [inp], [out])
        gen._save_cache()

        # Reload
        gen2 = GuideGenerator(
            config=_minimal_config(),
            components={},
            module_tree={},
            working_dir=wd,
        )
        assert gen2._should_regenerate("getting_started", [inp]) is False


def test_should_regenerate_when_input_changes():
    with tempfile.TemporaryDirectory() as wd:
        inp = os.path.join(wd, "input.md")
        Path(inp).write_text("hello", encoding="utf-8")
        out = os.path.join(wd, "guide-getting-started.md")
        Path(out).write_text("# Getting Started", encoding="utf-8")

        gen = GuideGenerator(
            config=_minimal_config(),
            components={},
            module_tree={},
            working_dir=wd,
        )
        gen._update_cache("getting_started", [inp], [out])
        gen._save_cache()

        # Mutate the input
        Path(inp).write_text("changed!", encoding="utf-8")

        gen2 = GuideGenerator(
            config=_minimal_config(),
            components={},
            module_tree={},
            working_dir=wd,
        )
        assert gen2._should_regenerate("getting_started", [inp]) is True


def test_should_not_regenerate_when_cache_manager_entry_valid():
    with tempfile.TemporaryDirectory() as wd:
        inp = os.path.join(wd, "input.md")
        Path(inp).write_text("hello", encoding="utf-8")
        out = os.path.join(wd, "guide-getting-started.md")
        Path(out).write_text("# Getting Started\n" + "Content here.\n" * 10, encoding="utf-8")
        cache_manager = CacheManager(os.path.join(wd, ".codewiki"))

        gen = GuideGenerator(
            config=_minimal_config(),
            components={},
            module_tree={},
            working_dir=wd,
            cache_manager=cache_manager,
        )
        input_hash = gen._compute_guide_input_hash([inp], "getting_started")
        cache_manager.mark_done(
            "guide:getting_started",
            input_hash=input_hash,
            output_path=out,
            output_file=os.path.basename(out),
        )

        assert gen._should_regenerate("getting_started", [inp]) is False


def test_update_cache_marks_cache_manager_entry():
    with tempfile.TemporaryDirectory() as wd:
        inp = os.path.join(wd, "input.md")
        Path(inp).write_text("hello", encoding="utf-8")
        out = os.path.join(wd, "guide-getting-started.md")
        Path(out).write_text("# Getting Started\n" + "Content here.\n" * 10, encoding="utf-8")
        cache_manager = CacheManager(os.path.join(wd, ".codewiki"))

        gen = GuideGenerator(
            config=_minimal_config(),
            components={},
            module_tree={},
            working_dir=wd,
            cache_manager=cache_manager,
        )
        gen._update_cache("getting_started", [inp], [out])

        input_hash = gen._compute_guide_input_hash([inp], "getting_started")
        assert cache_manager.is_valid("guide:getting_started", input_hash) is True


# ── Contract tests (Task 7b) ─────────────────────────────────────────────────


def test_sanitize_slug_strips_unsafe_chars():
    assert GuideGenerator._sanitize_slug("hello-world") == "hello-world"
    assert GuideGenerator._sanitize_slug("../../../etc/passwd") == "etcpasswd"
    assert GuideGenerator._sanitize_slug("Hello World!") == "helloworld"
    assert GuideGenerator._sanitize_slug("section_1:overview") == "section1overview"
    assert GuideGenerator._sanitize_slug("") == "part-0"
    assert GuideGenerator._sanitize_slug("---") == "part-0"
    assert GuideGenerator._sanitize_slug("", index=3) == "part-3"


def test_sanitize_slug_collapses_dashes():
    assert GuideGenerator._sanitize_slug("a---b") == "a-b"
    assert GuideGenerator._sanitize_slug("-leading-trailing-") == "leading-trailing"


def test_safe_output_path_rejects_traversal():
    with tempfile.TemporaryDirectory() as wd:
        gen = GuideGenerator(
            config=_minimal_config(),
            components={},
            module_tree={},
            working_dir=wd,
        )
        # Normal filename should work
        p = gen._safe_output_path("guide-getting-started.md")
        assert wd in p

        # Path traversal should raise
        with pytest.raises(Exception):
            gen._safe_output_path("../../../etc/passwd")


def test_prompt_version_affects_hash():
    with tempfile.TemporaryDirectory() as wd:
        inp = os.path.join(wd, "input.md")
        Path(inp).write_text("content", encoding="utf-8")

        h1 = GuideGenerator._compute_combined_hash([inp], extra="v1")
        h2 = GuideGenerator._compute_combined_hash([inp], extra="v2")
        assert h1 != h2


def test_parse_json_response_fallback():
    """Malformed JSON returns empty dict, not crash."""
    result = GuideGenerator._parse_json_response("not json at all", "OUTLINE")
    assert result == {}

    # Valid JSON in tags
    result = GuideGenerator._parse_json_response('<OUTLINE>{"sections": []}</OUTLINE>', "OUTLINE")
    assert result == {"sections": []}


def test_static_site_guide_navigation():
    """Guide pages appear in generated static HTML navigation."""
    from codewiki.cli.static_generator import StaticHTMLGenerator

    with tempfile.TemporaryDirectory() as tmp:
        docs_dir = Path(tmp)

        # Minimal module_tree.json so nav is built
        (docs_dir / "module_tree.json").write_text('{"main": {}}', encoding="utf-8")

        # overview.md is required for index.html
        (docs_dir / "overview.md").write_text("# Overview\nHello", encoding="utf-8")

        # Guide .md stubs
        for slug in (
            "guide-getting-started",
            "guide-beginners-guide",
            "guide-build-and-organization",
            "guide-core-algorithms",
        ):
            (docs_dir / f"{slug}.md").write_text(f"# {slug}\nPlaceholder", encoding="utf-8")
        # Sub-page
        (docs_dir / "guide-beginners-guide-setup.md").write_text(
            "# Setup\nPlaceholder", encoding="utf-8"
        )

        # Run static generation (writes .html files)
        gen = StaticHTMLGenerator()
        gen.generate(docs_dir)

        # Read the generated index.html and verify guide nav
        index_html = (docs_dir / "index.html").read_text(encoding="utf-8")

        assert 'href="guide-getting-started.html"' in index_html
        assert 'href="guide-beginners-guide.html"' in index_html
        assert 'href="guide-build-and-organization.html"' in index_html
        assert 'href="guide-core-algorithms.html"' in index_html
        assert 'href="guide-beginners-guide-setup.html"' in index_html

        # Fixed ordering: getting-started before core-algorithms
        gs_pos = index_html.index("guide-getting-started.html")
        ca_pos = index_html.index("guide-core-algorithms.html")
        assert gs_pos < ca_pos, "Guide navigation order must follow definition order"


def test_json_validation_failure_is_reported_as_failed():
    """JSON validation failure must appear as FAILED, not success."""
    import asyncio

    async def _run():
        with tempfile.TemporaryDirectory() as wd:
            gen = GuideGenerator(
                config=_minimal_config(),
                components={},
                module_tree={},
                working_dir=wd,
            )
            gen.docs_bundle = gen.collector.collect("/tmp", None, {})

            # Mock LLM to return invalid JSON for beginner outline
            async def bad_llm(prompt):
                return "this is not json"

            gen._call_llm_with_fallback = bad_llm

            with patch.object(gen, "_regenerate_overview", new_callable=AsyncMock):
                await gen.run()

            # Beginner guide should be FAILED, not success
            assert "FAILED" in gen._results.get("generate_beginner_guide", "")

    asyncio.run(_run())


def test_run_continues_on_guide_failure():
    """One guide failure should not prevent others from running."""
    import asyncio

    async def _run():
        with tempfile.TemporaryDirectory() as wd:
            gen = GuideGenerator(
                config=_minimal_config(),
                components={},
                module_tree={},
                working_dir=wd,
            )
            gen.docs_bundle = gen.collector.collect("/tmp", None, {})

            call_count = {"value": 0}

            async def failing_guide():
                raise RuntimeError("LLM exploded")

            async def counting_guide():
                call_count["value"] += 1

            gen.generate_getting_started = failing_guide
            gen.generate_beginner_guide = counting_guide
            gen.generate_build_analysis = counting_guide
            gen.generate_algorithm_deepdive = counting_guide

            with patch.object(gen, "_regenerate_overview", new_callable=AsyncMock):
                await gen.run()

            # 3 guides should have run despite the first one failing
            assert call_count["value"] == 3

    asyncio.run(_run())


def test_safe_generate_reraises_cancellation():
    import asyncio

    from codewiki.src.be.errors import CancellationError

    async def _run():
        with tempfile.TemporaryDirectory() as wd:
            gen = GuideGenerator(
                config=_minimal_config(),
                components={},
                module_tree={},
                working_dir=wd,
            )

            async def cancelled():
                raise CancellationError("stop now")

            with pytest.raises(CancellationError):
                await gen._safe_generate(cancelled)

    asyncio.run(_run())
