import inspect


class TestPolymorphicModelList:
    def test_string_item_defaults_stream_false(self):
        from codewiki.src.codewiki_config import ProviderConfig
        from codewiki.src.config_loader import resolve_model_ref

        provider = ProviderConfig(
            name="openai",
            type="openai_compatible",
            base_url="http://localhost",
            api_keys=["key"],
            model_list=["gpt-4o"],
        )
        resolved = resolve_model_ref("openai/gpt-4o", [provider])
        assert resolved.stream is False

    def test_direct_provider_config_dict_item_sets_stream_true(self):
        from codewiki.src.codewiki_config import ProviderConfig
        from codewiki.src.config_loader import resolve_model_ref

        provider = ProviderConfig(
            name="openai",
            type="openai_compatible",
            base_url="http://localhost",
            api_keys=["key"],
            model_list=[{"name": "gpt-4o", "stream": True}],
        )
        resolved = resolve_model_ref("openai/gpt-4o", [provider])
        assert resolved.stream is True

    def test_dict_item_with_stream_true(self):
        from codewiki.src.codewiki_config import ProviderConfig
        from codewiki.src.config_loader import _load_provider_configs, resolve_model_ref

        provider = _load_provider_configs(
            [
                {
                    "name": "openai",
                    "type": "openai_compatible",
                    "base_url": "http://localhost",
                    "api_keys": ["key"],
                    "model_list": [{"name": "gpt-4o", "stream": True}],
                }
            ],
            resolve_secrets=False,
        )[0]
        resolved = resolve_model_ref("openai/gpt-4o", [provider])
        assert resolved.stream is True


class TestCallLlmStreamParam:
    def test_middleware_call_accepts_stream_parameter(self):
        from codewiki.src.be.llm_middleware import LLMMiddleware

        sig = inspect.signature(LLMMiddleware.call)
        assert "stream" in sig.parameters
