"""
Tests for web app / background worker config-path threading (Task 5).

Behaviour under test:
- BackgroundWorker accepts config_path and passes it through to load_app_config
- WebAppConfig.CONFIG_PATH reads CODEWIKI_CONFIG env var at import time
- web_app module-level BackgroundWorker is initialised with WebAppConfig.CONFIG_PATH
- web_app main() propagates --config arg to both the env var and the worker
"""

import importlib
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

MINIMAL_TOML = (
    "[runtime]\noutput_dir = 'docs'\n"
    "[generation]\nmain_model = 'openai/gpt-4o-mini'\ncluster_model = 'openai/gpt-4o-mini'\n"
    "[[providers]]\nname = 'openai'\ntype = 'openai_compatible'\n"
    "model_list = ['gpt-4o-mini']\napi_keys = []\n"
)


# ── WebAppConfig ──────────────────────────────────────────────────────────────


def test_webapp_config_reads_config_path_from_env(monkeypatch, tmp_path):
    """WebAppConfig.CONFIG_PATH must reflect CODEWIKI_CONFIG at import time."""
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(MINIMAL_TOML)

    monkeypatch.setenv("CODEWIKI_CONFIG", str(toml_path))

    # Force re-evaluation — the class attr is set at module import so we reload.
    import codewiki.src.fe.config as cfg_mod

    importlib.reload(cfg_mod)

    assert cfg_mod.WebAppConfig.CONFIG_PATH == str(toml_path)


def test_webapp_config_config_path_is_none_when_env_unset(monkeypatch):
    monkeypatch.delenv("CODEWIKI_CONFIG", raising=False)

    import codewiki.src.fe.config as cfg_mod

    importlib.reload(cfg_mod)

    assert cfg_mod.WebAppConfig.CONFIG_PATH is None


# ── BackgroundWorker config_path threading ────────────────────────────────────


def test_background_worker_stores_config_path(tmp_path):
    from codewiki.src.fe.background_worker import BackgroundWorker

    toml_path = tmp_path / "codewiki.toml"
    toml_path.write_text(MINIMAL_TOML)

    worker = BackgroundWorker(cache_manager=MagicMock(), config_path=str(toml_path))
    assert worker.config_path == str(toml_path)


def test_background_worker_config_path_defaults_to_none():
    from codewiki.src.fe.background_worker import BackgroundWorker

    worker = BackgroundWorker(cache_manager=MagicMock())
    assert worker.config_path is None


def test_background_worker_load_app_config_raises_without_config_path():
    from codewiki.src.fe.background_worker import BackgroundWorker

    worker = BackgroundWorker(cache_manager=MagicMock(), config_path=None)

    with pytest.raises(RuntimeError, match="config_path"):
        worker._load_app_config()


def test_background_worker_load_app_config_delegates_to_loader(tmp_path):
    from codewiki.src.fe.background_worker import BackgroundWorker

    toml_path = tmp_path / "codewiki.toml"
    toml_path.write_text(MINIMAL_TOML)

    sentinel = MagicMock()
    worker = BackgroundWorker(cache_manager=MagicMock(), config_path=str(toml_path))

    with patch(
        "codewiki.src.fe.background_worker.load_app_config", return_value=sentinel
    ) as mock_load:
        result = worker._load_app_config()

    assert result is sentinel
    mock_load.assert_called_once_with(Path(toml_path))


# ── web_app module wiring ─────────────────────────────────────────────────────


def test_web_app_background_worker_receives_config_path_from_env(monkeypatch, tmp_path):
    """Module-level BackgroundWorker should be initialised with CONFIG_PATH."""
    toml_path = tmp_path / "codewiki.toml"
    toml_path.write_text(MINIMAL_TOML)

    monkeypatch.setenv("CODEWIKI_CONFIG", str(toml_path))

    # Reload modules so module-level code re-runs with the new env var.
    import codewiki.src.fe.config as cfg_mod
    import codewiki.src.fe.web_app as web_mod

    importlib.reload(cfg_mod)
    importlib.reload(web_mod)

    assert web_mod.background_worker.config_path == str(toml_path)


def test_web_app_main_propagates_config_arg_to_worker_and_env(monkeypatch, tmp_path):
    """web_app main() --config arg must update both env var and worker.config_path."""
    toml_path = tmp_path / "codewiki.toml"
    toml_path.write_text(MINIMAL_TOML)

    monkeypatch.delenv("CODEWIKI_CONFIG", raising=False)

    import codewiki.src.fe.web_app as web_mod

    importlib.reload(web_mod)

    # Patch uvicorn.run so main() doesn't actually start a server.
    with (
        patch("uvicorn.run"),
        patch.object(web_mod.background_worker, "start"),
        patch.object(web_mod.WebAppConfig, "ensure_directories"),
    ):
        import sys

        monkeypatch.setattr(sys, "argv", ["web_app", "--config", str(toml_path)])
        try:
            web_mod.main()
        except SystemExit:
            pass

    assert web_mod.background_worker.config_path == str(toml_path)
    assert os.environ.get("CODEWIKI_CONFIG") == str(toml_path)
