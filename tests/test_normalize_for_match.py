from codewiki.src.utils import _normalize_for_match


def test_normalize_for_match_treats_ampersand_like_and_word():
    assert _normalize_for_match("Media-&-Data.md") == _normalize_for_match("media_and_data.md")


def test_normalize_for_match_strips_punctuation_and_ignores_case():
    assert _normalize_for_match("Query, Context.MD") == _normalize_for_match("query_context.md")

