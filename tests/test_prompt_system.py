"""Tests for prompt system composition contracts.

Ensures that shared blocks (WRITING_DISCIPLINE, GROUNDING_RULES, etc.)
are properly injected into all prompt types, and that language instructions
are consistent across module, overview, and guide paths.
"""

from codewiki.src.be.prompt_template import (
    format_system_prompt,
    format_leaf_system_prompt,
    format_overview_prompt,
    format_language_instruction,
    GETTING_STARTED_PROMPT,
    BEGINNER_SECTION_PROMPT,
    BEGINNER_PARENT_PROMPT,
    BUILD_ANALYSIS_PROMPT,
    ALGORITHM_DEEPDIVE_PROMPT,
    ALGORITHM_PARENT_PROMPT,
    REPO_OVERVIEW_PROMPT,
    MODULE_OVERVIEW_PROMPT,
    _WRITING_DISCIPLINE,
)


class TestWritingDisciplineInjection:
    """WRITING_DISCIPLINE must appear in all doc-generating prompts."""

    def test_system_prompt_has_discipline(self):
        prompt = format_system_prompt("TestModule")
        assert "WRITING_DISCIPLINE" in prompt
        assert "Vary sentence structure" in prompt

    def test_leaf_system_prompt_has_discipline(self):
        prompt = format_leaf_system_prompt("TestModule")
        assert "WRITING_DISCIPLINE" in prompt
        assert "Vary sentence structure" in prompt

    def test_repo_overview_has_discipline(self):
        assert "WRITING_DISCIPLINE" in REPO_OVERVIEW_PROMPT

    def test_module_overview_has_discipline(self):
        assert "WRITING_DISCIPLINE" in MODULE_OVERVIEW_PROMPT

    def test_getting_started_has_discipline(self):
        assert "WRITING_DISCIPLINE" in GETTING_STARTED_PROMPT

    def test_beginner_section_has_discipline(self):
        assert "WRITING_DISCIPLINE" in BEGINNER_SECTION_PROMPT

    def test_beginner_parent_has_discipline(self):
        assert "WRITING_DISCIPLINE" in BEGINNER_PARENT_PROMPT

    def test_build_analysis_has_discipline(self):
        assert "WRITING_DISCIPLINE" in BUILD_ANALYSIS_PROMPT

    def test_algorithm_deepdive_has_discipline(self):
        assert "WRITING_DISCIPLINE" in ALGORITHM_DEEPDIVE_PROMPT

    def test_algorithm_parent_has_discipline(self):
        assert "WRITING_DISCIPLINE" in ALGORITHM_PARENT_PROMPT


class TestSharedBlocksPreserved:
    """System prompts must retain grounding rules and key structural elements."""

    def test_system_prompt_has_grounding_rules(self):
        prompt = format_system_prompt("TestModule")
        assert "GROUNDING_RULES" in prompt
        assert "Do NOT invent or hallucinate" in prompt

    def test_leaf_prompt_has_grounding_rules(self):
        prompt = format_leaf_system_prompt("TestModule")
        assert "GROUNDING_RULES" in prompt
        assert "Do NOT invent or hallucinate" in prompt

    def test_system_prompt_has_evidence_rules(self):
        prompt = format_system_prompt("TestModule")
        assert "Evidence-Driven Writing Rules" in prompt

    def test_leaf_prompt_has_evidence_rules(self):
        prompt = format_leaf_system_prompt("TestModule")
        assert "Evidence-Driven Writing Rules" in prompt

    def test_system_prompt_has_objectives(self):
        prompt = format_system_prompt("TestModule")
        assert "Why this module exists" in prompt
        assert "How it connects" in prompt

    def test_leaf_prompt_has_objectives(self):
        prompt = format_leaf_system_prompt("TestModule")
        assert "Why this module exists" in prompt

    def test_system_prompt_has_workflow(self):
        prompt = format_system_prompt("TestModule")
        assert "generate_sub_module_documentation" in prompt

    def test_leaf_prompt_no_sub_module_delegation(self):
        prompt = format_leaf_system_prompt("TestModule")
        assert "generate_sub_module_documentation" not in prompt


class TestBannedWordList:
    """The banned word list must be present in WRITING_DISCIPLINE."""

    def test_english_banned_words(self):
        assert "delve" in _WRITING_DISCIPLINE
        assert "tapestry" in _WRITING_DISCIPLINE
        assert "leverage" in _WRITING_DISCIPLINE
        assert "holistic" in _WRITING_DISCIPLINE

    def test_chinese_banned_phrases(self):
        assert "值得注意的是" in _WRITING_DISCIPLINE
        assert "综上所述" in _WRITING_DISCIPLINE
        assert "众所周知" in _WRITING_DISCIPLINE


class TestLanguageInstructionConsistency:
    """Guide and module language instructions must have the same rules."""

    def test_guide_language_has_parenthetical_explanation(self):
        inst = format_language_instruction("zh")
        assert "parenthetical explanation" in inst

    def test_guide_language_has_mid_sentence_switching_rule(self):
        inst = format_language_instruction("zh")
        assert "mid-sentence language switching" in inst

    def test_module_language_has_parenthetical_explanation(self):
        prompt = format_system_prompt("Test", output_language="zh")
        assert "parenthetical explanation" in prompt

    def test_module_language_has_mid_sentence_switching_rule(self):
        prompt = format_system_prompt("Test", output_language="zh")
        assert "mid-sentence language switching" in prompt

    def test_overview_language_has_parenthetical_explanation(self):
        prompt = format_overview_prompt("test", "{}", output_language="zh")
        assert "parenthetical explanation" in prompt

    def test_english_produces_no_language_block(self):
        inst = format_language_instruction("en")
        assert inst == ""
        prompt = format_system_prompt("Test", output_language="en")
        assert "<OUTPUT_LANGUAGE>" not in prompt


class TestOverviewMandatoryCoverage:
    """Overview prompts must list all required topics as mandatory."""

    def test_repo_overview_all_topics_mandatory(self):
        assert "must cover ALL" in REPO_OVERVIEW_PROMPT
        assert "What this project does" in REPO_OVERVIEW_PROMPT
        assert "Architecture at a glance" in REPO_OVERVIEW_PROMPT
        assert "Key design decisions" in REPO_OVERVIEW_PROMPT
        assert "Module guide" in REPO_OVERVIEW_PROMPT
        assert "End-to-end workflows" in REPO_OVERVIEW_PROMPT

    def test_module_overview_all_topics_mandatory(self):
        assert "must cover ALL" in MODULE_OVERVIEW_PROMPT
        assert "Purpose" in MODULE_OVERVIEW_PROMPT
        assert "Architecture" in MODULE_OVERVIEW_PROMPT
        assert "How sub-modules interact" in MODULE_OVERVIEW_PROMPT
        assert "Design tradeoffs" in MODULE_OVERVIEW_PROMPT

    def test_repo_overview_allows_free_ordering(self):
        assert "whatever order" in REPO_OVERVIEW_PROMPT
