"""Test formatter module with markdown rendering and speaker labels."""
import pytest
from formatter import (
    _format_timestamp,
    _get_speaker_name,
    format_markdown,
    format_batch_markdown,
)


class TestFormatTimestamp:
    """Test timestamp formatting."""

    def test_format_timestamp_zero(self):
        """Test formatting zero seconds."""
        result = _format_timestamp(0.0)
        assert result == "0:00"

    def test_format_timestamp_seconds_only(self):
        """Test formatting seconds only."""
        result = _format_timestamp(45.7)
        assert result == "0:45"

    def test_format_timestamp_minutes(self):
        """Test formatting minutes and seconds."""
        result = _format_timestamp(125.3)  # 2:05
        assert result == "2:05"

    def test_format_timestamp_minutes_zero_pad(self):
        """Test that minutes and seconds are zero-padded."""
        result = _format_timestamp(65.0)  # 1:05
        assert result == "1:05"

    def test_format_timestamp_large_minutes(self):
        """Test formatting large minute values."""
        result = _format_timestamp(1225.0)  # 20:25
        assert result == "20:25"

    def test_format_timestamp_with_hours(self):
        """Test formatting with hours."""
        result = _format_timestamp(3665.0)  # 1:01:05
        assert "1:" in result
        assert ":01:05" in result

    def test_format_timestamp_hours_format(self):
        """Test that hours are included without padding."""
        result = _format_timestamp(7325.0)  # 2:02:05
        assert result.startswith("2:")

    def test_format_timestamp_large_duration(self):
        """Test formatting large durations."""
        result = _format_timestamp(36065.0)  # 10:01:05
        assert "10:" in result

    def test_format_timestamp_fractional_seconds(self):
        """Test that fractional seconds are truncated."""
        result1 = _format_timestamp(45.1)
        result2 = _format_timestamp(45.9)
        assert result1 == result2 == "0:45"


class TestGetSpeakerName:
    """Test speaker name resolution."""

    def test_get_speaker_name_with_mapping(self):
        """Test getting speaker name from mapping."""
        speaker_names = {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}
        result = _get_speaker_name("SPEAKER_00", speaker_names)
        assert result == "Alice"

    def test_get_speaker_name_fallback_to_id(self):
        """Test fallback to speaker ID when not in mapping."""
        speaker_names = {"SPEAKER_00": "Alice"}
        result = _get_speaker_name("SPEAKER_01", speaker_names)
        assert result == "SPEAKER_01"

    def test_get_speaker_name_empty_mapping(self):
        """Test with empty speaker names mapping."""
        result = _get_speaker_name("SPEAKER_00", {})
        assert result == "SPEAKER_00"

    def test_get_speaker_name_none_speaker_id(self):
        """Test with None speaker ID."""
        result = _get_speaker_name(None, {"SPEAKER_00": "Alice"})
        assert result == ""

    def test_get_speaker_name_empty_speaker_id(self):
        """Test with empty speaker ID."""
        result = _get_speaker_name("", {})
        assert result == ""

    def test_get_speaker_name_case_sensitive(self):
        """Test that speaker name lookup is case-sensitive."""
        speaker_names = {"SPEAKER_00": "Alice"}
        result = _get_speaker_name("speaker_00", speaker_names)
        assert result == "speaker_00"  # Not found, returns ID as-is


class TestFormatMarkdown:
    """Test markdown formatting for single video."""

    def test_format_markdown_basic(self, mock_segments):
        """Test basic markdown formatting."""
        result = format_markdown(
            "Test Video",
            "/path/to/video.mp4",
            mock_segments,
            "This is a summary",
        )

        assert "# Test Video" in result
        assert "This is a summary" in result
        assert "/path/to/video.mp4" in result

    def test_format_markdown_metadata(self, mock_segments):
        """Test that metadata is included."""
        result = format_markdown(
            "Test",
            "test.mp4",
            mock_segments,
            "Summary",
        )

        assert "Source:" in result
        assert "Duration:" in result
        assert "Words:" in result
        assert "Segments:" in result

    def test_format_markdown_segment_count(self, mock_segments):
        """Test that segment count is correct."""
        result = format_markdown(
            "Test",
            "test.mp4",
            mock_segments,
            "Summary",
        )

        assert f"Segments:** {len(mock_segments)}" in result

    def test_format_markdown_duration(self, mock_segments):
        """Test that total duration is calculated."""
        result = format_markdown(
            "Test",
            "test.mp4",
            mock_segments,
            "Summary",
        )

        # Last segment ends at 15.0s
        assert "15:" in result or "0:15" in result

    def test_format_markdown_without_speakers(self, mock_segments):
        """Test markdown without speaker information."""
        result = format_markdown(
            "Test",
            "test.mp4",
            mock_segments,
            "Summary",
        )

        # Should include timestamps
        assert "[0:00]" in result or "[0.0s]" in result

    def test_format_markdown_with_speakers_ignored_in_transcript(self, mock_segments_with_speaker):
        """Test that speaker labels are not shown in transcript section."""
        speaker_names = {
            "SPEAKER_00": "Manager",
            "SPEAKER_01": "Client",
        }
        result = format_markdown(
            "Test",
            "test.mp4",
            mock_segments_with_speaker,
            "Summary",
            speaker_names=speaker_names,
        )

        assert "Manager" not in result
        assert "Client" not in result
        assert "SPEAKER_" not in result

    def test_format_markdown_timestamps_only(self, mock_segments_with_speaker):
        """Test that transcript uses plain timestamp format even with speaker segments."""
        result = format_markdown(
            "Test",
            "test.mp4",
            mock_segments_with_speaker,
            "Summary",
        )

        assert "**[0:00]**" in result
        assert "**[0:05]**" in result
        assert "**[0:10]**" in result

    def test_format_markdown_summary_section(self, mock_segments):
        """Test that summary section is present."""
        result = format_markdown(
            "Test",
            "test.mp4",
            mock_segments,
            "This is the summary text",
        )

        assert "## Summary" in result
        assert "This is the summary text" in result

    def test_format_markdown_transcript_section(self, mock_segments):
        """Test that transcript section is present."""
        result = format_markdown(
            "Test",
            "test.mp4",
            mock_segments,
            "Summary",
        )

        assert "## Full Transcript" in result

    def test_format_markdown_empty_segments(self):
        """Test formatting with empty segments."""
        result = format_markdown(
            "Test",
            "test.mp4",
            [],
            "Summary",
        )

        assert "# Test" in result
        assert "Summary" in result
        # Duration should be 0
        assert "Duration:" in result

    def test_format_markdown_none_speaker_names(self, mock_segments_with_speaker):
        """Test with None speaker_names parameter."""
        result = format_markdown(
            "Test",
            "test.mp4",
            mock_segments_with_speaker,
            "Summary",
            speaker_names=None,
        )

        assert "SPEAKER_" not in result
        assert "## Full Transcript" in result

    def test_format_markdown_preserves_text_order(self, mock_segments):
        """Test that segment text order is preserved."""
        result = format_markdown(
            "Test",
            "test.mp4",
            mock_segments,
            "Summary",
        )

        # Check order of segment texts
        first_idx = result.find("Hello, welcome")
        second_idx = result.find("contract details")
        third_idx = result.find("payment terms")

        assert first_idx < second_idx < third_idx


class TestFormatBatchMarkdown:
    """Test batch markdown formatting for multiple videos."""

    def test_format_batch_markdown_basic(self):
        """Test basic batch markdown formatting."""
        batch_results = [
            {
                "title": "Video 1",
                "source": "video1.mp4",
                "segments": [
                    {"start": 0, "end": 10, "text": "Content 1"}
                ],
                "duration": 10,
            }
        ]
        result = format_batch_markdown(
            batch_results,
            "Combined summary",
        )

        assert "# Batch Transcription Report" in result
        assert "Combined summary" in result
        assert "Video 1" in result

    def test_format_batch_markdown_multiple_videos(self):
        """Test batch formatting with multiple videos."""
        batch_results = [
            {
                "title": "Video 1",
                "source": "video1.mp4",
                "segments": [{"start": 0, "end": 10, "text": "Content 1"}],
                "duration": 10,
            },
            {
                "title": "Video 2",
                "source": "video2.mp4",
                "segments": [{"start": 0, "end": 20, "text": "Content 2"}],
                "duration": 20,
            },
        ]
        result = format_batch_markdown(
            batch_results,
            "Summary",
        )

        assert "Videos processed:** 2" in result
        assert "Video 1" in result
        assert "Video 2" in result

    def test_format_batch_markdown_video_count(self):
        """Test that video count is correct."""
        batch_results = [
            {
                "title": f"Video {i}",
                "source": f"video{i}.mp4",
                "segments": [{"start": 0, "end": 10, "text": f"Content {i}"}],
                "duration": 10,
            }
            for i in range(3)
        ]
        result = format_batch_markdown(
            batch_results,
            "Summary",
        )

        assert "Videos processed:** 3" in result

    def test_format_batch_markdown_total_duration(self):
        """Test that total duration is calculated."""
        batch_results = [
            {
                "title": "Video 1",
                "source": "video1.mp4",
                "segments": [{"start": 0, "end": 10, "text": "Content 1"}],
                "duration": 10,
            },
            {
                "title": "Video 2",
                "source": "video2.mp4",
                "segments": [{"start": 0, "end": 20, "text": "Content 2"}],
                "duration": 20,
            },
        ]
        result = format_batch_markdown(
            batch_results,
            "Summary",
        )

        # Total is 30 seconds = 0:30
        assert "Total duration:" in result

    def test_format_batch_markdown_word_count(self):
        """Test that word count is calculated."""
        batch_results = [
            {
                "title": "Video 1",
                "source": "video1.mp4",
                "segments": [{"start": 0, "end": 10, "text": "one two three"}],
                "duration": 10,
            }
        ]
        result = format_batch_markdown(
            batch_results,
            "Summary",
        )

        assert "Total words:" in result

    def test_format_batch_markdown_overall_summary(self):
        """Test that overall summary section is present."""
        batch_results = [
            {
                "title": "Video 1",
                "source": "video1.mp4",
                "segments": [{"start": 0, "end": 10, "text": "Content"}],
                "duration": 10,
            }
        ]
        result = format_batch_markdown(
            batch_results,
            "This is the overall summary",
        )

        assert "## Overall Summary" in result
        assert "This is the overall summary" in result

    def test_format_batch_markdown_per_video_sections(self):
        """Test that per-video sections are present."""
        batch_results = [
            {
                "title": "Video 1",
                "source": "video1.mp4",
                "segments": [{"start": 0, "end": 10, "text": "Content"}],
                "duration": 10,
            },
            {
                "title": "Video 2",
                "source": "video2.mp4",
                "segments": [{"start": 0, "end": 20, "text": "Content"}],
                "duration": 20,
            },
        ]
        result = format_batch_markdown(
            batch_results,
            "Summary",
        )

        assert "## Video 1:" in result
        assert "## Video 2:" in result

    def test_format_batch_markdown_with_speakers(self):
        """Test batch formatting with speaker information."""
        batch_results = [
            {
                "title": "Video 1",
                "source": "video1.mp4",
                "segments": [
                    {
                        "start": 0,
                        "end": 10,
                        "text": "Hello",
                        "speaker": "SPEAKER_00",
                    }
                ],
                "duration": 10,
            }
        ]
        speaker_names = {"SPEAKER_00": "Manager"}
        result = format_batch_markdown(
            batch_results,
            "Summary",
            speaker_names=speaker_names,
        )

        assert "Manager" not in result
        assert "SPEAKER_" not in result
        assert "**[0:00]**" in result

    def test_format_batch_markdown_empty_results(self):
        """Test batch formatting with empty results."""
        result = format_batch_markdown(
            [],
            "Summary",
        )

        assert "# Batch Transcription Report" in result
        assert "Videos processed:** 0" in result

    def test_format_batch_markdown_default_speaker_names(self):
        """Test batch formatting with None speaker_names."""
        batch_results = [
            {
                "title": "Video 1",
                "source": "video1.mp4",
                "segments": [{"start": 0, "end": 10, "text": "Content"}],
                "duration": 10,
            }
        ]
        result = format_batch_markdown(
            batch_results,
            "Summary",
            speaker_names=None,
        )

        assert "# Batch Transcription Report" in result

    def test_format_batch_markdown_per_video_transcripts(self):
        """Test that per-video transcripts are included."""
        batch_results = [
            {
                "title": "Video 1",
                "source": "video1.mp4",
                "segments": [{"start": 0, "end": 10, "text": "Content 1"}],
                "duration": 10,
            }
        ]
        result = format_batch_markdown(
            batch_results,
            "Summary",
        )

        assert "### Transcript" in result
        assert "Content 1" in result

    def test_format_batch_markdown_per_video_metadata(self):
        """Test that per-video metadata is included."""
        batch_results = [
            {
                "title": "Video 1",
                "source": "video1.mp4",
                "segments": [
                    {"start": 0, "end": 10, "text": "Content 1"}
                ],
                "duration": 10,
            }
        ]
        result = format_batch_markdown(
            batch_results,
            "Summary",
        )

        assert "video1.mp4" in result
        assert "Source:" in result
        assert "Duration:" in result

    def test_format_batch_markdown_missing_optional_fields(self):
        """Test batch formatting when optional fields are missing."""
        batch_results = [
            {
                "title": "Video 1",
                # Missing source, segments, duration
            }
        ]
        # Should not crash
        result = format_batch_markdown(
            batch_results,
            "Summary",
        )

        assert "# Batch Transcription Report" in result
