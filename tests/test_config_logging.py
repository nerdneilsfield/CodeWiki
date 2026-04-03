import logging
from unittest.mock import MagicMock


def test_generate_logs_effective_config(caplog):
    """generate command must log effective config at INFO level."""
    caplog.set_level(logging.INFO)

    from codewiki.cli.commands.generate import log_effective_config

    config = MagicMock()
    config.main_model = "gpt-4o"
    config.cluster_model = "gpt-4o"
    config.fallback_model = "glm-4p5"
    config.max_tokens = 32768
    config.max_concurrent = 3
    config.output_language = "zh"
    config.providers = [MagicMock(), MagicMock()]

    log_effective_config(config)

    log_text = caplog.text
    assert "gpt-4o" in log_text
    assert "32768" in log_text or "32_768" in log_text
