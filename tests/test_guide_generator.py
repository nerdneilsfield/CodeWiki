# tests/test_guide_generator.py
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

from codewiki.src.be.guide_generator import GuideGenerator, _PROMPT_VERSIONS


def _minimal_config():
    """Return a minimal Config-like object for testing."""
    from codewiki.src.config import Config
    return Config(
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


def test_should_not_regenerate_when_hash_matches():
    with tempfile.TemporaryDirectory() as wd:
        # Create a fake input file
        inp = os.path.join(wd, "input.md")
        Path(inp).write_text("hello", encoding="utf-8")
        out = os.path.join(wd, "guide-getting-started.md")
        Path(out).write_text("# Getting Started\nContent here.", encoding="utf-8")

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
    result = GuideGenerator._parse_json_response(
        '<OUTLINE>{"sections": []}</OUTLINE>', "OUTLINE"
    )
    assert result == {"sections": []}


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

            with patch.object(gen, '_regenerate_overview', new_callable=AsyncMock):
                await gen.run()

            # 3 guides should have run despite the first one failing
            assert call_count["value"] == 3

    asyncio.run(_run())
