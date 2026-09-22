"""Pytest configuration and shared fixtures."""
import pytest
from pathlib import Path
from unittest.mock import Mock, MagicMock
from config import Config


@pytest.fixture
def config_default():
    """Create a default Config object."""
    return Config()


@pytest.fixture
def config_custom():
    """Create a Config object with custom values."""
    return Config(
        whisper_model="small",
        language="en",
        summary_mode="call_check",
        summary_mode_config={"required_phrases": ["contract", "payment"]},
        diarize=True,
        speaker_names={"SPEAKER_00": "Manager", "SPEAKER_01": "Client"},
        batch_mode=True,
    )


@pytest.fixture
def mock_segments():
    """Create sample transcript segments."""
    return [
        {
            "start": 0.0,
            "end": 5.0,
            "text": "Hello, welcome to the meeting.",
            "language": "en",
        },
        {
            "start": 5.0,
            "end": 10.0,
            "text": "Let's discuss the contract details.",
            "language": "en",
        },
        {
            "start": 10.0,
            "end": 15.0,
            "text": "The payment terms are in the agreement.",
            "language": "en",
        },
    ]


@pytest.fixture
def mock_segments_with_speaker():
    """Create sample segments with speaker labels."""
    return [
        {
            "start": 0.0,
            "end": 5.0,
            "text": "Hello, welcome to the meeting.",
            "language": "en",
            "speaker": "SPEAKER_00",
        },
        {
            "start": 5.0,
            "end": 10.0,
            "text": "Let's discuss the contract details.",
            "language": "en",
            "speaker": "SPEAKER_01",
        },
        {
            "start": 10.0,
            "end": 15.0,
            "text": "The payment terms are in the agreement.",
            "language": "en",
            "speaker": "SPEAKER_00",
        },
    ]


@pytest.fixture
def mock_diarization():
    """Create sample diarization results."""
    return [
        {"start": 0.0, "end": 8.0, "speaker": "SPEAKER_00"},
        {"start": 8.0, "end": 12.0, "speaker": "SPEAKER_01"},
        {"start": 12.0, "end": 15.0, "speaker": "SPEAKER_00"},
    ]


@pytest.fixture
def temp_prompts_dir(tmp_path):
    """Create a temporary prompts directory structure."""
    prompts_dir = tmp_path / "prompts"

    # Create notes mode
    notes_dir = prompts_dir / "notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "chunk.txt").write_text("Notes chunk prompt: {{TRANSCRIPT_CHUNK}}")
    (notes_dir / "final.txt").write_text("Notes final prompt: {{CHUNK_SUMMARIES}}")

    # Create call_check mode
    call_check_dir = prompts_dir / "call_check"
    call_check_dir.mkdir(parents=True)
    (call_check_dir / "chunk.txt").write_text(
        "Check for required phrases: {{REQUIRED_PHRASES}}\n\nTranscript: {{TRANSCRIPT_CHUNK}}"
    )
    (call_check_dir / "final.txt").write_text("Final check: {{CHUNK_SUMMARIES}}")

    # Create factcheck mode
    factcheck_dir = prompts_dir / "factcheck"
    factcheck_dir.mkdir(parents=True)
    (factcheck_dir / "chunk.txt").write_text("Fact check: {{TRANSCRIPT_CHUNK}}")
    (factcheck_dir / "final.txt").write_text("Final facts: {{CHUNK_SUMMARIES}}")

    # Create tldr mode
    tldr_dir = prompts_dir / "tldr"
    tldr_dir.mkdir(parents=True)
    (tldr_dir / "chunk.txt").write_text("TL;DR chunk: {{TRANSCRIPT_CHUNK}}")
    (tldr_dir / "final.txt").write_text("TL;DR final: {{CHUNK_SUMMARIES}}")

    # Create inquisition mode
    inq_dir = prompts_dir / "inquisition"
    inq_dir.mkdir(parents=True)
    (inq_dir / "chunk.txt").write_text(
        "Inquisition chunk: {{EVALUATION_GOAL}} {{TRANSCRIPT_CHUNK}}"
    )
    (inq_dir / "final.txt").write_text("Inquisition final: {{CHUNK_SUMMARIES}}")

    # Create old-style fallback prompts
    (prompts_dir / "chunk_summary.txt").write_text("Old chunk format")
    (prompts_dir / "final_summary.txt").write_text("Old final format")

    # Shared modular prompts (summarizer topic pipeline)
    shared_dir = prompts_dir / "_shared"
    shared_dir.mkdir(parents=True)
    (shared_dir / "topic_reduce.txt").write_text(
        "TOPIC_REDUCE_TEST_MARKER\nSynthesize one section.\n{{CHUNK_SUMMARIES}}\n{target_language}\n"
    )
    (shared_dir / "overview.txt").write_text("Overview: {{TOPIC_BRIEFS}}\n{target_language}\n")
    (shared_dir / "conclusion.txt").write_text("Conclusion: {{TOPIC_BRIEFS}}\n{target_language}\n")

    return prompts_dir
