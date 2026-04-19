from codewiki.src.codewiki_config import CodeWikiConfig, IncrementalConfig, RefinementConfig


def test_refinement_config_defaults():
    cfg = CodeWikiConfig(
        repo_path="/tmp/repo",
        docs_dir="/tmp/docs",
        llm_base_url="http://localhost",
        llm_api_key="x",
        main_model="openai/gpt-4o-mini",
        cluster_model="openai/gpt-4o-mini",
    )

    assert cfg.refinement.max_depth == 3
    assert cfg.refinement.min_components_for_split == 6
    assert cfg.refinement.min_distinct_files_for_split == 4
    assert cfg.refinement.max_cluster_components == 1000
    assert cfg.refinement.identity_reuse_threshold == 0.70


def test_refinement_config_override():
    cfg = CodeWikiConfig(
        repo_path="/tmp/repo",
        docs_dir="/tmp/docs",
        llm_base_url="http://localhost",
        llm_api_key="x",
        main_model="openai/gpt-4o-mini",
        cluster_model="openai/gpt-4o-mini",
        refinement=RefinementConfig(
            max_depth=5,
            min_components_for_split=10,
            min_distinct_files_for_split=6,
            max_cluster_components=500,
            identity_reuse_threshold=0.80,
        ),
    )

    assert cfg.refinement.max_depth == 5
    assert cfg.refinement.min_components_for_split == 10
    assert cfg.refinement.min_distinct_files_for_split == 6
    assert cfg.refinement.max_cluster_components == 500
    assert cfg.refinement.identity_reuse_threshold == 0.80


def test_incremental_config_defaults():
    cfg = CodeWikiConfig(
        repo_path="/tmp/repo",
        docs_dir="/tmp/docs",
        llm_base_url="http://localhost",
        llm_api_key="x",
        main_model="openai/gpt-4o-mini",
        cluster_model="openai/gpt-4o-mini",
    )

    assert cfg.incremental.leaf_rerun_threshold == 0.30
    assert cfg.incremental.parent_rerun_threshold == 0.30


def test_incremental_config_override():
    cfg = CodeWikiConfig(
        repo_path="/tmp/repo",
        docs_dir="/tmp/docs",
        llm_base_url="http://localhost",
        llm_api_key="x",
        main_model="openai/gpt-4o-mini",
        cluster_model="openai/gpt-4o-mini",
        incremental=IncrementalConfig(
            leaf_rerun_threshold=0.40,
            parent_rerun_threshold=0.55,
        ),
    )

    assert cfg.incremental.leaf_rerun_threshold == 0.40
    assert cfg.incremental.parent_rerun_threshold == 0.55
