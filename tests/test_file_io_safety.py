import json
import unittest.mock as mock

import pytest


class TestSaveTextAtomicAndEncoding:
    def test_save_text_writes_utf8(self, tmp_path):
        from codewiki.src.utils import file_manager

        path = str(tmp_path / "test.md")
        file_manager.save_text("你好世界 🌍", path)
        with open(path, "r", encoding="utf-8") as f:
            assert f.read() == "你好世界 🌍"

    def test_save_text_atomic_no_partial_on_error(self, tmp_path):
        from codewiki.src.utils import file_manager

        path = str(tmp_path / "test.md")
        file_manager.save_text("original content", path)

        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                file_manager.save_text("new content that should not appear", path)

        assert file_manager.load_text(path) == "original content"

    def test_load_text_reads_utf8(self, tmp_path):
        from codewiki.src.utils import file_manager

        path = str(tmp_path / "test.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("中文内容")
        assert file_manager.load_text(path) == "中文内容"


class TestSaveJsonEncoding:
    def test_save_json_writes_utf8(self, tmp_path):
        from codewiki.src.utils import file_manager

        path = str(tmp_path / "test.json")
        file_manager.save_json({"name": "模块名称"}, path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["name"] == "模块名称"

    def test_load_json_reads_utf8(self, tmp_path):
        from codewiki.src.utils import file_manager

        path = str(tmp_path / "test.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"key": "日本語"}, f, ensure_ascii=False)
        data = file_manager.load_json(path)
        assert data["key"] == "日本語"
