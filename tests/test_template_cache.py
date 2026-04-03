def test_compile_template_is_cached():
    from codewiki.src.fe.template_utils import _compile_template

    t1 = _compile_template("<p>{{ x }}</p>")
    t2 = _compile_template("<p>{{ x }}</p>")
    assert t1 is t2


def test_render_template_uses_cache():
    from codewiki.src.fe.template_utils import render_template

    html1 = render_template("<p>{{ name }}</p>", {"name": "A"})
    html2 = render_template("<p>{{ name }}</p>", {"name": "B"})
    assert "A" in html1
    assert "B" in html2
