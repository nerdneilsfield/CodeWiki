"""
Error handling utilities and exit codes for CLI.

Exit Codes:
  0: Success
  1: General error
  2: Configuration error (missing/invalid credentials)
  3: Repository error (not a git repo, no code files)
  4: LLM API error (including rate limits)
  5: File system error (permissions, disk space)
"""

import logging
import sys
from typing import Optional

logger = logging.getLogger(__name__)


# Exit codes
EXIT_SUCCESS = 0
EXIT_GENERAL_ERROR = 1
EXIT_CONFIG_ERROR = 2
EXIT_REPOSITORY_ERROR = 3
EXIT_API_ERROR = 4
EXIT_FILESYSTEM_ERROR = 5


class CodeWikiError(Exception):
    """Base exception for CodeWiki CLI errors."""

    def __init__(self, message: str, exit_code: int = EXIT_GENERAL_ERROR):
        self.message = message
        self.exit_code = exit_code
        super().__init__(self.message)


class ConfigurationError(CodeWikiError):
    """Configuration-related errors."""

    def __init__(self, message: str):
        super().__init__(message, EXIT_CONFIG_ERROR)


class RepositoryError(CodeWikiError):
    """Repository-related errors."""

    def __init__(self, message: str):
        super().__init__(message, EXIT_REPOSITORY_ERROR)


class APIError(CodeWikiError):
    """LLM API-related errors."""

    def __init__(self, message: str):
        super().__init__(message, EXIT_API_ERROR)


class FileSystemError(CodeWikiError):
    """File system-related errors."""

    def __init__(self, message: str):
        super().__init__(message, EXIT_FILESYSTEM_ERROR)


def handle_error(error: Exception, verbose: bool = False) -> int:
    """
    Handle errors and return appropriate exit code.

    Args:
        error: The exception to handle
        verbose: Whether to show detailed error information

    Returns:
        Exit code for the error
    """
    if isinstance(error, CodeWikiError):
        logger.error(error.message)
        return error.exit_code
    else:
        if verbose:
            logger.exception("Unexpected error", exc_info=error)
        else:
            logger.error("Unexpected error: %s", error)
        return EXIT_GENERAL_ERROR


def error_with_suggestion(message: str, suggestion: str, exit_code: int = EXIT_GENERAL_ERROR):
    """
    Display error message with actionable suggestion and exit.

    Args:
        message: The error message
        suggestion: Suggested action to resolve the error
        exit_code: Exit code to use
    """
    logger.error(message)
    logger.info(suggestion)
    sys.exit(exit_code)


def warning(message: str):
    """Display a warning message."""
    logger.warning(message)


def success(message: str):
    """Display a success message."""
    logger.info(message, extra={"outcome": "success"})


def info(message: str):
    """Display an info message."""
    logger.info(message)
