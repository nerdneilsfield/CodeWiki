"""HTML sanitization for rendered documentation content."""

import nh3

_ALLOWED_TAGS = {
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "br",
    "hr",
    "ul",
    "ol",
    "li",
    "dl",
    "dt",
    "dd",
    "strong",
    "em",
    "b",
    "i",
    "u",
    "s",
    "del",
    "ins",
    "code",
    "pre",
    "blockquote",
    "kbd",
    "a",
    "img",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "th",
    "td",
    "caption",
    "div",
    "span",
    "sup",
    "sub",
    "details",
    "summary",
}

_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title", "id", "class"},
    "img": {"src", "alt", "title", "width", "height"},
    "div": {"class", "id", "data-nav", "data-nav-sub"},
    "span": {"class", "id"},
    "code": {"class"},
    "pre": {"class"},
    "td": {"align", "colspan", "rowspan"},
    "th": {"align", "colspan", "rowspan"},
    "*": {"id", "class"},
}

_SAFE_URL_SCHEMES = {"http", "https", "mailto"}


def sanitize_html(html: str) -> str:
    """Remove dangerous tags and attributes while preserving markdown output."""
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        link_rel="noopener noreferrer",
        url_schemes=_SAFE_URL_SCHEMES,
    )
