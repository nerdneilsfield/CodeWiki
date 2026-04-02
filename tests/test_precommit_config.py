from pathlib import Path

import yaml


def test_precommit_config_contains_ruff_and_ty_hooks():
    config_path = Path(__file__).resolve().parents[1] / ".pre-commit-config.yaml"
    assert config_path.exists()
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    repos = data["repos"]
    hooks = [hook for repo in repos for hook in repo.get("hooks", [])]

    hook_ids = {hook["id"] for hook in hooks}
    assert "ruff-check" in hook_ids
    assert "ruff-format" in hook_ids
    assert "ty-check" in hook_ids

    ty_hook = next(hook for hook in hooks if hook["id"] == "ty-check")
    assert ty_hook["entry"] == "uv run ty check"
    assert ty_hook["pass_filenames"] is False
