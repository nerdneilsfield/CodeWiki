from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from codewiki.src.codewiki_config import CodeWikiConfig


def test_build_runtime_config_from_args_uses_toml_loader(tmp_path):
    from codewiki.src.be import main as mod

    config_path = tmp_path / "codewiki.toml"
    config_path.write_text(
        "[runtime]\noutput_dir='docs'\n[generation]\nmain_model='openai/gpt-4o-mini'\ncluster_model='openai/gpt-4o-mini'\n[[providers]]\nname='openai'\ntype='openai_compatible'\nmodel_list=['gpt-4o-mini']\napi_keys=[]\n",
        encoding="utf-8",
    )

    sentinel_runtime = CodeWikiConfig(
        repo_path="/tmp/repo",
        docs_dir="docs",
        main_model="openai/gpt-4o-mini",
        cluster_model="openai/gpt-4o-mini",
    )

    args = Namespace(repo_path="/tmp/repo", config=str(config_path))

    with patch.object(mod, "load_config", return_value=sentinel_runtime) as mock_load:
        result = mod._build_runtime_config_from_args(args)

    assert result is sentinel_runtime
    mock_load.assert_called_once_with(Path(config_path), repo_path="/tmp/repo", context="cli")
