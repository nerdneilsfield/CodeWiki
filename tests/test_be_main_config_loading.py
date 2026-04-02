from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch


def test_build_runtime_config_from_args_uses_toml_loader(tmp_path):
    from codewiki.src.be import main as mod

    config_path = tmp_path / "codewiki.toml"
    config_path.write_text(
        "[runtime]\noutput_dir='docs'\n[generation]\nmain_model='openai/gpt-4o-mini'\ncluster_model='openai/gpt-4o-mini'\n[[providers]]\nname='openai'\ntype='openai_compatible'\nmodel_list=['gpt-4o-mini']\napi_keys=[]\n",
        encoding="utf-8",
    )

    app_config = MagicMock()
    sentinel_runtime = object()
    app_config.to_runtime_config.return_value = sentinel_runtime

    args = Namespace(repo_path="/tmp/repo", config=str(config_path))

    with patch.object(mod, "load_app_config", return_value=app_config) as mock_load:
        result = mod._build_runtime_config_from_args(args)

    assert result is sentinel_runtime
    mock_load.assert_called_once_with(Path(config_path))
    app_config.to_runtime_config.assert_called_once_with("/tmp/repo")
