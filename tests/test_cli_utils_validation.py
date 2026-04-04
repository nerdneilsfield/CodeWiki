from pathlib import Path

import pytest


def test_validate_url_requires_scheme():
    from codewiki.cli.utils.errors import ConfigurationError
    from codewiki.cli.utils.validation import validate_url

    with pytest.raises(ConfigurationError, match="missing scheme"):
        validate_url("example.com")


def test_validate_url_allows_http_for_localhost_when_https_required():
    from codewiki.cli.utils.validation import validate_url

    assert (
        validate_url("http://localhost:8000/docs", require_https=True)
        == "http://localhost:8000/docs"
    )


def test_validate_url_rejects_http_for_non_localhost_when_https_required():
    from codewiki.cli.utils.errors import ConfigurationError
    from codewiki.cli.utils.validation import validate_url

    with pytest.raises(ConfigurationError, match="must use HTTPS"):
        validate_url("http://example.com", require_https=True)


def test_validate_api_key_strips_whitespace_and_rejects_short_values():
    from codewiki.cli.utils.errors import ConfigurationError
    from codewiki.cli.utils.validation import validate_api_key

    assert validate_api_key("  abcdefghij  ") == "abcdefghij"
    with pytest.raises(ConfigurationError, match="too short"):
        validate_api_key(" short ")


def test_validate_model_name_rejects_empty_and_strips_whitespace():
    from codewiki.cli.utils.errors import ConfigurationError
    from codewiki.cli.utils.validation import validate_model_name

    assert validate_model_name("  gpt-5  ") == "gpt-5"
    with pytest.raises(ConfigurationError, match="cannot be empty"):
        validate_model_name("   ")


def test_validate_output_directory_rejects_file_path(tmp_path):
    from codewiki.cli.utils.errors import ConfigurationError
    from codewiki.cli.utils.validation import validate_output_directory

    file_path = tmp_path / "output.txt"
    file_path.write_text("x", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="not a directory"):
        validate_output_directory(str(file_path))


def test_validate_repository_path_rejects_missing_and_file(tmp_path):
    from codewiki.cli.utils.errors import RepositoryError
    from codewiki.cli.utils.validation import validate_repository_path

    with pytest.raises(RepositoryError, match="does not exist"):
        validate_repository_path(tmp_path / "missing")

    file_path = tmp_path / "repo.txt"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(RepositoryError, match="not a directory"):
        validate_repository_path(file_path)


def test_detect_supported_languages_excludes_vendor_dirs_and_sorts(tmp_path):
    from codewiki.cli.utils.validation import detect_supported_languages

    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "c.ts").write_text("", encoding="utf-8")
    excluded = tmp_path / "node_modules"
    excluded.mkdir()
    (excluded / "ignored.js").write_text("", encoding="utf-8")

    detected = detect_supported_languages(tmp_path)

    assert detected[0] == ("Python", 2)
    assert detected[1] == ("TypeScript", 1)
    assert all(lang != "JavaScript" for lang, _ in detected)


def test_is_top_tier_model_is_case_insensitive():
    from codewiki.cli.utils.validation import is_top_tier_model

    assert is_top_tier_model("GPT-5-mini") is True
    assert is_top_tier_model("custom-small-model") is False


def test_mask_api_key_handles_empty_short_and_long_values():
    from codewiki.cli.utils.validation import mask_api_key

    assert mask_api_key("") == "Not set"
    assert mask_api_key("abcd") == "ab...cd"
    assert mask_api_key("abcdefghijkl") == "abcd...ijkl"
