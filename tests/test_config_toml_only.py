import pytest


class TestNoEnvVarFallback:
    def test_config_module_has_no_getenv(self):
        """config.py must not call os.getenv() directly."""
        import inspect

        import codewiki.src.config as config_mod

        source = inspect.getsource(config_mod)
        assert "os.getenv" not in source, "config.py still contains os.getenv() calls"

    def test_config_module_has_no_load_dotenv(self):
        """config.py must not import or call load_dotenv."""
        import inspect

        import codewiki.src.config as config_mod

        source = inspect.getsource(config_mod)
        assert "load_dotenv" not in source, "config.py still references load_dotenv"

    def test_no_hardcoded_sk_1234(self):
        """The placeholder API key must be gone."""
        import inspect

        import codewiki.src.config as config_mod

        source = inspect.getsource(config_mod)
        assert "sk-1234" not in source


class TestValidateLlmCredentials:
    def test_raises_when_no_provider_has_key(self):
        from codewiki.src.be.llm_services import validate_llm_credentials
        from unittest.mock import MagicMock

        config = MagicMock()
        config.main_model = "nonexistent-model"
        config.providers = []
        config.llm_api_key = ""
        config.llm_base_url = ""

        with pytest.raises(RuntimeError, match="No API key"):
            validate_llm_credentials(config)
