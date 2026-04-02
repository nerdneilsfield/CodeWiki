class TestSanitizeHtml:
    def test_strips_script_tags(self):
        from codewiki.src.fe.html_sanitizer import sanitize_html

        dirty = '<p>Hello</p><script>alert("xss")</script>'
        clean = sanitize_html(dirty)
        assert "<script>" not in clean
        assert "alert" not in clean
        assert "<p>Hello</p>" in clean

    def test_strips_onerror_attribute(self):
        from codewiki.src.fe.html_sanitizer import sanitize_html

        dirty = '<img src="x" onerror="alert(1)">'
        clean = sanitize_html(dirty)
        assert "onerror" not in clean
        assert "<img" in clean

    def test_strips_javascript_url(self):
        from codewiki.src.fe.html_sanitizer import sanitize_html

        dirty = '<a href="javascript:alert(1)">click</a>'
        clean = sanitize_html(dirty)
        assert "javascript:" not in clean

    def test_strips_data_url(self):
        from codewiki.src.fe.html_sanitizer import sanitize_html

        dirty = '<a href="data:text/html,<script>alert(1)</script>">click</a>'
        clean = sanitize_html(dirty)
        assert "data:" not in clean

    def test_allows_safe_markdown_html(self):
        from codewiki.src.fe.html_sanitizer import sanitize_html

        safe = (
            '<h1>Title</h1><p>Text with <strong>bold</strong> and '
            '<a href="https://example.com">link</a></p>'
            '<pre><code class="language-python">print("hi")</code></pre>'
        )
        result = sanitize_html(safe)
        assert "<h1>Title</h1>" in result
        assert "<strong>bold</strong>" in result
        assert 'href="https://example.com"' in result
        assert 'class="language-python"' in result

    def test_allows_mermaid_div(self):
        from codewiki.src.fe.html_sanitizer import sanitize_html

        html = '<div class="mermaid">graph TD; A-->B</div>'
        result = sanitize_html(html)
        assert 'class="mermaid"' in result
        assert "graph TD" in result

    def test_strips_iframe(self):
        from codewiki.src.fe.html_sanitizer import sanitize_html

        dirty = '<iframe src="https://evil.com"></iframe>'
        clean = sanitize_html(dirty)
        assert "<iframe" not in clean

    def test_strips_style_attribute(self):
        from codewiki.src.fe.html_sanitizer import sanitize_html

        dirty = '<div style="background:url(javascript:alert(1))">hi</div>'
        clean = sanitize_html(dirty)
        assert "style=" not in clean

    def test_strips_svg_tags(self):
        from codewiki.src.fe.html_sanitizer import sanitize_html

        dirty = '<svg onload="alert(1)"><circle r="50"/></svg>'
        clean = sanitize_html(dirty)
        assert "<svg" not in clean

    def test_preserves_table_structure(self):
        from codewiki.src.fe.html_sanitizer import sanitize_html

        html = (
            "<table><thead><tr><th>Col</th></tr></thead>"
            "<tbody><tr><td>Val</td></tr></tbody></table>"
        )
        result = sanitize_html(html)
        assert "<table>" in result
        assert "<th>Col</th>" in result
