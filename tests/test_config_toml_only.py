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
    def test_allows_legacy_config_with_api_key(self):
        from codewiki.src.be.llm_services import validate_llm_credentials
        from unittest.mock import MagicMock

        config = MagicMock()
        config.main_model = "main-model"
        config.providers = []
        config.llm_api_key = "secret"

        validate_llm_credentials(config)

    def test_raises_when_main_model_missing(self):
        from codewiki.src.be.llm_services import validate_llm_credentials
        from unittest.mock import MagicMock

        config = MagicMock()
        config.main_model = ""
        config.providers = []
        config.llm_api_key = "secret"

        with pytest.raises(RuntimeError, match="No main_model configured"):
            validate_llm_credentials(config)

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

    def test_raises_when_provider_has_no_key(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from codewiki.src.be.llm_services import validate_llm_credentials

        config = MagicMock()
        config.main_model = "provider-model"
        config.providers = [object()]
        config.llm_api_key = ""

        provider = SimpleNamespace(name="test-provider", api_keys=[])

        with (
            patch("codewiki.src.be.llm_services._has_provider_registry", return_value=True),
            patch(
                "codewiki.src.be.llm_services._get_provider_config",
                return_value=(provider, "provider-model"),
            ),
        ):
            with pytest.raises(RuntimeError, match="provider='test-provider'"):
                validate_llm_credentials(config)
