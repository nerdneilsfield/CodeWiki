from codewiki.src.codewiki_config import CodeWikiConfig, ProviderConfig


class TestCodeWikiConfig:
    def test_minimal_creation(self):
        cfg = CodeWikiConfig(repo_path="/tmp/repo", docs_dir="/tmp/docs")
        assert cfg.repo_path == "/tmp/repo"
        assert cfg.context == "cli"

    def test_context_field(self):
        cfg = CodeWikiConfig(repo_path="/tmp", docs_dir="/tmp", context="web")
        assert cfg.context == "web"

    def test_provider_list_with_runtime_fields(self):
        cfg = CodeWikiConfig(
            repo_path="/tmp",
            docs_dir="/tmp",
            providers=[
                ProviderConfig(
                    name="openai",
                    type="openai_compatible",
                    base_url="https://api.openai.com/v1",
                    api_keys=["test-key"],
                    model_list=["gpt-4o"],
                    extra_headers={"x-test": "1"},
                    api_version="2024-02-01",
                )
            ],
        )
        assert len(cfg.providers) == 1
        assert cfg.providers[0].model_list == ["gpt-4o"]
        assert cfg.providers[0].extra_headers["x-test"] == "1"

    def test_cli_override_via_model_copy(self):
        base = CodeWikiConfig(repo_path="/tmp", docs_dir="/tmp", main_model="gpt-4o")
        overridden = base.model_copy(update={"main_model": "claude-sonnet"})
        assert overridden.main_model == "claude-sonnet"
        assert base.main_model == "gpt-4o"

    def test_agent_instruction_helpers(self):
        cfg = CodeWikiConfig(
            repo_path="/tmp",
            docs_dir="/tmp",
            agent_instructions={
                "include_patterns": ["src/**"],
                "exclude_patterns": ["tests/**"],
                "focus_modules": ["src/core", "src/api"],
                "doc_type": "architecture",
                "custom_instructions": "Be concise.",
            },
        )
        assert cfg.include_patterns == ["src/**"]
        assert cfg.exclude_patterns == ["tests/**"]
        assert cfg.focus_modules == ["src/core", "src/api"]
        assert cfg.doc_type == "architecture"
        assert cfg.custom_instructions == "Be concise."

    def test_get_prompt_addition_preserves_runtime_behavior(self):
        cfg = CodeWikiConfig(
            repo_path="/tmp",
            docs_dir="/tmp",
            agent_instructions={
                "focus_modules": ["src/core"],
                "doc_type": "api",
                "custom_instructions": "Use short examples.",
            },
        )
        addition = cfg.get_prompt_addition()
        assert "API documentation" in addition
        assert "src/core" in addition
        assert "Use short examples." in addition
