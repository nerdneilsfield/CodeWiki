from pathlib import Path
from unittest.mock import patch

import pytest


class TestWriteFailurePropagation:
    def test_write_file_raises_on_failure(self, tmp_path):
        from codewiki.src.be.agent_tools.str_replace_editor import EditTool

        tool = EditTool(REGISTRY={}, absolute_docs_path=str(tmp_path), allowed_base_path=tmp_path)
        target = tmp_path / "test.md"

        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            with pytest.raises(PermissionError, match="Could not write"):
                tool.write_file(target, "content")

    def test_write_file_succeeds_normally(self, tmp_path):
        from codewiki.src.be.agent_tools.str_replace_editor import EditTool

        tool = EditTool(REGISTRY={}, absolute_docs_path=str(tmp_path), allowed_base_path=tmp_path)
        target = tmp_path / "test.md"
        tool.write_file(target, "hello world")
        assert target.read_text() == "hello world"
