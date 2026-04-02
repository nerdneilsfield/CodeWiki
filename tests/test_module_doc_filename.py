from codewiki.src.utils import module_doc_filename


class TestModuleDocFilename:
    def test_empty_path_uses_overview(self):
        assert module_doc_filename([]) == "overview.md"

    def test_lowercases_simple_name(self):
        assert module_doc_filename(["AuthManager"]) == "authmanager.md"

    def test_spaces_become_underscores(self):
        assert module_doc_filename(["Auth Manager"]) == "auth_manager.md"

    def test_hyphens_inside_parts_become_underscores(self):
        assert module_doc_filename(["auth-manager"]) == "auth_manager.md"

    def test_slashes_inside_part_become_underscores(self):
        assert module_doc_filename(["src/auth"]) == "src_auth.md"

    def test_ampersands_become_and(self):
        assert module_doc_filename(["Media & Data"]) == "media_and_data.md"

    def test_punctuation_is_stripped(self):
        assert module_doc_filename(["Query, Context"]) == "query_context.md"

    def test_multiple_parts_keep_dash_separator(self):
        assert module_doc_filename(["cli", "transports"]) == "cli-transports.md"

    def test_repeated_spacing_and_symbols_are_collapsed(self):
        assert module_doc_filename(["a  &  b"]) == "a_and_b.md"

    def test_nested_path_and_leaf_keep_level_separator(self):
        assert (
            module_doc_filename(["services/mcp", "connection_mgr"])
            == "services_mcp-connection_mgr.md"
        )

    def test_whitespace_only_path_falls_back_to_overview(self):
        assert module_doc_filename(["   ", "\t"]) == "overview.md"

    def test_empty_normalized_parts_are_skipped_between_real_parts(self):
        assert module_doc_filename(["CLI", "   ", "Transports"]) == "cli-transports.md"

    def test_punctuation_only_parts_do_not_introduce_extra_separators(self):
        assert module_doc_filename(["...", "Auth", "!!!"]) == "auth.md"

    def test_mixed_noise_inside_nested_path_still_produces_stable_filename(self):
        assert (
            module_doc_filename(["Services/MCP", "   ", "Connection, Manager!!!"])
            == "services_mcp-connection_manager.md"
        )
