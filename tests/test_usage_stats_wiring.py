from unittest.mock import MagicMock, patch


def test_usage_stats_created_on_generator():
    from codewiki.src.be.documentation_generator import DocumentationGenerator
    from codewiki.src.be.llm_usage import LLMUsageStats

    config = MagicMock()
    config.repo_path = "/tmp/fake"
    config.docs_dir = "/tmp/fake/docs"
    config.output_dir = "/tmp/fake/output"
    config.dependency_graph_dir = "/tmp/fake/graphs"
    config.max_depth = 2
    config.get_prompt_addition.return_value = ""
    config.output_language = "en"
    config.long_context_model = None
    config.main_model = "test-model"
    config.cluster_model = "test-cluster"
    config.fallback_model = []
    config.max_tokens = 1024
    config.max_concurrent = 2

    with (
        patch("codewiki.src.be.documentation_generator.DependencyGraphBuilder"),
        patch("codewiki.src.be.documentation_generator.AgentOrchestrator"),
    ):
        gen = DocumentationGenerator(config)
    assert isinstance(gen.usage_stats, LLMUsageStats)


def test_usage_stats_record_accumulates():
    from codewiki.src.be.llm_usage import LLMUsageStats

    stats = LLMUsageStats()
    stats.record("model-a", 100, 50)
    stats.record("model-a", 200, 100)
    d = stats.to_dict()
    assert d["total_input_tokens"] == 300
    assert d["total_requests"] == 2
