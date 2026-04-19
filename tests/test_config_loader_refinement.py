from pathlib import Path
import textwrap

from codewiki.src.config_loader import load_config


def _write_config(tmp_path: Path, body: str) -> Path:
    config_path = tmp_path / "codewiki.toml"
    config_path.write_text(textwrap.dedent(body), encoding="utf-8")
    return config_path


def test_refinement_section_loads_from_toml(tmp_path):
    config_path = _write_config(
        tmp_path,
        """
        [runtime]
        output_dir = "docs"

        [generation]
        main_model = "openai/gpt-4o-mini"
        cluster_model = "openai/gpt-4o-mini"

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        model_list = ["gpt-4o-mini"]
        api_keys = []

        [refinement]
        max_depth = 4
        min_components_for_split = 8
        min_distinct_files_for_split = 5
        max_cluster_components = 800
        identity_reuse_threshold = 0.85
        """,
    )

    cfg = load_config(str(config_path), repo_path="/tmp/repo", resolve_secrets=False)

    assert cfg.refinement.max_depth == 4
    assert cfg.refinement.min_components_for_split == 8
    assert cfg.refinement.min_distinct_files_for_split == 5
    assert cfg.refinement.max_cluster_components == 800
    assert cfg.refinement.identity_reuse_threshold == 0.85


def test_refinement_section_absent_uses_defaults(tmp_path):
    config_path = _write_config(
        tmp_path,
        """
        [runtime]
        output_dir = "docs"

        [generation]
        main_model = "openai/gpt-4o-mini"
        cluster_model = "openai/gpt-4o-mini"

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        model_list = ["gpt-4o-mini"]
        api_keys = []
        """,
    )

    cfg = load_config(str(config_path), repo_path="/tmp/repo", resolve_secrets=False)

    assert cfg.refinement.max_depth == 3
    assert cfg.refinement.min_components_for_split == 6
    assert cfg.refinement.min_distinct_files_for_split == 4
    assert cfg.refinement.max_cluster_components == 1000
    assert cfg.refinement.identity_reuse_threshold == 0.70


def test_incremental_section_loads_from_toml(tmp_path):
    config_path = _write_config(
        tmp_path,
        """
        [runtime]
        output_dir = "docs"

        [generation]
        main_model = "openai/gpt-4o-mini"
        cluster_model = "openai/gpt-4o-mini"

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        model_list = ["gpt-4o-mini"]
        api_keys = []

        [incremental]
        leaf_rerun_threshold = 0.45
        parent_rerun_threshold = 0.55
        """,
    )

    cfg = load_config(str(config_path), repo_path="/tmp/repo", resolve_secrets=False)
    assert cfg.incremental.leaf_rerun_threshold == 0.45
    assert cfg.incremental.parent_rerun_threshold == 0.55


def test_incremental_section_absent_uses_defaults(tmp_path):
    config_path = _write_config(
        tmp_path,
        """
        [runtime]
        output_dir = "docs"

        [generation]
        main_model = "openai/gpt-4o-mini"
        cluster_model = "openai/gpt-4o-mini"

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        model_list = ["gpt-4o-mini"]
        api_keys = []
        """,
    )

    cfg = load_config(str(config_path), repo_path="/tmp/repo", resolve_secrets=False)
    assert cfg.incremental.leaf_rerun_threshold == 0.30
    assert cfg.incremental.parent_rerun_threshold == 0.30
