"""Test LLM-based diarizer module."""
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from config import Config
from diarizer import (
    diarize_with_llm,
    _format_lines_for_llm,
    _parse_speaker_assignments,
    _load_diarize_prompt,
)


class TestFormatLinesForLLM:
    """Test transcript formatting for LLM input."""

    def test_basic_formatting(self):
        segments = [
            {"start": 0.0, "end": 2.5, "text": "Hello world"},
            {"start": 2.5, "end": 5.0, "text": "How are you"},
        ]
        result = _format_lines_for_llm(segments)
        assert "1. [0.0s] Hello world" in result
        assert "2. [2.5s] How are you" in result

    def test_formatting_with_offset(self):
        segments = [
            {"start": 10.0, "end": 12.0, "text": "Continuation"},
        ]
        result = _format_lines_for_llm(segments, start_idx=5)
        assert "6. [10.0s] Continuation" in result

    def test_empty_segments(self):
        result = _format_lines_for_llm([])
        assert result == ""


class TestParseSpeakerAssignments:
    """Test LLM response parsing."""

    def test_colon_format(self):
        response = "1:S1\n2:S2\n3:S1"
        result = _parse_speaker_assignments(response, 3)
        assert result == {1: "SPEAKER_00", 2: "SPEAKER_01", 3: "SPEAKER_00"}

    def test_colon_with_space(self):
        response = "1: S1\n2: S2"
        result = _parse_speaker_assignments(response, 2)
        assert result == {1: "SPEAKER_00", 2: "SPEAKER_01"}

    def test_dash_format(self):
        response = "1 - S1\n2 - S2"
        result = _parse_speaker_assignments(response, 2)
        assert result == {1: "SPEAKER_00", 2: "SPEAKER_01"}

    def test_dot_format(self):
        response = "1. S1\n2. S2\n3. S1"
        result = _parse_speaker_assignments(response, 3)
        assert result == {1: "SPEAKER_00", 2: "SPEAKER_01", 3: "SPEAKER_00"}

    def test_ignores_out_of_range(self):
        response = "1:S1\n2:S2\n999:S3"
        result = _parse_speaker_assignments(response, 5)
        assert 999 not in result
        assert result == {1: "SPEAKER_00", 2: "SPEAKER_01"}

    def test_empty_response(self):
        result = _parse_speaker_assignments("", 5)
        assert result == {}

    def test_garbage_response(self):
        result = _parse_speaker_assignments("This is not valid output at all", 5)
        assert result == {}

    def test_mixed_format(self):
        response = "1:S1\n2: S1\n3 - S2\n4. S2"
        result = _parse_speaker_assignments(response, 4)
        assert len(result) == 4
        assert result[1] == "SPEAKER_00"
        assert result[4] == "SPEAKER_01"

    def test_two_phase_output_with_assignments_block(self):
        response = (
            "SPEAKERS:\n"
            "S1: Author/host\n"
            "S2: Guest\n"
            "\n"
            "ASSIGNMENTS:\n"
            "1:S1\n"
            "2:S2\n"
            "3:S1\n"
        )
        result = _parse_speaker_assignments(response, 3)
        assert result == {1: "SPEAKER_00", 2: "SPEAKER_01", 3: "SPEAKER_00"}

    def test_two_phase_ignores_speakers_block_labels(self):
        """S1/S2 in the SPEAKERS: section should not be misinterpreted as line assignments."""
        response = (
            "SPEAKERS:\n"
            "S1: Main host who leads the show\n"
            "S2: Expert guest\n"
            "\n"
            "ASSIGNMENTS:\n"
            "1:S1\n"
            "2:S1\n"
        )
        result = _parse_speaker_assignments(response, 2)
        assert result == {1: "SPEAKER_00", 2: "SPEAKER_00"}

    def test_quoted_clip_label_sq(self):
        response = "1:S1\n2:SQ\n3:S1"
        result = _parse_speaker_assignments(response, 3)
        assert result[1] == "SPEAKER_00"
        assert result[2] == "SPEAKER_QUOTED"
        assert result[3] == "SPEAKER_00"

    def test_quoted_clip_in_two_phase(self):
        response = (
            "SPEAKERS:\n"
            "S1: Author\n"
            "SQ: Quoted news clip\n"
            "\n"
            "ASSIGNMENTS:\n"
            "1:S1\n"
            "2:SQ\n"
            "3:S1\n"
        )
        result = _parse_speaker_assignments(response, 3)
        assert result[2] == "SPEAKER_QUOTED"

    def test_warning_on_zero_assignments(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="diarizer"):
            result = _parse_speaker_assignments("no valid content", 5)
        assert result == {}
        assert "zero valid speaker assignments" in caplog.text


class TestLoadDiarizePrompt:
    """Test prompt template loading."""

    def test_prompt_exists(self):
        prompt = _load_diarize_prompt()
        assert "{{LINES}}" in prompt
        assert len(prompt) > 50

    def test_prompt_contains_instructions(self):
        prompt = _load_diarize_prompt()
        assert "S1" in prompt
        assert "speaker" in prompt.lower()

    def test_prompt_has_two_phase_structure(self):
        prompt = _load_diarize_prompt()
        assert "SPEAKERS:" in prompt
        assert "ASSIGNMENTS:" in prompt

    def test_prompt_mentions_quoted_clips(self):
        prompt = _load_diarize_prompt()
        assert "SQ" in prompt
        assert "quoted" in prompt.lower() or "QUOTED" in prompt


class TestDiarizeWithLLM:
    """Test the main LLM diarization function."""

    def test_empty_segments_returns_empty(self):
        config = Config(diarize=True)
        result = diarize_with_llm([], config)
        assert result == []

    def test_missing_prompt_returns_segments_unchanged(self):
        config = Config(diarize=True)
        segments = [
            {"start": 0.0, "end": 5.0, "text": "Hello"},
        ]
        with patch("diarizer._load_diarize_prompt", side_effect=FileNotFoundError):
            result = diarize_with_llm(segments, config)
        assert result == segments
        assert "speaker" not in result[0]

    @patch("diarizer._load_llm")
    @patch("diarizer._load_diarize_prompt")
    def test_successful_diarization(self, mock_prompt, mock_llm):
        mock_prompt.return_value = "Test prompt {{LINES}}"
        mock_llm_instance = MagicMock()
        mock_llm_instance.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "1:S1\n2:S2\n3:S1"}}]
        }
        mock_llm.return_value = mock_llm_instance

        config = Config(diarize=True)
        segments = [
            {"start": 0.0, "end": 3.0, "text": "Good morning"},
            {"start": 3.0, "end": 6.0, "text": "Hi there"},
            {"start": 6.0, "end": 9.0, "text": "Let's begin"},
        ]
        result = diarize_with_llm(segments, config)
        assert len(result) == 3
        assert result[0]["speaker"] == "SPEAKER_00"
        assert result[1]["speaker"] == "SPEAKER_01"
        assert result[2]["speaker"] == "SPEAKER_00"

    @patch("diarizer._load_llm")
    @patch("diarizer._load_diarize_prompt")
    def test_llm_failure_defaults_to_speaker_00(self, mock_prompt, mock_llm):
        mock_prompt.return_value = "Test prompt {{LINES}}"
        mock_llm.side_effect = Exception("LLM load failed")

        config = Config(diarize=True)
        segments = [
            {"start": 0.0, "end": 3.0, "text": "Hello"},
            {"start": 3.0, "end": 6.0, "text": "World"},
        ]
        result = diarize_with_llm(segments, config)
        assert len(result) == 2
        for seg in result:
            assert seg["speaker"] == "SPEAKER_00"

    @patch("diarizer._load_llm")
    @patch("diarizer._load_diarize_prompt")
    def test_progress_callback_called(self, mock_prompt, mock_llm):
        mock_prompt.return_value = "Test prompt {{LINES}}"
        mock_llm_instance = MagicMock()
        mock_llm_instance.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "1:S1"}}]
        }
        mock_llm.return_value = mock_llm_instance

        progress_calls = []
        config = Config(diarize=True)
        segments = [{"start": 0.0, "end": 3.0, "text": "Test"}]
        diarize_with_llm(
            segments, config,
            progress_cb=lambda p, msg: progress_calls.append((p, msg)),
        )
        assert len(progress_calls) > 0

    @patch("diarizer._load_llm")
    @patch("diarizer._load_diarize_prompt")
    def test_unassigned_lines_default_to_speaker_00(self, mock_prompt, mock_llm):
        mock_prompt.return_value = "Test prompt {{LINES}}"
        mock_llm_instance = MagicMock()
        mock_llm_instance.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "1:S1\n3:S2"}}]
        }
        mock_llm.return_value = mock_llm_instance

        config = Config(diarize=True)
        segments = [
            {"start": 0.0, "end": 3.0, "text": "Line one"},
            {"start": 3.0, "end": 6.0, "text": "Line two"},
            {"start": 6.0, "end": 9.0, "text": "Line three"},
        ]
        result = diarize_with_llm(segments, config)
        assert result[0]["speaker"] == "SPEAKER_00"
        assert result[1]["speaker"] == "SPEAKER_00"  # unassigned -> default
        assert result[2]["speaker"] == "SPEAKER_01"

    @patch("diarizer._load_llm")
    @patch("diarizer._load_diarize_prompt")
    def test_speaker_id_format(self, mock_prompt, mock_llm):
        mock_prompt.return_value = "Test prompt {{LINES}}"
        mock_llm_instance = MagicMock()
        mock_llm_instance.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "1:S1\n2:S3"}}]
        }
        mock_llm.return_value = mock_llm_instance

        config = Config(diarize=True)
        segments = [
            {"start": 0.0, "end": 3.0, "text": "A"},
            {"start": 3.0, "end": 6.0, "text": "B"},
        ]
        result = diarize_with_llm(segments, config)
        for seg in result:
            assert seg["speaker"].startswith("SPEAKER_")
            parts = seg["speaker"].split("_")
            assert len(parts) == 2
            assert parts[1].isdigit()
            assert len(parts[1]) == 2


class TestDiarizationConfig:
    """Test diarization-related configuration."""

    def test_diarize_disabled_by_default(self):
        config = Config()
        assert config.diarize is False

    def test_diarize_enabled(self):
        config = Config(diarize=True)
        assert config.diarize is True

    def test_speaker_names_mapping(self):
        names = {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}
        config = Config(diarize=True, speaker_names=names)
        assert config.speaker_names == names

    def test_speaker_names_empty_default(self):
        config = Config()
        assert config.speaker_names == {}

    def test_no_diarize_backend_field(self):
        config = Config()
        assert not hasattr(config, "diarize_backend")

    def test_no_clustering_threshold_field(self):
        config = Config()
        assert not hasattr(config, "diarize_clustering_threshold")

    def test_no_num_speakers_field(self):
        config = Config()
        assert not hasattr(config, "num_speakers")
