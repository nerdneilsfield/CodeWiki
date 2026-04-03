import contextlib
import io
import logging
import warnings


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

    def test_configure_cli_logging_does_not_warn_on_exc_info(self):
        import structlog

        from codewiki.src.logging_setup import configure_cli_logging

        configure_cli_logging(verbose=False)

        current_stderr = io.StringIO()
        with contextlib.redirect_stderr(current_stderr):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                try:
                    raise RuntimeError("boom")
                except RuntimeError:
                    structlog.get_logger("codewiki.test").exception("oops")

        assert "oops" in current_stderr.getvalue()
        assert not any(
            "Remove `format_exc_info` from your processor chain" in str(w.message) for w in caught
        )
