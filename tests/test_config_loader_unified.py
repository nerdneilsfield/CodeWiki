from pathlib import Path


def test_load_config_returns_codewiki_config(tmp_path):
    from codewiki.src.codewiki_config import CodeWikiConfig
    from codewiki.src.config_loader import load_config

    toml_content = """
[runtime]
output_dir = "docs"
output_language = "zh"

[generation]
main_model = "openai/gpt-4o"
cluster_model = "openai/gpt-4o"

[[providers]]
name = "openai"
type = "openai_compatible"
base_url = "https://api.openai.com/v1"
api_keys = ["test-key"]
model_list = ["gpt-4o"]
"""
    config_file = tmp_path / "config.toml"
    config_file.write_text(toml_content, encoding="utf-8")

    cfg = load_config(config_file, repo_path=str(tmp_path / "repo"))

    assert isinstance(cfg, CodeWikiConfig)
    assert cfg.repo_path == str(tmp_path / "repo")
    assert cfg.main_model == "openai/gpt-4o"
    assert cfg.output_language == "zh"
    assert cfg.context == "cli"
    assert len(cfg.providers) == 1
    assert cfg.providers[0].model_list == ["gpt-4o"]


def test_load_config_applies_runtime_overrides(tmp_path):
    from codewiki.src.config_loader import RuntimeOverrides, load_config

    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
[runtime]
output_dir = "docs"
max_concurrent = 2

[generation]
main_model = "openai/gpt-4o"
cluster_model = "openai/gpt-4o"
fallback_models = ["openai/gpt-4o"]

[[providers]]
name = "openai"
type = "openai_compatible"
api_keys = ["test-key"]
model_list = ["gpt-4o"]
""",
        encoding="utf-8",
    )

    cfg = load_config(
        config_file,
        repo_path=str(tmp_path / "repo"),
        overrides=RuntimeOverrides(output_dir=str(tmp_path / "custom-docs"), max_concurrent=5),
        context="web",
    )

    assert cfg.context == "web"
    assert cfg.docs_dir == str(tmp_path / "custom-docs")
    assert cfg.max_concurrent == 5
    assert cfg.output_dir.endswith("temp")
