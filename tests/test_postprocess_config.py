from __future__ import annotations

import tomli_w

from codewiki.src.codewiki_config import CodeWikiConfig
from codewiki.src.config_loader import load_config


def test_postprocess_config_defaults():
    from codewiki.src.codewiki_config import PostprocessConfig

    pp = PostprocessConfig()
    assert pp.strict is False
    assert pp.fix_links is True
    assert pp.repair_model == ""
    assert pp.repair_fallback_1 == ""
    assert pp.repair_fallback_2 == ""
    assert pp.repair_batch_size == 8
    assert pp.repair_max_retries == 2


def test_codewiki_config_has_postprocess():
    from codewiki.src.codewiki_config import PostprocessConfig

    config = CodeWikiConfig(repo_path="/tmp", docs_dir="/tmp/docs")
    assert isinstance(config.postprocess, PostprocessConfig)
    assert config.postprocess.strict is False


def test_codewiki_config_no_top_level_postprocess_fields():
    config = CodeWikiConfig(repo_path="/tmp", docs_dir="/tmp/docs")
    assert "postprocess_strict" not in CodeWikiConfig.model_fields
    assert "postprocess_fix_links" not in CodeWikiConfig.model_fields


def test_postprocess_section_parsed_from_model():
    from codewiki.src.codewiki_config import PostprocessConfig

    config = CodeWikiConfig(
        repo_path="/tmp",
        docs_dir="/tmp/docs",
        postprocess=PostprocessConfig(
            strict=True,
            repair_model="openai/gpt-4o-mini",
            repair_fallback_1="claude/claude-sonnet-4-5-20250929",
            repair_fallback_2="openai/gpt-4.1",
            repair_batch_size=16,
            repair_max_retries=3,
        ),
    )
    assert config.postprocess.strict is True
    assert config.postprocess.repair_model == "openai/gpt-4o-mini"
    assert config.postprocess.repair_batch_size == 16


def test_load_config_parses_postprocess_section(tmp_path):
    toml_data = {
        "runtime": {"output_dir": str(tmp_path / "docs")},
        "generation": {
            "main_model": "openai/gpt-4o-mini",
            "cluster_model": "openai/gpt-4o-mini",
        },
        "postprocess": {
            "strict": True,
            "repair_model": "openai/gpt-4o-mini",
            "repair_batch_size": 4,
        },
        "providers": [
            {
                "name": "openai",
                "type": "openai_compatible",
                "base_url": "http://localhost",
                "api_keys": ["test-key"],
                "model_list": ["gpt-4o-mini"],
            }
        ],
    }
    config_path = tmp_path / "config.toml"
    config_path.write_text(tomli_w.dumps(toml_data), encoding="utf-8")

    config = load_config(str(config_path), repo_path=str(tmp_path))
    assert config.postprocess.strict is True
    assert config.postprocess.repair_model == "openai/gpt-4o-mini"
    assert config.postprocess.repair_batch_size == 4
