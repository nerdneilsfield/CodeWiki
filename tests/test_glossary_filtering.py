import pytest


class TestGlossaryEntry:
    def test_structured_glossary_entry(self):
        from codewiki.src.be.generation.glossary import GlossaryEntry

        entry = GlossaryEntry(
            term="MyClass",
            definition="A class.",
            symbol_id="py:src/foo.py#MyClass(class)",
            file_path="src/foo.py",
            kind="class",
        )
        assert entry.symbol_id == "py:src/foo.py#MyClass(class)"


class TestFilterGlossary:
    def test_filters_by_symbol_ids(self):
        from codewiki.src.be.generation.glossary import GlossaryEntry, filter_glossary

        glossary = {
            "A": GlossaryEntry("A", "def A", "sym:A", "src/a.py", "function"),
            "B": GlossaryEntry("B", "def B", "sym:B", "src/b.py", "function"),
            "C": GlossaryEntry("C", "def C", "sym:C", "src/c.py", "function"),
        }

        relevant = filter_glossary(glossary, relevant_symbol_ids={"sym:A", "sym:B"})
        assert "A" in relevant
        assert "B" in relevant
        assert "C" not in relevant

    def test_path_proximity_adds_entries(self):
        from codewiki.src.be.generation.glossary import GlossaryEntry, filter_glossary

        glossary = {
            "A": GlossaryEntry("A", "def A", "sym:A", "src/auth/a.py", "function"),
            "B": GlossaryEntry("B", "def B", "sym:B", "src/auth/b.py", "function"),
            "C": GlossaryEntry("C", "def C", "sym:C", "src/db/c.py", "function"),
        }

        relevant = filter_glossary(
            glossary,
            relevant_symbol_ids={"sym:A"},
            module_file_paths={"src/auth/a.py"},
        )
        assert "A" in relevant
        assert "B" in relevant
        assert "C" not in relevant

    def test_token_limit_truncates(self):
        from codewiki.src.be.generation.glossary import GlossaryEntry, filter_glossary

        glossary = {
            f"sym_{i}": GlossaryEntry(f"sym_{i}", "x" * 100, f"id:{i}", f"src/{i}.py", "function")
            for i in range(100)
        }

        relevant = filter_glossary(
            glossary,
            relevant_symbol_ids={f"id:{i}" for i in range(100)},
            token_limit=500,
        )
        assert len(relevant) < 100
