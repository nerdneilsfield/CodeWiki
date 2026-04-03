import logging
from pathlib import Path


def test_load_module_tree_logs_missing_tree_warning(caplog, tmp_path: Path):
    from codewiki.src.fe.visualise_docs import load_module_tree

    with caplog.at_level(logging.WARNING, logger="codewiki.src.fe.visualise_docs"):
        tree = load_module_tree(tmp_path)

    assert tree is None
    assert any("module_tree.json not found" in record.getMessage() for record in caplog.records)
