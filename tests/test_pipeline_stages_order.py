from codewiki.src.be.stages import DEFAULT_STAGES


def test_tree_refinement_runs_after_clustering_and_before_state_init():
    names = [stage.name for stage in DEFAULT_STAGES]
    assert "ClusteringStage" in names
    assert "TreeRefinementStage" in names
    assert "StateInitStage" in names
    clustering_idx = names.index("ClusteringStage")
    refinement_idx = names.index("TreeRefinementStage")
    state_init_idx = names.index("StateInitStage")
    assert clustering_idx < refinement_idx < state_init_idx
