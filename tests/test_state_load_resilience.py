import json


class TestGenerationStateLoadResilience:
    def test_corrupt_json_returns_empty_state(self, tmp_path):
        from codewiki.src.be.generation_state import GenerationState

        path = tmp_path / "generation_state.json"
        path.write_text("{invalid json content!!!", encoding="utf-8")
        state = GenerationState.load(str(path))
        assert len(state.tasks) == 0

    def test_truncated_json_returns_empty_state(self, tmp_path):
        from codewiki.src.be.generation_state import GenerationState

        path = tmp_path / "generation_state.json"
        path.write_text('{"schema_version": "v1", "tasks": [', encoding="utf-8")
        state = GenerationState.load(str(path))
        assert len(state.tasks) == 0

    def test_malformed_task_skipped(self, tmp_path):
        from codewiki.src.be.generation_state import GenerationState

        path = tmp_path / "generation_state.json"
        data = {
            "repo_commit": "abc",
            "tasks": [
                {
                    "doc_id": "good",
                    "kind": "module",
                    "module_path": ["A"],
                    "output_file": "a.md",
                    "status": "completed",
                },
                {"broken": "missing required fields"},
                {
                    "doc_id": "also_good",
                    "kind": "module",
                    "module_path": ["B"],
                    "output_file": "b.md",
                    "status": "planned",
                },
            ],
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        state = GenerationState.load(str(path))
        assert "good" in state.tasks
        assert "also_good" in state.tasks
        assert len(state.tasks) == 2

    def test_missing_file_returns_empty_state(self, tmp_path):
        from codewiki.src.be.generation_state import GenerationState

        state = GenerationState.load(str(tmp_path / "nonexistent.json"))
        assert len(state.tasks) == 0
