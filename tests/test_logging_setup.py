import contextlib
import io
import logging


class TestLoggingSetup:
    def test_configure_cli_logging_sets_codewiki_info(self):
        from codewiki.src.logging_setup import configure_cli_logging

        configure_cli_logging(verbose=False)
        logger = logging.getLogger("codewiki")
        assert logger.level <= logging.INFO

    def test_configure_cli_logging_suppresses_third_party(self):
        from codewiki.src.logging_setup import configure_cli_logging

        configure_cli_logging(verbose=False)
        for name in ["httpx", "openai", "httpcore"]:
            assert logging.getLogger(name).level >= logging.WARNING

    def test_configure_cli_verbose_enables_debug(self):
        from codewiki.src.logging_setup import configure_cli_logging

        configure_cli_logging(verbose=True)
        logger = logging.getLogger("codewiki")
        assert logger.level <= logging.DEBUG

    def test_configure_web_logging_exists(self):
        from codewiki.src.logging_setup import configure_web_logging

        configure_web_logging()

    def test_configure_cli_logging_uses_current_stderr(self):
        import structlog

        from codewiki.src.logging_setup import configure_cli_logging

        configure_cli_logging(verbose=False)

        current_stderr = io.StringIO()
        with contextlib.redirect_stderr(current_stderr):
            structlog.get_logger("codewiki.test").info("hello", key="value")

        assert "hello" in current_stderr.getvalue()
