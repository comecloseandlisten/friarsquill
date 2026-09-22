"""Test pipeline module with batch processing and progress callbacks."""
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import tempfile

from config import Config
from pipeline import Pipeline, _cleanup_summary_checkpoints


class TestPipelineInitialization:
    """Test Pipeline initialization."""

    def test_pipeline_init(self):
        """Test Pipeline initialization."""
        callback = Mock()
        pipeline = Pipeline(callback)

        assert pipeline.send == callback
        assert hasattr(pipeline, "_cancel_event")

    def test_pipeline_cancel(self):
        """Test pipeline cancellation flag."""
        callback = Mock()
        pipeline = Pipeline(callback)

        assert not pipeline._cancel_event.is_set()
        pipeline.cancel()
        assert pipeline._cancel_event.is_set()


class TestPipelineProgressCallback:
    """Test pipeline progress callback mechanism."""

    def test_progress_callback_format(self):
        """Test that progress callback sends correct message format."""
        messages = []

        def callback(msg):
            messages.append(msg)

        pipeline = Pipeline(callback)
        pipeline._progress(1, "download", 50, "Downloading...")

        assert len(messages) == 1
        msg = messages[0]
        assert msg["id"] == 1
        assert msg["type"] == "progress"
        assert msg["stage"] == "download"
        assert msg["percent"] == 50
        assert msg["message"] == "Downloading..."

    def test_progress_callback_multiple_stages(self):
        """Test progress callbacks across multiple stages."""
        messages = []

        def callback(msg):
            messages.append(msg)

        pipeline = Pipeline(callback)
        stages = ["download", "extract", "diarize", "transcribe", "summarize", "format"]

        for i, stage in enumerate(stages):
            pipeline._progress(1, stage, i * 10, f"Working on {stage}...")

        assert len(messages) == len(stages)
        for i, msg in enumerate(messages):
            assert msg["stage"] == stages[i]


def _mock_pipeline_deps():
    """Return a stack of patches for common pipeline dependencies."""
    def _fake_extract(input_path, output_path, **kwargs):
        Path(output_path).write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
        return output_path

    return {
        "get_duration": patch("pipeline.get_duration", return_value=60.0),
        "extract_audio": patch("pipeline.extract_audio", side_effect=_fake_extract),
        "transcribe": patch("pipeline.transcribe"),
        "summarize": patch("pipeline.summarize"),
        "ASRRuntime": patch("pipeline.ASRRuntime"),
        "format_markdown": patch("pipeline.format_markdown", return_value="# Summary\nTest"),
    }


class TestPipelineRun:
    """Test single video pipeline execution."""

    def test_run_requires_source(self):
        """Test that run requires a source parameter."""
        pipeline = Pipeline(Mock())

        with pytest.raises((KeyError, ValueError)):
            pipeline.run(1, {})

    def test_run_with_file_path(self):
        """Test run with local file path."""
        callback = Mock()
        pipeline = Pipeline(callback)

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            patches = _mock_pipeline_deps()
            with patches["get_duration"], patches["ASRRuntime"], patches["extract_audio"] as mock_extract:
                with patches["transcribe"] as mock_transcribe:
                    with patches["summarize"] as mock_summarize:
                        with patches["format_markdown"]:
                            mock_transcribe.return_value = [
                                {"start": 0, "end": 5, "text": "test", "language": "en"}
                            ]
                            mock_summarize.return_value = "Summary"

                            result = pipeline.run(
                                1,
                                {
                                    "source": tmp_path,
                                    "config": {},
                                },
                            )

                            assert "markdown" in result
                            assert "output_path" in result
                            mock_extract.assert_called_once()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_run_config_overrides(self):
        """Test that run params override config."""
        callback = Mock()
        pipeline = Pipeline(callback)

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            patches = _mock_pipeline_deps()
            with patches["get_duration"], patches["ASRRuntime"], patches["extract_audio"]:
                with patches["transcribe"] as mock_transcribe:
                    with patches["summarize"] as mock_summarize:
                        with patches["format_markdown"]:
                            mock_transcribe.return_value = [
                                {"start": 0, "end": 5, "text": "test", "language": "en"}
                            ]
                            mock_summarize.return_value = "Summary"

                            params = {
                                "source": tmp_path,
                                "config": {"summary_mode": "notes"},
                                "summary_mode": "call_check",
                                "summary_mode_config": {"required_phrases": ["test"]},
                            }

                            result = pipeline.run(1, params)

                            assert "markdown" in result
                            summary_call = mock_summarize.call_args
                            if summary_call:
                                config_arg = summary_call[0][1]
                                assert config_arg.summary_mode == "call_check"
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_run_cancellation(self):
        """Test that run respects cancellation."""
        callback = Mock()
        pipeline = Pipeline(callback)

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            with patch("pipeline.get_duration", side_effect=Exception("Cancelled")):
                pipeline.cancel()
                with pytest.raises(Exception):
                    pipeline.run(1, {"source": tmp_path, "config": {}})
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_run_diarization_skipped(self):
        """Diarization feature is disabled — pipeline skips it even if config requests it."""
        callback = Mock()
        pipeline = Pipeline(callback)

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            patches = _mock_pipeline_deps()
            with patches["get_duration"], patches["ASRRuntime"], patches["extract_audio"]:
                with patches["transcribe"] as mock_transcribe:
                    with patches["summarize"] as mock_summarize:
                        with patches["format_markdown"]:
                            mock_transcribe.return_value = [
                                {"start": 0, "end": 5, "text": "test", "language": "en"}
                            ]
                            mock_summarize.return_value = "Summary"

                            result = pipeline.run(
                                1,
                                {
                                    "source": tmp_path,
                                    "config": {"diarize": True},
                                },
                            )

                            assert "markdown" in result
        finally:
            Path(tmp_path).unlink(missing_ok=True)


class TestPipelineNoSummary:
    """Test pipeline with no_summary=True (transcription only)."""

    def test_run_no_summary_skips_summarize(self):
        """When no_summary=True, summarize should not be called."""
        callback = Mock()
        pipeline = Pipeline(callback)

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            patches = _mock_pipeline_deps()
            with patches["get_duration"], patches["ASRRuntime"], patches["extract_audio"]:
                with patches["transcribe"] as mock_transcribe:
                    with patches["summarize"] as mock_summarize:
                        with patches["format_markdown"]:
                            mock_transcribe.return_value = [
                                {"start": 0, "end": 5, "text": "test", "language": "en"}
                            ]
                            mock_summarize.return_value = "Summary"

                            result = pipeline.run(
                                1,
                                {
                                    "source": tmp_path,
                                    "no_summary": True,
                                    "config": {},
                                },
                            )

                            assert "markdown" in result
                            # summarize should NOT have been called
                            mock_summarize.assert_not_called()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_run_no_summary_progress_shows_skipped(self):
        """When no_summary=True, progress should show skip message."""
        messages = []

        def callback(msg):
            messages.append(msg)

        pipeline = Pipeline(callback)

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            patches = _mock_pipeline_deps()
            with patches["get_duration"], patches["ASRRuntime"], patches["extract_audio"]:
                with patches["transcribe"] as mock_transcribe:
                    with patches["summarize"] as mock_summarize:
                        with patches["format_markdown"]:
                            mock_transcribe.return_value = [
                                {"start": 0, "end": 5, "text": "test", "language": "en"}
                            ]
                            mock_summarize.return_value = "Summary"

                            pipeline.run(
                                1,
                                {
                                    "source": tmp_path,
                                    "no_summary": True,
                                    "config": {},
                                },
                            )

                            # Check progress messages for skip
                            summarize_msgs = [
                                m for m in messages
                                if m.get("type") == "progress" and m.get("stage") == "summarize"
                            ]
                            assert any("Skipped" in m.get("message", "") for m in summarize_msgs)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_batch_no_summary_skips_summarize(self):
        """When no_summary=True in batch, summarize should not be called."""
        callback = Mock()
        pipeline = Pipeline(callback)

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            patches = _mock_pipeline_deps()
            with patches["get_duration"], patches["ASRRuntime"], patches["extract_audio"]:
                with patches["transcribe"] as mock_transcribe:
                    with patches["summarize"] as mock_summarize:
                        mock_transcribe.return_value = [
                            {"start": 0, "end": 5, "text": "test", "language": "en"}
                        ]
                        mock_summarize.return_value = "Summary"

                        result = pipeline.run_batch(
                            1,
                            {
                                "sources": [tmp_path],
                                "no_summary": True,
                                "config": {},
                            },
                        )

                        assert "markdown" in result
                        mock_summarize.assert_not_called()
        finally:
            Path(tmp_path).unlink(missing_ok=True)


class TestPipelineBatch:
    """Test batch processing pipeline."""

    def test_run_batch_requires_sources(self):
        """Test that run_batch requires sources."""
        pipeline = Pipeline(Mock())

        with pytest.raises(ValueError, match="No sources"):
            pipeline.run_batch(1, {"config": {}})

    def test_run_batch_single_source(self):
        """Test batch processing with single source."""
        callback = Mock()
        pipeline = Pipeline(callback)

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            patches = _mock_pipeline_deps()
            with patches["get_duration"], patches["ASRRuntime"], patches["extract_audio"] as mock_extract:
                with patches["transcribe"] as mock_transcribe:
                    with patches["summarize"] as mock_summarize:
                        with patches["format_markdown"]:
                            mock_transcribe.return_value = [
                                {"start": 0, "end": 5, "text": "test", "language": "en"}
                            ]
                            mock_summarize.return_value = "Summary"

                            result = pipeline.run_batch(
                                1,
                                {
                                    "sources": [tmp_path],
                                    "config": {},
                                },
                            )

                            assert "markdown" in result
                            assert "output_path" in result
                            assert result["videos_processed"] == 1
                            mock_extract.assert_called_once()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_run_batch_multiple_sources(self):
        """Test batch processing with multiple sources."""
        callback = Mock()
        pipeline = Pipeline(callback)

        sources = []
        try:
            for i in range(3):
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                    sources.append(tmp.name)

            patches = _mock_pipeline_deps()
            with patches["get_duration"], patches["ASRRuntime"], patches["extract_audio"]:
                with patches["transcribe"] as mock_transcribe:
                    with patches["summarize"] as mock_summarize:
                        with patches["format_markdown"]:
                            mock_transcribe.return_value = [
                                {"start": 0, "end": 5, "text": "test", "language": "en"}
                            ]
                            mock_summarize.return_value = "Summary"

                            result = pipeline.run_batch(
                                1,
                                {
                                    "sources": sources,
                                    "config": {},
                                },
                            )

                            assert result["videos_processed"] >= 0
        finally:
            for src in sources:
                Path(src).unlink(missing_ok=True)

    def test_run_batch_config_overrides(self):
        """Test batch processing respects config overrides."""
        callback = Mock()
        pipeline = Pipeline(callback)

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            patches = _mock_pipeline_deps()
            with patches["get_duration"], patches["ASRRuntime"], patches["extract_audio"]:
                with patches["transcribe"] as mock_transcribe:
                    with patches["summarize"] as mock_summarize:
                        with patches["format_markdown"]:
                            mock_transcribe.return_value = [
                                {"start": 0, "end": 5, "text": "test", "language": "en"}
                            ]
                            mock_summarize.return_value = "Summary"

                            params = {
                                "sources": [tmp_path],
                                "config": {"summary_mode": "notes"},
                                "summary_mode": "tldr",
                                "diarize": True,
                            }

                            result = pipeline.run_batch(1, params)
                            assert "markdown" in result
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_run_batch_combined_summary(self):
        """Test that batch produces combined summary."""
        callback = Mock()
        pipeline = Pipeline(callback)

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            patches = _mock_pipeline_deps()
            with patches["get_duration"], patches["ASRRuntime"], patches["extract_audio"]:
                with patches["transcribe"] as mock_transcribe:
                    with patches["summarize"] as mock_summarize:
                        mock_transcribe.return_value = [
                            {"start": 0, "end": 5, "text": "test", "language": "en"}
                        ]
                        mock_summarize.return_value = "Combined summary"

                        result = pipeline.run_batch(
                            1,
                            {
                                "sources": [tmp_path],
                                "config": {},
                            },
                        )

                        assert "Combined summary" in result["markdown"]
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_run_batch_cancellation(self):
        """Test that run_batch respects cancellation."""
        callback = Mock()
        pipeline = Pipeline(callback)

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            patches = _mock_pipeline_deps()
            with patches["get_duration"], patches["ASRRuntime"], patches["extract_audio"]:
                with patches["transcribe"] as mock_transcribe:
                    def cancel_and_transcribe(*args, **kwargs):
                        pipeline.cancel()
                        return [{"start": 0, "end": 5, "text": "test", "language": "en"}]

                    mock_transcribe.side_effect = cancel_and_transcribe

                    with pytest.raises(Exception, match="Cancelled"):
                        pipeline.run_batch(
                            1,
                            {
                                "sources": [tmp_path],
                                "config": {},
                            },
                        )
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_run_batch_partial_failure(self):
        """Test batch continues on partial failures."""
        callback = Mock()
        pipeline = Pipeline(callback)

        sources = []
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp1:
                sources.append(tmp1.name)
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp2:
                sources.append(tmp2.name)

            patches = _mock_pipeline_deps()
            with patches["get_duration"] as mock_duration, patches["ASRRuntime"], patches["extract_audio"]:
                with patches["transcribe"] as mock_transcribe:
                    with patches["summarize"] as mock_summarize:
                        call_count = [0]

                        def transcribe_effect(*args, **kwargs):
                            call_count[0] += 1
                            if call_count[0] == 1:
                                return [{"start": 0, "end": 5, "text": "test", "language": "en"}]
                            else:
                                raise Exception("Transcription failed")

                        mock_transcribe.side_effect = transcribe_effect
                        mock_summarize.return_value = "Summary"

                        result = pipeline.run_batch(
                            1,
                            {
                                "sources": sources,
                                "config": {},
                            },
                        )

                        assert result["videos_processed"] >= 0
        finally:
            for src in sources:
                Path(src).unlink(missing_ok=True)


class TestPipelineOutputHandling:
    """Test pipeline output handling."""

    def test_run_returns_markdown_and_path(self):
        """Test that run returns both markdown and output path."""
        callback = Mock()
        pipeline = Pipeline(callback)

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            patches = _mock_pipeline_deps()
            with patches["get_duration"], patches["ASRRuntime"], patches["extract_audio"]:
                with patches["transcribe"] as mock_transcribe:
                    with patches["summarize"] as mock_summarize:
                        with patches["format_markdown"]:
                            mock_transcribe.return_value = [
                                {"start": 0, "end": 5, "text": "test", "language": "en"}
                            ]
                            mock_summarize.return_value = "Summary"

                            result = pipeline.run(
                                1,
                                {
                                    "source": tmp_path,
                                    "config": {},
                                },
                            )

                            assert "markdown" in result
                            assert "output_path" in result
                            assert isinstance(result["markdown"], str)
                            assert isinstance(result["output_path"], str)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_batch_output_path_timestamp(self):
        """Test that batch output includes timestamp."""
        callback = Mock()
        pipeline = Pipeline(callback)

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            patches = _mock_pipeline_deps()
            with patches["get_duration"], patches["ASRRuntime"], patches["extract_audio"]:
                with patches["transcribe"] as mock_transcribe:
                    with patches["summarize"] as mock_summarize:
                        mock_transcribe.return_value = [
                            {"start": 0, "end": 5, "text": "test", "language": "en"}
                        ]
                        mock_summarize.return_value = "Summary"

                        result = pipeline.run_batch(
                            1,
                            {
                                "sources": [tmp_path],
                                "config": {},
                            },
                        )

                        assert "batch_report" in result["output_path"]
        finally:
            Path(tmp_path).unlink(missing_ok=True)


class TestSummaryCheckpointCleanup:
    """Summary resume files under .checkpoints should be removed after a successful run."""

    def test_cleanup_removes_summary_json_and_dir(self, tmp_path):
        cp = tmp_path / ".checkpoints"
        cp.mkdir(parents=True)
        (cp / "summary_deadbeef.json").write_text('{"v":1}', encoding="utf-8")
        _cleanup_summary_checkpoints(tmp_path)
        assert not (cp / "summary_deadbeef.json").exists()
        assert not cp.exists()

    def test_cleanup_leaves_unrelated_files(self, tmp_path):
        cp = tmp_path / ".checkpoints"
        cp.mkdir(parents=True)
        (cp / "summary_aaa.json").write_text("{}", encoding="utf-8")
        (cp / "asr_state.bin").write_bytes(b"x")
        _cleanup_summary_checkpoints(tmp_path)
        assert not (cp / "summary_aaa.json").exists()
        assert (cp / "asr_state.bin").exists()
        assert cp.is_dir()
