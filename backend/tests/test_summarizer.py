"""Test summarizer module with prompt loading and variable substitution."""
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import tempfile

from config import Config
from summarizer import (
    _estimate_tokens,
    _load_prompt_template,
    _substitute_config_variables,
    _chunk_segments,
    _strip_thinking_tags,
    _strip_numbered_analysis,
    _strip_prompt_instruction_echo,
    _finalize_summary_hygiene,
    _detect_repetition_loop,
    _auto_context_length,
    _validate_mode,
    _cap_summary_chunk_tokens,
    _prose_only_for_wrapped_section,
)


class TestProseOnlyForWrappedSection:
    """Strip spurious markdown headings from overview/conclusion LLM bodies."""

    def test_truncates_at_first_atx_heading(self):
        text = (
            "First paragraph.\n\nSecond line still prose.\n\n"
            "## Лишний заголовок\n\nShould be removed."
        )
        out = _prose_only_for_wrapped_section(text)
        assert "First paragraph" in out
        assert "Second line still prose" in out
        assert "## Лишний" not in out
        assert "Should be removed" not in out

    def test_indented_heading_still_truncates(self):
        text = "Intro.\n  ## Indented section\nTail."
        out = _prose_only_for_wrapped_section(text)
        assert out == "Intro."

    def test_empty_and_no_heading(self):
        assert _prose_only_for_wrapped_section("") == ""
        assert _prose_only_for_wrapped_section("Plain only.") == "Plain only."


class TestCapSummaryChunkTokens:
    """summary_chunk_max_tokens caps MAP / intermediate reduce budgets."""

    def test_zero_means_no_cap(self):
        config = Config(summary_chunk_max_tokens=0)
        assert _cap_summary_chunk_tokens(4096, config) == 4096

    def test_cap_truncates_large_budget(self):
        config = Config(summary_chunk_max_tokens=2048)
        assert _cap_summary_chunk_tokens(4096, config) == 2048

    def test_cap_does_not_raise_small_budget(self):
        config = Config(summary_chunk_max_tokens=2048)
        assert _cap_summary_chunk_tokens(1024, config) == 1024


class TestTokenEstimation:
    """Test token estimation logic."""

    def test_estimate_tokens_empty(self):
        """Test token estimation for empty text."""
        assert _estimate_tokens("") == 0

    def test_estimate_tokens_short(self):
        """Test token estimation for short text."""
        tokens = _estimate_tokens("abc")
        assert tokens == 1  # 3 chars / 3 = 1

    def test_estimate_tokens_long(self):
        """Test token estimation for longer text."""
        text = "a" * 300
        tokens = _estimate_tokens(text)
        assert tokens == 100  # 300 chars / 3 = 100

    def test_estimate_tokens_with_spaces(self):
        """Test token estimation with spaces."""
        tokens = _estimate_tokens("hello world test")
        assert tokens == 5  # 15 chars / 3 = 5


class TestLoadPromptTemplate:
    """Test prompt template loading with fallback."""

    def test_load_prompt_notes_chunk(self, temp_prompts_dir, monkeypatch):
        """Test loading notes mode chunk prompt."""
        monkeypatch.setattr("summarizer.PROMPTS_DIR", temp_prompts_dir)

        prompt = _load_prompt_template("notes", "chunk")
        assert "Notes chunk prompt" in prompt
        assert "{{TRANSCRIPT_CHUNK}}" in prompt

    def test_load_prompt_notes_final(self, temp_prompts_dir, monkeypatch):
        """Test loading notes mode final prompt."""
        monkeypatch.setattr("summarizer.PROMPTS_DIR", temp_prompts_dir)

        prompt = _load_prompt_template("notes", "final")
        assert "Notes final prompt" in prompt
        assert "{{CHUNK_SUMMARIES}}" in prompt

    def test_load_prompt_call_check(self, temp_prompts_dir, monkeypatch):
        """Test loading call_check mode prompt."""
        monkeypatch.setattr("summarizer.PROMPTS_DIR", temp_prompts_dir)

        prompt = _load_prompt_template("call_check", "chunk")
        assert "required phrases" in prompt
        assert "{{REQUIRED_PHRASES}}" in prompt

    def test_load_prompt_factcheck(self, temp_prompts_dir, monkeypatch):
        """Test loading factcheck mode prompt."""
        monkeypatch.setattr("summarizer.PROMPTS_DIR", temp_prompts_dir)

        prompt = _load_prompt_template("factcheck", "chunk")
        assert "Fact check" in prompt

    def test_load_prompt_tldr(self, temp_prompts_dir, monkeypatch):
        """Test loading tldr mode prompt."""
        monkeypatch.setattr("summarizer.PROMPTS_DIR", temp_prompts_dir)

        prompt = _load_prompt_template("tldr", "chunk")
        assert "TL;DR" in prompt

    def test_load_prompt_inquisition(self, temp_prompts_dir, monkeypatch):
        """Test loading inquisition mode prompt (must not fall back to notes)."""
        monkeypatch.setattr("summarizer.PROMPTS_DIR", temp_prompts_dir)

        chunk = _load_prompt_template("inquisition", "chunk")
        final = _load_prompt_template("inquisition", "final")
        assert "Inquisition chunk" in chunk
        assert "{{EVALUATION_GOAL}}" in chunk
        assert "Inquisition final" in final

    def test_load_bard_reduce_intermediate(self):
        """Bard tree-reduce uses a compact intermediate template (repo file)."""
        prompt = _load_prompt_template("bard", "reduce_intermediate")
        assert "{{CHUNK_SUMMARIES}}" in prompt
        assert "{{HIGHLIGHT_COUNT}}" in prompt

    def test_validate_mode_inquisition(self):
        assert _validate_mode("inquisition") == "inquisition"

    def test_load_prompt_fallback_to_notes(self, temp_prompts_dir, monkeypatch):
        """Test fallback to notes mode when custom mode not found."""
        monkeypatch.setattr("summarizer.PROMPTS_DIR", temp_prompts_dir)

        # Try to load a non-existent mode (should fall back to notes)
        prompt = _load_prompt_template("nonexistent", "chunk")
        assert "Notes chunk prompt" in prompt

    def test_load_prompt_fallback_to_old_style(self, temp_prompts_dir, monkeypatch):
        """Test fallback to old-style prompts."""
        # Create a temp dir with only old-style prompts
        old_dir = Path(tempfile.mkdtemp())
        try:
            (old_dir / "chunk_summary.txt").write_text("Old chunk format")
            (old_dir / "final_summary.txt").write_text("Old final format")

            monkeypatch.setattr("summarizer.PROMPTS_DIR", old_dir)

            prompt = _load_prompt_template("notes", "chunk")
            assert "Old chunk format" in prompt
        finally:
            # Cleanup
            (old_dir / "chunk_summary.txt").unlink()
            (old_dir / "final_summary.txt").unlink()
            old_dir.rmdir()

    def test_load_prompt_not_found(self, tmp_path, monkeypatch):
        """Test FileNotFoundError when no prompt found."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        monkeypatch.setattr("summarizer.PROMPTS_DIR", empty_dir)

        with pytest.raises(FileNotFoundError):
            _load_prompt_template("nonexistent", "chunk")

    def test_load_prompt_both_phases(self, temp_prompts_dir, monkeypatch):
        """Test loading both chunk and final phases."""
        monkeypatch.setattr("summarizer.PROMPTS_DIR", temp_prompts_dir)

        chunk = _load_prompt_template("notes", "chunk")
        final = _load_prompt_template("notes", "final")

        assert chunk != final
        assert "chunk" in chunk.lower()
        assert "final" in final.lower()


class TestSubstituteConfigVariables:
    """Test variable substitution in prompts."""

    def test_substitute_simple_variable(self):
        """Test substituting a single variable."""
        template = "Check for: {{REQUIRED_PHRASES}}"
        config_dict = {"REQUIRED_PHRASES": "contract"}
        result = _substitute_config_variables(template, config_dict)
        assert result == "Check for: contract"

    def test_substitute_multiple_variables(self):
        """Test substituting multiple variables."""
        template = "Check {{RED_FLAGS}} and {{REQUIRED_PHRASES}}"
        config_dict = {"RED_FLAGS": "fraud", "REQUIRED_PHRASES": "contract"}
        result = _substitute_config_variables(template, config_dict)
        assert "fraud" in result
        assert "contract" in result

    def test_substitute_list_to_bullet_points(self):
        """Test substituting list as bullet points."""
        template = "Required phrases:\n{{REQUIRED_PHRASES}}"
        config_dict = {"REQUIRED_PHRASES": ["contract", "payment", "signature"]}
        result = _substitute_config_variables(template, config_dict)
        assert "- contract" in result
        assert "- payment" in result
        assert "- signature" in result

    def test_substitute_tuple_to_bullet_points(self):
        """Test substituting tuple as bullet points."""
        template = "Items:\n{{ITEMS}}"
        config_dict = {"ITEMS": ("apple", "banana", "orange")}
        result = _substitute_config_variables(template, config_dict)
        assert "- apple" in result
        assert "- banana" in result

    def test_substitute_empty_template(self):
        """Test substituting in empty template."""
        result = _substitute_config_variables("", {})
        assert result == ""

    def test_substitute_no_placeholders(self):
        """Test substituting when template has no placeholders."""
        template = "This is plain text"
        config_dict = {"KEY": "value"}
        result = _substitute_config_variables(template, config_dict)
        assert result == template

    def test_substitute_missing_variable(self):
        """Test substituting when variable not in config."""
        template = "Check {{REQUIRED_PHRASES}}"
        config_dict = {"OTHER_KEY": "value"}
        result = _substitute_config_variables(template, config_dict)
        # Variable stays in template if not found
        assert "{{REQUIRED_PHRASES}}" in result

    def test_substitute_integer_value(self):
        """Test substituting integer value."""
        template = "Threshold: {{THRESHOLD}}"
        config_dict = {"THRESHOLD": 85}
        result = _substitute_config_variables(template, config_dict)
        assert "85" in result

    def test_substitute_float_value(self):
        """Test substituting float value."""
        template = "Score: {{SCORE}}"
        config_dict = {"SCORE": 0.95}
        result = _substitute_config_variables(template, config_dict)
        assert "0.95" in result

    def test_substitute_case_sensitive(self):
        """Test that substitution is case-sensitive."""
        template = "Check {{REQUIRED_PHRASES}}"
        config_dict = {"required_phrases": "contract"}
        result = _substitute_config_variables(template, config_dict)
        # Keys are case-sensitive, so substitution should happen if key matches exactly
        # But config_dict keys are typically uppercase in prompts
        assert "{{REQUIRED_PHRASES}}" in result  # Not substituted

    def test_substitute_complex_nested_structure(self):
        """Test substituting complex nested structures."""
        template = "Data: {{DATA}}"
        config_dict = {"DATA": {"nested": {"value": "test"}}}
        result = _substitute_config_variables(template, config_dict)
        assert "nested" in result


class TestChunkSegments:
    """Test chunking of transcript segments."""

    def test_chunk_empty_segments(self):
        """Test chunking empty segment list."""
        chunks = _chunk_segments([])
        assert chunks == []

    def test_chunk_single_segment(self):
        """Test chunking single segment."""
        segments = [{"start": 0.0, "end": 5.0, "text": "Hello world"}]
        chunks = _chunk_segments(segments, chunk_size=1500, overlap=200)
        assert len(chunks) == 1

    def test_chunk_multiple_small_segments(self):
        """Test chunking multiple small segments that fit in one chunk."""
        segments = [
            {"start": 0.0, "end": 5.0, "text": "Hello"},
            {"start": 5.0, "end": 10.0, "text": "world"},
        ]
        chunks = _chunk_segments(segments, chunk_size=1500, overlap=200)
        assert len(chunks) == 1

    def test_chunk_large_segments(self):
        """Test chunking segments that exceed chunk size."""
        segments = [
            {"start": 0.0, "end": 5.0, "text": "a" * 2000},
            {"start": 5.0, "end": 10.0, "text": "b" * 2000},
            {"start": 10.0, "end": 15.0, "text": "c" * 2000},
        ]
        chunks = _chunk_segments(segments, chunk_size=1500, overlap=200)
        assert len(chunks) > 1

    def test_chunk_respects_overlap(self):
        """Test that chunks maintain overlap."""
        segments = [
            {"start": 0.0, "end": 5.0, "text": "a" * 900},
            {"start": 5.0, "end": 10.0, "text": "b" * 900},
            {"start": 10.0, "end": 15.0, "text": "c" * 900},
        ]
        chunks = _chunk_segments(segments, chunk_size=1500, overlap=200)

        # If we have more than one chunk, check that content appears in multiple chunks
        if len(chunks) > 1:
            # Last line of first chunk should appear in second chunk (overlap)
            assert "a" in chunks[0]
            assert "a" in chunks[1] or "b" in chunks[1]

    def test_chunk_contains_text_only(self):
        """Test that chunks contain raw text without timestamp prefixes."""
        segments = [{"start": 10.5, "end": 15.0, "text": "Hello world"}]
        chunks = _chunk_segments(segments, chunk_size=1500, overlap=200)
        assert chunks[0] == "Hello world"

    def test_chunk_size_parameter(self):
        """Test chunk_size parameter."""
        segments = [
            {"start": 0.0, "end": 5.0, "text": "a" * 800},
            {"start": 5.0, "end": 10.0, "text": "b" * 800},
        ]
        chunks_small = _chunk_segments(segments, chunk_size=500, overlap=100)
        chunks_large = _chunk_segments(segments, chunk_size=2000, overlap=200)

        # Smaller chunk size should produce more chunks
        assert len(chunks_small) >= len(chunks_large)

    def test_chunk_overlap_parameter(self):
        """Test chunk_overlap parameter."""
        segments = [
            {"start": 0.0, "end": 5.0, "text": "a" * 800},
            {"start": 5.0, "end": 10.0, "text": "b" * 800},
            {"start": 10.0, "end": 15.0, "text": "c" * 800},
        ]
        chunks_no_overlap = _chunk_segments(segments, chunk_size=1000, overlap=0)
        chunks_with_overlap = _chunk_segments(segments, chunk_size=1000, overlap=200)

        # Both should produce chunks, overlap affects content distribution
        assert len(chunks_no_overlap) > 0
        assert len(chunks_with_overlap) > 0

    def test_chunk_preserves_order(self):
        """Test that chunks preserve segment order."""
        segments = [
            {"start": 0.0, "end": 5.0, "text": "first"},
            {"start": 5.0, "end": 10.0, "text": "second"},
            {"start": 10.0, "end": 15.0, "text": "third"},
        ]
        chunks = _chunk_segments(segments, chunk_size=1500, overlap=200)
        full_text = "\n".join(chunks)

        # Check order
        first_idx = full_text.find("first")
        second_idx = full_text.find("second")
        third_idx = full_text.find("third")

        assert first_idx < second_idx < third_idx

    def test_chunk_real_world_scenario(self, mock_segments):
        """Test chunking with realistic segments."""
        chunks = _chunk_segments(mock_segments, chunk_size=1500, overlap=200)

        # Should produce at least one chunk
        assert len(chunks) >= 1

        # All segments should appear in chunks
        full_text = "\n".join(chunks)
        for seg in mock_segments:
            assert seg["text"] in full_text


@pytest.fixture
def stub_gguf_path(tmp_path):
    """Real file path so summarize() can stat the model before Llama is mocked."""
    p = tmp_path / "stub.gguf"
    p.write_bytes(b"\0" * 4096)
    return str(p)


class TestSummarizerIntegration:
    """Integration tests for summarizer module."""

    def test_summarize_mocked_llm(self, mock_segments, temp_prompts_dir, monkeypatch, stub_gguf_path):
        """Test summarize function with mocked LLM."""
        from summarizer import summarize

        monkeypatch.setattr("summarizer.PROMPTS_DIR", temp_prompts_dir)

        # Mock the Llama model
        mock_llm = MagicMock()
        mock_response = {
            "choices": [{"message": {"content": "This is a summary."}}]
        }
        mock_llm.create_chat_completion.return_value = mock_response

        with patch.dict("sys.modules", {"llama_cpp": MagicMock()}), \
             patch("llama_cpp.Llama", return_value=mock_llm):
            config = Config(summary_mode="notes", llm_model_path=stub_gguf_path)
            result = summarize(mock_segments, config)

            assert isinstance(result, str)
            assert len(result) > 0

    def test_summarize_batch_mode(self, mock_segments, temp_prompts_dir, monkeypatch, stub_gguf_path):
        """Test summarize with batch_mode flag."""
        from summarizer import summarize

        monkeypatch.setattr("summarizer.PROMPTS_DIR", temp_prompts_dir)

        mock_llm = MagicMock()
        mock_response = {
            "choices": [{"message": {"content": "Batch summary"}}]
        }
        mock_llm.create_chat_completion.return_value = mock_response

        with patch.dict("sys.modules", {"llama_cpp": MagicMock()}), \
             patch("llama_cpp.Llama", return_value=mock_llm):
            config = Config(summary_mode="notes", llm_model_path=stub_gguf_path)
            result = summarize(mock_segments, config, batch_mode=True)

            assert isinstance(result, str)

    def test_summarize_with_progress_callback(
        self, mock_segments, temp_prompts_dir, monkeypatch, stub_gguf_path
    ):
        """Test summarize with progress callback."""
        from summarizer import summarize

        monkeypatch.setattr("summarizer.PROMPTS_DIR", temp_prompts_dir)

        callback_calls = []

        def progress_callback(percent, message):
            callback_calls.append((percent, message))

        mock_llm = MagicMock()
        mock_response = {
            "choices": [{"message": {"content": "Summary"}}]
        }
        mock_llm.create_chat_completion.return_value = mock_response

        with patch.dict("sys.modules", {"llama_cpp": MagicMock()}), \
             patch("llama_cpp.Llama", return_value=mock_llm):
            config = Config(summary_mode="notes", llm_model_path=stub_gguf_path)
            summarize(mock_segments, config, progress_cb=progress_callback)

            # Should have called progress callback
            assert len(callback_calls) > 0

    def test_summarize_call_check_mode_with_config(
        self, mock_segments, temp_prompts_dir, monkeypatch, stub_gguf_path
    ):
        """Test summarize with call_check mode and required phrases."""
        from summarizer import summarize

        monkeypatch.setattr("summarizer.PROMPTS_DIR", temp_prompts_dir)

        mock_llm = MagicMock()
        mock_response = {
            "choices": [{"message": {"content": "Call check summary"}}]
        }
        mock_llm.create_chat_completion.return_value = mock_response

        with patch.dict("sys.modules", {"llama_cpp": MagicMock()}), \
             patch("llama_cpp.Llama", return_value=mock_llm):
            config = Config(
                summary_mode="call_check",
                summary_mode_config={"required_phrases": ["contract", "payment"]},
                llm_model_path=stub_gguf_path,
            )
            result = summarize(mock_segments, config)

            assert isinstance(result, str)

    def test_summarize_empty_segments(self, temp_prompts_dir, monkeypatch, stub_gguf_path):
        """Test summarize with empty segments."""
        from summarizer import summarize

        monkeypatch.setattr("summarizer.PROMPTS_DIR", temp_prompts_dir)

        mock_llm = MagicMock()
        with patch.dict("sys.modules", {"llama_cpp": MagicMock()}), \
             patch("llama_cpp.Llama", return_value=mock_llm):
            config = Config(summary_mode="notes", llm_model_path=stub_gguf_path)
            result = summarize([], config)

            assert "_No content to summarize._" in result

    def test_modular_notes_always_runs_topic_reduce(
        self, mock_segments, temp_prompts_dir, monkeypatch, stub_gguf_path
    ):
        """Single-chunk topic groups must still invoke topic_reduce (shared template)."""
        from summarizer import summarize

        monkeypatch.setattr("summarizer.PROMPTS_DIR", temp_prompts_dir)

        mock_llm = MagicMock()
        long_body = "## Section\n\n" + ("Substantive summary text for tests. " * 3)
        mock_llm.create_chat_completion.return_value = {
            "choices": [{"message": {"content": long_body}}]
        }

        with patch.dict("sys.modules", {"llama_cpp": MagicMock()}), \
             patch("llama_cpp.Llama", return_value=mock_llm):
            config = Config(
                summary_mode="notes",
                llm_model_path=stub_gguf_path,
                topic_segmentation=True,
            )
            result = summarize(mock_segments, config)

        assert isinstance(result, str)
        prompts_concat = ""
        for call in mock_llm.create_chat_completion.call_args_list:
            kwargs = call[1] if len(call) > 1 else {}
            messages = kwargs.get("messages", [])
            for m in messages:
                prompts_concat += (m.get("content") or "") + "\n"
        assert "TOPIC_REDUCE_TEST_MARKER" in prompts_concat
        # Default auto language follows transcript → en; assembly headings must match.
        assert "## About this video" in result
        assert "## Conclusion" in result

    def test_modular_assembly_headings_follow_summary_language_ru(
        self, mock_segments, temp_prompts_dir, monkeypatch, stub_gguf_path
    ):
        """Explicit Russian summary must use Russian section titles, not English."""
        from summarizer import summarize

        monkeypatch.setattr("summarizer.PROMPTS_DIR", temp_prompts_dir)

        mock_llm = MagicMock()
        long_body = "## Section\n\n" + ("Substantive summary text for tests. " * 3)
        mock_llm.create_chat_completion.return_value = {
            "choices": [{"message": {"content": long_body}}]
        }

        with patch.dict("sys.modules", {"llama_cpp": MagicMock()}), \
             patch("llama_cpp.Llama", return_value=mock_llm):
            config = Config(
                summary_mode="notes",
                llm_model_path=stub_gguf_path,
                topic_segmentation=True,
                summary_language="ru",
            )
            result = summarize(mock_segments, config)

        assert "## О чём видео" in result
        assert "## Итог" in result

    def test_summarize_missing_prompts_fallback(self, tmp_path, monkeypatch, stub_gguf_path):
        """Test summarize falls back gracefully on missing prompts."""
        from summarizer import summarize

        empty_prompts = tmp_path / "prompts"
        empty_prompts.mkdir()
        monkeypatch.setattr("summarizer.PROMPTS_DIR", empty_prompts)

        mock_llm = MagicMock()
        with patch.dict("sys.modules", {"llama_cpp": MagicMock()}), \
             patch("llama_cpp.Llama", return_value=mock_llm):
            config = Config(summary_mode="notes", llm_model_path=stub_gguf_path)
            result = summarize([{"start": 0, "end": 5, "text": "test"}], config)

            assert "_Error loading prompt templates" in result


class TestStripThinkingTags:
    """Test _strip_thinking_tags with various model outputs."""

    def test_empty_input(self):
        assert _strip_thinking_tags("") == ""
        assert _strip_thinking_tags(None) is None

    def test_no_thinking_tags(self):
        text = "## О чём видео\nЭто видео про Python."
        assert _strip_thinking_tags(text) == text

    def test_complete_think_block(self):
        text = "<think>Let me analyze this...</think>## О чём видео\nЭто видео про Python."
        result = _strip_thinking_tags(text)
        assert "<think>" not in result
        assert "## О чём видео" in result

    def test_complete_thinking_block(self):
        text = "<thinking>I need to summarize...</thinking>\n\n## Конспект\nВажные моменты."
        result = _strip_thinking_tags(text)
        assert "<thinking>" not in result
        assert "## Конспект" in result

    def test_complete_thought_block(self):
        text = "<thought>Processing...</thought>Result here."
        result = _strip_thinking_tags(text)
        assert "Result here." in result
        assert "<thought>" not in result

    def test_complete_reasoning_block(self):
        text = "<reasoning>Step 1... Step 2...</reasoning>Final answer."
        result = _strip_thinking_tags(text)
        assert "Final answer." in result

    def test_reflection_block(self):
        text = "<reflection>Hmm, let me reconsider...</reflection>Actual content."
        result = _strip_thinking_tags(text)
        assert "Actual content." in result
        assert "<reflection>" not in result

    def test_scratchpad_block(self):
        text = "<scratchpad>Working memory here...</scratchpad>Real output."
        result = _strip_thinking_tags(text)
        assert "Real output." in result

    def test_multiple_consecutive_blocks(self):
        text = "<think>First thought</think><thinking>Second thought</thinking>Actual content."
        result = _strip_thinking_tags(text)
        assert "Actual content." in result
        assert "<think>" not in result
        assert "<thinking>" not in result

    def test_dangling_closing_tag(self):
        """Model started thinking implicitly, only marked the end."""
        text = "some internal reasoning here</think>## Real Content\nHere is the summary."
        result = _strip_thinking_tags(text)
        assert "## Real Content" in result
        assert "internal reasoning" not in result

    def test_dangling_opening_tag(self):
        """Model started thinking and never closed (truncation)."""
        text = "## Summary\nGood content.\n<think>Started reasoning but got cut off..."
        result = _strip_thinking_tags(text)
        assert "## Summary" in result
        assert "Good content." in result
        assert "reasoning" not in result

    def test_dangling_opening_then_heading_keeps_bard_body(self):
        """Truncated CoT followed by real markdown (bard final) must not lose the body."""
        text = (
            "<think>partial reasoning without close\n\n"
            "## О чём видео\n"
            "Одно предложение о ролике.\n\n"
            "## Лучшие моменты\n"
            "### №1 — Hook\n"
            "**Почему это цепляет:** test.\n"
        )
        result = _strip_thinking_tags(text)
        assert "<think>" not in result
        assert "## О чём видео" in result
        assert "Одно предложение" in result
        assert "## Лучшие моменты" in result
        assert "Hook" in result
        assert "partial reasoning" not in result

    def test_markdown_thinking_preamble_english(self):
        text = "## Thinking:\nLet me consider the transcript carefully.\n\n## О чём видео\nThis is about Python programming."
        result = _strip_thinking_tags(text)
        assert "## О чём видео" in result

    def test_markdown_thinking_preamble_russian(self):
        text = "Размышление: Мне нужно проанализировать транскрипт.\n\nОсновная тема видео — Python."
        result = _strip_thinking_tags(text)
        assert "Основная тема" in result

    def test_chinese_thinking_preamble(self):
        text = "思考过程: 我需要分析这个转录\n\n## 视频摘要\n这是关于Python的."
        result = _strip_thinking_tags(text)
        assert "## 视频摘要" in result

    def test_multiline_think_block(self):
        text = (
            "<think>\n"
            "Let me break this down step by step.\n"
            "1. First, I need to understand the topic\n"
            "2. Then extract key points\n"
            "3. Finally organize them\n"
            "</think>\n"
            "## Summary\nKey points from the video."
        )
        result = _strip_thinking_tags(text)
        assert "## Summary" in result
        assert "step by step" not in result

    def test_case_insensitive(self):
        text = "<THINK>Uppercase thinking</THINK>Content."
        result = _strip_thinking_tags(text)
        assert "Content." in result
        assert "Uppercase" not in result

    def test_spaces_in_tags(self):
        text = "< think >Some thought</ think >Content."
        result = _strip_thinking_tags(text)
        assert "Content." in result

    def test_only_thinking_returns_empty(self):
        """When model produces ONLY thinking, result should be empty."""
        text = "<think>This is all thinking with no real output at all.</think>"
        result = _strip_thinking_tags(text)
        assert result == ""

    def test_preserves_legitimate_content_with_angle_brackets(self):
        """Legitimate angle brackets in content should not be stripped."""
        text = "The function returns x < 10 and y > 5 as a boolean check."
        result = _strip_thinking_tags(text)
        assert "x < 10" in result

    def test_thinking_process_single_newline_before_heading(self):
        """Untagged CoT without blank line after 'Thinking Process:' must be stripped."""
        text = (
            "Thinking Process:\n"
            "1. **Analyze the Request:** do internal steps.\n"
            "## Раздел\n"
            "Содержимое конспекта."
        )
        result = _strip_thinking_tags(text)
        assert "## Раздел" in result
        assert "Содержимое конспекта" in result
        assert "Thinking Process" not in result
        assert "Analyze the Request" not in result

    def test_the_user_wants_prefix_stripped_to_heading(self):
        text = (
            "The user wants a deep analytical summary.\n"
            "I need to follow these steps.\n\n"
            "## Итог\n"
            "Кратко о видео."
        )
        result = _strip_thinking_tags(text)
        assert "## Итог" in result
        assert "The user wants" not in result

    def test_untagged_cot_no_heading_preserved(self):
        """If model never emits a markdown heading, do not destroy the text."""
        text = (
            "Thinking Process:\n"
            "1. Still reasoning.\n"
            "Plain answer without hash heading."
        )
        result = _strip_thinking_tags(text)
        assert "Plain answer" in result

    def test_real_world_qwen_output(self):
        """Simulate realistic Qwen model output with thinking block."""
        text = (
            "<think>\n"
            "Okay, I need to summarize this video transcript about machine learning.\n"
            "The key topics seem to be:\n"
            "- Neural networks basics\n"
            "- Training process\n"
            "- Real-world applications\n"
            "Let me organize these into a coherent summary.\n"
            "</think>\n\n"
            "## О чём видео\n"
            "Видео посвящено основам машинного обучения.\n\n"
            "## Нейронные сети\n"
            "Рассматриваются базовые принципы работы нейронных сетей."
        )
        result = _strip_thinking_tags(text)
        assert "## О чём видео" in result
        assert "Видео посвящено" in result
        assert "Okay, I need to" not in result
        assert "<think>" not in result


class TestStripNumberedCoT:
    """Regression: Qwen-style numbered planning steps (Determine, Final Polish)."""

    def test_determine_and_final_polish_stripped_keeps_summary(self):
        text = (
            "## О чём видео\n\n"
            "В этом разборе рассматриваются экономические перспективы на 2026 год.\n\n"
            "## [Тема фрагмента — назови по смыслу]\n\n"
            "3.  **Determine Content Type & Structure:**\n"
            "    *   Since the instruction says \"Connect facts\", I should structure logically.\n"
            "    *   The sponsorship can be integrated or separated.\n\n"
            "6.  **Final Polish (checking constraints):**\n"
            "    *   Language: Russian.\n"
            "    *   No thinking tags.\n\n"
            "## Экономический прогноз и состояние ключевых отраслей\n\n"
            "Анализ по данным ИНП РАН показывает, что восстановление экономики "
            "потребует около 20 лет.\n"
        )
        result = _strip_numbered_analysis(text)
        assert "Determine Content Type" not in result
        assert "Since the instruction" not in result
        assert "Final Polish" not in result
        assert "## О чём видео" in result
        assert "экономические перспективы" in result
        assert "## Экономический прогноз" in result
        assert "ИНП РАН" in result

    def test_numbered_cot_detected_after_long_preamble(self):
        """CoT after >2k chars of clean text must still trigger stripping."""
        preamble = "Вступление без маркеров планирования. " * 90
        assert len(preamble) > 2000
        text = (
            f"{preamble}\n\n"
            "8.  **Outline the narrative:**\n"
            "    *   First cover macro, then sectors.\n\n"
            "## Итоги\n\n"
            "Сохраняем этот абзац целиком.\n"
        )
        result = _strip_numbered_analysis(text)
        assert "Outline the narrative" not in result
        assert "First cover macro" not in result
        assert "## Итоги" in result
        assert "Сохраняем этот абзац" in result

    def test_final_polish_same_line_prose_stripped(self):
        """Numbered step must match when the model continues on the same line."""
        text = (
            "## Тема\n\n"
            "6.  **Final Polish:** Combine into a coherent narrative flow. "
            "Start with the broad economic outlook.\n"
            "    *   Bullet meta line.\n\n"
            "## Следующий раздел\n\n"
            "Настоящий текст абзаца.\n"
        )
        result = _strip_numbered_analysis(text)
        assert "Final Polish" not in result
        assert "Combine into a coherent" not in result
        assert "## Следующий раздел" in result
        assert "Настоящий текст" in result


class TestStripPromptInstructionEcho:
    """Catches bullet-label prompt echoes the model emits instead of content."""

    def test_topic_format_language_bullets_stripped(self):
        """The exact leak pattern from the 2h9m Apostol summary (Apr 16)."""
        text = (
            "    *   **Topic:** Based on the chunks, it's about \"Operational "
            "Activity, Supply and Communications of Units in Combat Zones\" with "
            "specific focus on logistics, supply drops, communications "
            "(Starlink/Telgram), air support/hovercraft operations, and combat "
            "environment details.\n"
            "    *   **Format:** Start immediately with `## Topic Title`. Use "
            "subsections (`###`) if needed. Continuous text, not a list of "
            "fragments. No intros/outros (\"In this section...\", \"Thus...\"). "
            "Write impersonally (\"it is discussed\", \"examples are given\"). "
            "Keep facts/numbers/citations prominent. Group by meaning, not "
            "fragment order.\n"
            "    *   **Language:** Russian.\n"
        )
        result = _strip_prompt_instruction_echo(text)
        assert "Topic:" not in result
        assert "Format:" not in result
        assert "Language:" not in result
        assert "Operational Activity" not in result
        assert "Start immediately" not in result
        assert result.strip() == ""

    def test_topic_echo_mixed_with_real_content(self):
        """Echo bullets stripped, real content preserved."""
        text = (
            "    *   **Topic:** About flywheels.\n"
            "    *   **Format:** Continuous text.\n\n"
            "## Маховики и рекуперация\n\n"
            "Рассматривается система рекуперации энергии на велосипеде с "
            "маховиком массой 5.5 кг.\n"
        )
        result = _strip_prompt_instruction_echo(text)
        assert "**Topic:**" not in result
        assert "**Format:**" not in result
        assert "## Маховики и рекуперация" in result
        assert "5.5 кг" in result

    def test_indented_continuation_dropped(self):
        """Prose continuation directly under an echo bullet is also dropped."""
        text = (
            "    *   **Format:** Use a structure with\n"
            "        subsections only when needed.\n"
            "        Keep facts prominent.\n\n"
            "## Реальный заголовок\n\n"
            "Настоящий контент.\n"
        )
        result = _strip_prompt_instruction_echo(text)
        assert "Format:" not in result
        assert "subsections only when needed" not in result
        assert "Keep facts prominent" not in result
        assert "## Реальный заголовок" in result
        assert "Настоящий контент" in result

    def test_legit_section_header_not_stripped(self):
        """A real `## Language` heading without trailing `:` must survive."""
        text = (
            "## Language\n\n"
            "Обсуждается лингвистика.\n"
        )
        result = _strip_prompt_instruction_echo(text)
        assert "## Language" in result
        assert "лингвистика" in result


class TestFinalizeSummaryHygiene:
    """End-to-end cleanup on assembled markdown (matches real leak patterns)."""

    def test_topic_format_language_full_pipeline(self):
        """Real leak pattern from video_summary.md must be fully cleaned."""
        raw = (
            "## Оперативная деятельность, снабжение и коммуникации\n\n"
            "    *   **Topic:** Based on the chunks, it's about logistics.\n"
            "    *   **Format:** Start immediately with `## Topic Title`.\n"
            "    *   **Language:** Russian.\n\n"
            "## Следующая тема\n\n"
            "Реальный контент раздела.\n"
        )
        result = _finalize_summary_hygiene(raw)
        assert "**Topic:**" not in result
        assert "**Format:**" not in result
        assert "**Language:**" not in result
        assert "Based on the chunks" not in result
        assert "## Следующая тема" in result
        assert "Реальный контент" in result


    def test_fixture_like_video_summary(self):
        raw = (
            "## О чём видео\n\nКраткий обзор.\n\n"
            "3.  **Determine Content Type & Structure:**\n"
            "    *   Since the instruction says connect facts.\n\n"
            "6.  **Final Polish:** Combine into narrative.\n"
            "    *   Language: Russian.\n\n"
            "## Role:** Конспектист (Note-taker).\n\n"
            "*   **Task:** Read a transcript fragment.\n"
            "*   **Goal:** Understand meaning.\n\n"
            "## Экономический прогноз\n\n"
            "Анализ по данным ИНП РАН показывает сроки восстановления.\n"
        )
        result = _finalize_summary_hygiene(raw)
        assert "Determine" not in result
        assert "Since the instruction" not in result
        assert "Final Polish" not in result
        assert "## Role:**" not in result
        assert "Note-taker" not in result
        assert "**Task:**" not in result
        assert "## Экономический прогноз" in result
        assert "ИНП РАН" in result

    def test_numbered_cot_stripped_when_mode_is_set(self):
        """Production passes mode=…; numbered_analysis must run (not only no-mode)."""
        raw = (
            "## Summary\n\n"
            "1.  **Analyze the Request:**\n"
            "    *   **Role:** Bard (storyteller).\n"
            "    *   **Task:** Select top moments.\n\n"
            "2.  **Analyze Input Content:**\n"
            "    *   Chunk 1 highlights.\n\n"
            "## О чём видео\n\n"
            "Видео про тестовый контент.\n"
        )
        for mode in ("bard", "notes"):
            result = _finalize_summary_hygiene(raw, mode=mode)
            assert "Analyze the Request" not in result, mode
            assert "Chunk 1 highlights" not in result, mode
            assert "## О чём видео" in result, mode
            assert "тестовый контент" in result, mode
