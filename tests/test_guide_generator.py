# tests/test_guide_generator.py
import os
import tempfile
from pathlib import Path

from codewiki.src.be.guide_generator import GuideGenerator


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
