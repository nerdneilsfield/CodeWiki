import re


_COMMIT_RE = re.compile(r"^[a-f0-9]{4,40}$")


class TestCommitIdValidation:
    def test_valid_short_hash(self):
        assert _COMMIT_RE.match("abcd")

    def test_valid_full_hash(self):
        assert _COMMIT_RE.match("a" * 40)

    def test_empty_string_allowed(self):
        # Empty strings are handled before regex: if not commit_id, skip check.
        assert not _COMMIT_RE.match("")

    def test_rejects_uppercase(self):
        assert not _COMMIT_RE.match("ABCD1234")

    def test_rejects_git_flag_injection(self):
        assert not _COMMIT_RE.match("--upload-pack=malicious")

    def test_rejects_too_short(self):
        assert not _COMMIT_RE.match("abc")

    def test_rejects_too_long(self):
        assert not _COMMIT_RE.match("a" * 41)

    def test_rejects_special_characters(self):
        assert not _COMMIT_RE.match("abcd;rm -rf /")

    def test_rejects_branch_name(self):
        assert not _COMMIT_RE.match("main")

    def test_rejects_path_traversal(self):
        assert not _COMMIT_RE.match("../../etc/passwd")


class TestCommitIdRouteIntegration:
    def test_route_has_commit_id_validation(self):
        import inspect

        from codewiki.src.fe.routes import WebRoutes

        source = inspect.getsource(WebRoutes.index_post)
        assert "_COMMIT_RE" in source or "re.match" in source, (
            "commit_id validation regex not found in index_post() — "
            "the regex exists but is not wired into the route"
        )
