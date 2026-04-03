"""
Tests for web app / background worker config-path threading (Task 5).

Behaviour under test:
- BackgroundWorker accepts config_path and preserves it for later config loading
- WebAppConfig.CONFIG_PATH reads CODEWIKI_CONFIG env var at import time
- web_app module-level BackgroundWorker is initialised with WebAppConfig.CONFIG_PATH
- web_app main() propagates --config arg to both the env var and the worker
"""

import importlib.util
import os
import sys
import types
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


def _ensure_namespace_packages():
    root = Path(__file__).resolve().parents[1]
    package_paths = {
        "codewiki": root / "codewiki",
        "codewiki.src": root / "codewiki" / "src",
        "codewiki.src.fe": root / "codewiki" / "src" / "fe",
        "codewiki.src.be": root / "codewiki" / "src" / "be",
    }
    for name, path in package_paths.items():
        module = sys.modules.get(name)
        if module is None or not hasattr(module, "__path__"):
            module = types.ModuleType(name)
            module.__path__ = [str(path)]
            sys.modules[name] = module
    if "codewiki.src.utils" not in sys.modules:
        util_spec = importlib.util.spec_from_file_location(
            "codewiki.src.utils", root / "codewiki" / "src" / "utils.py"
        )
        assert util_spec is not None
        assert util_spec.loader is not None
        util_mod = importlib.util.module_from_spec(util_spec)
        sys.modules["codewiki.src.utils"] = util_mod
        util_spec.loader.exec_module(util_mod)


def _load_module(module_name: str, relative_path: str):
    _ensure_namespace_packages()
    module_path = Path(relative_path)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# ── WebAppConfig ──────────────────────────────────────────────────────────────


def test_webapp_config_reads_config_path_from_env(monkeypatch, tmp_path):
    """WebAppConfig.CONFIG_PATH must reflect CODEWIKI_CONFIG at import time."""
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(MINIMAL_TOML)

    monkeypatch.setenv("CODEWIKI_CONFIG", str(toml_path))

    cfg_mod = _load_module("codewiki.src.fe.config", "codewiki/src/fe/config.py")

    assert cfg_mod.WebAppConfig.CONFIG_PATH == str(toml_path)


def test_webapp_config_config_path_is_none_when_env_unset(monkeypatch):
    monkeypatch.delenv("CODEWIKI_CONFIG", raising=False)

    cfg_mod = _load_module("codewiki.src.fe.config", "codewiki/src/fe/config.py")

    assert cfg_mod.WebAppConfig.CONFIG_PATH is None


# ── BackgroundWorker config_path threading ────────────────────────────────────


def test_background_worker_stores_config_path(tmp_path):
    background_worker_mod = _load_module(
        "codewiki.src.fe.background_worker", "codewiki/src/fe/background_worker.py"
    )

    toml_path = tmp_path / "codewiki.toml"
    toml_path.write_text(MINIMAL_TOML)

    worker = background_worker_mod.BackgroundWorker(
        cache_manager=MagicMock(), config_path=str(toml_path)
    )
    assert worker.config_path == str(toml_path)


def test_background_worker_config_path_defaults_to_none():
    background_worker_mod = _load_module(
        "codewiki.src.fe.background_worker", "codewiki/src/fe/background_worker.py"
    )

    worker = background_worker_mod.BackgroundWorker(cache_manager=MagicMock())
    assert worker.config_path is None


# ── web_app module wiring ─────────────────────────────────────────────────────


def test_web_app_background_worker_receives_config_path_from_env(monkeypatch, tmp_path):
    """Module-level BackgroundWorker should be initialised with CONFIG_PATH."""
    toml_path = tmp_path / "codewiki.toml"
    toml_path.write_text(MINIMAL_TOML)

    monkeypatch.setenv("CODEWIKI_CONFIG", str(toml_path))

    _load_module("codewiki.src.fe.config", "codewiki/src/fe/config.py")
    web_mod = _load_module("codewiki.src.fe.web_app", "codewiki/src/fe/web_app.py")

    assert web_mod.background_worker.config_path == str(toml_path)


def test_web_app_main_propagates_config_arg_to_worker_and_env(monkeypatch, tmp_path):
    """web_app main() --config arg must update both env var and worker.config_path."""
    toml_path = tmp_path / "codewiki.toml"
    toml_path.write_text(MINIMAL_TOML)

    monkeypatch.delenv("CODEWIKI_CONFIG", raising=False)

    web_mod = _load_module("codewiki.src.fe.web_app", "codewiki/src/fe/web_app.py")

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
