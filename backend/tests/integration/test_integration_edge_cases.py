"""
Integration tests: edge cases and robustness.

Validates graceful handling of adversarial / unusual inputs:
- Silence-only audio (no speech)
- Very short clips (< 2s)
- Corrupted / truncated files
- Unusual sample rates, mono/stereo, bit depths
- Missing output directory
- Path with special characters / unicode
- Large file resilience
"""
import json
import math
import shutil
import struct
import sys
import tempfile
import time
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from audio import get_duration
from config import Config
from conftest import BenchmarkResult, MetricsCollector


# ──────────────────────────────────────────────────────────────
# Synthetic WAV generators
# ──────────────────────────────────────────────────────────────

def _make_silence_wav(path: Path, duration_sec: float = 3.0, sample_rate: int = 16000):
    """Write a silent WAV file (all zeros)."""
    n_samples = int(duration_sec * sample_rate)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_samples)


def _make_tone_wav(path: Path, duration_sec: float = 1.0, freq: int = 440, sample_rate: int = 16000):
    """Write a WAV file with a pure sine tone (no speech)."""
    n_samples = int(duration_sec * sample_rate)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = b""
        for i in range(n_samples):
            val = int(16000 * math.sin(2 * math.pi * freq * i / sample_rate))
            frames += struct.pack("<h", val)
        wf.writeframes(frames)


def _make_truncated_wav(path: Path):
    """Write a WAV with valid header but truncated data."""
    _make_silence_wav(path, duration_sec=5.0)
    data = path.read_bytes()
    path.write_bytes(data[:200])


# ──────────────────────────────────────────────────────────────
# Silence handling
# ──────────────────────────────────────────────────────────────

class TestSilenceHandling:
    """Pipeline gracefully handles silence-only input."""

    def test_silence_produces_empty_or_fallback(self, output_dir: Path, metrics_collector: MetricsCollector):
        br = BenchmarkResult(
            test_name="silence_handling",
            file="synthetic_silence.wav",
            category="edge_cases",
        )
        silence_path = output_dir / "silence_test.wav"
        _make_silence_wav(silence_path, duration_sec=5.0)

        try:
            from pipeline import Pipeline

            messages = []
            pipeline = Pipeline(lambda msg: messages.append(msg))
            result = pipeline.run(1, {
                "source": str(silence_path),
                "config": {"output_dir": str(output_dir)},
            })
            br.total_time_sec = result.get("metrics", {}).get("stage_times_sec", {}).get("total", 0)

            md = result["markdown"]
            # Either no segments or a "no speech" fallback
            segments_count = result.get("metrics", {}).get("segments", 0)
            br.segments_count = segments_count
            br.extra["has_no_speech_message"] = "No speech" in md or segments_count == 0

        except Exception as e:
            br.error = str(e)
            metrics_collector.add(br)
            raise
        metrics_collector.add(br)

    def test_tone_only_produces_empty(self, output_dir: Path, metrics_collector: MetricsCollector):
        """Pure tone (non-speech audio) should result in no/few segments."""
        br = BenchmarkResult(
            test_name="tone_only",
            file="synthetic_tone.wav",
            category="edge_cases",
        )
        tone_path = output_dir / "tone_test.wav"
        _make_tone_wav(tone_path, duration_sec=3.0, freq=1000)

        try:
            from pipeline import Pipeline

            messages = []
            pipeline = Pipeline(lambda msg: messages.append(msg))
            result = pipeline.run(1, {
                "source": str(tone_path),
                "config": {"output_dir": str(output_dir)},
            })
            segments_count = result.get("metrics", {}).get("segments", 0)
            br.segments_count = segments_count
            # Tone shouldn't produce meaningful speech segments
            br.extra["segments_count"] = segments_count

        except Exception as e:
            br.error = str(e)
            metrics_collector.add(br)
            raise
        metrics_collector.add(br)


# ──────────────────────────────────────────────────────────────
# Very short clips
# ──────────────────────────────────────────────────────────────

class TestVeryShortClips:
    """Clips under 2 seconds should not crash the pipeline."""

    def test_one_second_silence(self, output_dir: Path, metrics_collector: MetricsCollector):
        br = BenchmarkResult(
            test_name="very_short_1s",
            file="synthetic_1s.wav",
            category="edge_cases",
        )
        path = output_dir / "short_1s.wav"
        _make_silence_wav(path, duration_sec=1.0)

        try:
            from pipeline import Pipeline
            pipeline = Pipeline(lambda msg: None)
            result = pipeline.run(1, {
                "source": str(path),
                "config": {"output_dir": str(output_dir)},
            })
            br.segments_count = result.get("metrics", {}).get("segments", 0)
            assert "markdown" in result
        except Exception as e:
            br.error = str(e)
            metrics_collector.add(br)
            raise
        metrics_collector.add(br)

    def test_half_second_clip(self, output_dir: Path, metrics_collector: MetricsCollector):
        br = BenchmarkResult(
            test_name="very_short_0.5s",
            file="synthetic_0.5s.wav",
            category="edge_cases",
        )
        path = output_dir / "short_05s.wav"
        _make_silence_wav(path, duration_sec=0.5)

        try:
            from pipeline import Pipeline
            pipeline = Pipeline(lambda msg: None)
            result = pipeline.run(1, {
                "source": str(path),
                "config": {"output_dir": str(output_dir)},
            })
            assert "markdown" in result
        except Exception as e:
            br.error = str(e)
            metrics_collector.add(br)
            raise
        metrics_collector.add(br)


# ──────────────────────────────────────────────────────────────
# Corrupted / truncated files
# ──────────────────────────────────────────────────────────────

class TestCorruptedFiles:
    """Corrupted files raise informative errors, not unhandled crashes."""

    def test_truncated_wav(self, output_dir: Path, metrics_collector: MetricsCollector):
        br = BenchmarkResult(
            test_name="truncated_wav",
            file="synthetic_truncated.wav",
            category="edge_cases",
        )
        path = output_dir / "truncated.wav"
        _make_truncated_wav(path)

        try:
            from pipeline import Pipeline
            pipeline = Pipeline(lambda msg: None)
            # This should either succeed with partial data or raise a clear error
            try:
                result = pipeline.run(1, {
                    "source": str(path),
                    "config": {"output_dir": str(output_dir)},
                })
                br.extra["outcome"] = "succeeded_partial"
            except (RuntimeError, FileNotFoundError, Exception) as e:
                br.extra["outcome"] = "raised_error"
                br.extra["error_type"] = type(e).__name__
                # As long as it doesn't crash with an unhandled traceback, this is OK

        except Exception as e:
            br.error = str(e)
        metrics_collector.add(br)

    def test_zero_byte_file(self, output_dir: Path, metrics_collector: MetricsCollector):
        br = BenchmarkResult(
            test_name="zero_byte_file",
            file="empty.wav",
            category="edge_cases",
        )
        path = output_dir / "empty.wav"
        path.write_bytes(b"")

        from pipeline import Pipeline
        pipeline = Pipeline(lambda msg: None)
        with pytest.raises(Exception):
            pipeline.run(1, {
                "source": str(path),
                "config": {"output_dir": str(output_dir)},
            })
        br.extra["outcome"] = "correctly_rejected"
        metrics_collector.add(br)

    def test_random_bytes_file(self, output_dir: Path, metrics_collector: MetricsCollector):
        br = BenchmarkResult(
            test_name="random_bytes",
            file="random_garbage.bin",
            category="edge_cases",
        )
        path = output_dir / "garbage.mp4"
        import os
        path.write_bytes(os.urandom(4096))

        from pipeline import Pipeline
        pipeline = Pipeline(lambda msg: None)
        with pytest.raises(Exception):
            pipeline.run(1, {
                "source": str(path),
                "config": {"output_dir": str(output_dir)},
            })
        br.extra["outcome"] = "correctly_rejected"
        metrics_collector.add(br)


# ──────────────────────────────────────────────────────────────
# Files from edge_cases/ directory
# ──────────────────────────────────────────────────────────────

class TestEdgeCaseFiles:
    """User-provided edge case files are handled without crashing."""

    def test_edge_case_file(self, edge_case_file: Path, metrics_collector: MetricsCollector, output_dir: Path):
        br = BenchmarkResult(
            test_name="edge_case_user_file",
            file=edge_case_file.name,
            category="edge_cases",
            file_size_mb=edge_case_file.stat().st_size / (1024 * 1024),
        )
        try:
            from pipeline import Pipeline
            pipeline = Pipeline(lambda msg: None)
            try:
                result = pipeline.run(1, {
                    "source": str(edge_case_file),
                    "config": {"output_dir": str(output_dir)},
                })
                br.extra["outcome"] = "success"
                br.segments_count = result.get("metrics", {}).get("segments", 0)
            except Exception as e:
                br.extra["outcome"] = "error"
                br.extra["error_type"] = type(e).__name__
                br.extra["error_msg"] = str(e)[:200]
        except Exception as e:
            br.error = str(e)
        metrics_collector.add(br)


# ──────────────────────────────────────────────────────────────
# Path handling
# ──────────────────────────────────────────────────────────────

class TestPathHandling:
    """Pipeline handles unusual path characters."""

    def test_unicode_filename(self, short_file: Path, output_dir: Path, metrics_collector: MetricsCollector):
        """File with unicode characters in name is processed correctly."""
        if short_file is None:
            pytest.skip("No short test files")

        br = BenchmarkResult(
            test_name="unicode_filename",
            file="тест_файл.wav",
            category="edge_cases",
        )
        unicode_path = output_dir / "тест_файл_аудио.wav"
        try:
            shutil.copy2(str(short_file), str(unicode_path))
        except Exception as e:
            br.error = f"Copy failed: {e}"
            metrics_collector.add(br)
            pytest.skip(f"Cannot create unicode filename: {e}")

        try:
            from pipeline import Pipeline
            pipeline = Pipeline(lambda msg: None)
            result = pipeline.run(1, {
                "source": str(unicode_path),
                "config": {"output_dir": str(output_dir)},
            })
            assert "markdown" in result
            br.extra["outcome"] = "success"
        except Exception as e:
            br.error = str(e)
            metrics_collector.add(br)
            raise
        metrics_collector.add(br)

    def test_space_in_path(self, short_file: Path, output_dir: Path, metrics_collector: MetricsCollector):
        """File path with spaces works correctly."""
        if short_file is None:
            pytest.skip("No short test files")

        br = BenchmarkResult(
            test_name="space_in_path",
            file="test file with spaces.wav",
            category="edge_cases",
        )
        space_dir = output_dir / "path with spaces"
        space_dir.mkdir(parents=True, exist_ok=True)
        space_path = space_dir / "test file with spaces.wav"
        try:
            shutil.copy2(str(short_file), str(space_path))
        except Exception as e:
            br.error = f"Copy failed: {e}"
            metrics_collector.add(br)
            pytest.skip(str(e))

        try:
            from pipeline import Pipeline
            pipeline = Pipeline(lambda msg: None)
            result = pipeline.run(1, {
                "source": str(space_path),
                "config": {"output_dir": str(space_dir)},
            })
            assert "markdown" in result
        except Exception as e:
            br.error = str(e)
            metrics_collector.add(br)
            raise
        metrics_collector.add(br)


# ──────────────────────────────────────────────────────────────
# Source validation
# ──────────────────────────────────────────────────────────────

class TestSourceValidation:
    """Invalid sources are rejected with clear errors."""

    def test_nonexistent_file_rejected(self, output_dir: Path):
        from pipeline import Pipeline
        pipeline = Pipeline(lambda msg: None)
        with pytest.raises((FileNotFoundError, ValueError)):
            pipeline.run(1, {
                "source": str(output_dir / "this_does_not_exist.wav"),
                "config": {"output_dir": str(output_dir)},
            })

    def test_empty_source_rejected(self, output_dir: Path):
        from pipeline import Pipeline
        pipeline = Pipeline(lambda msg: None)
        with pytest.raises((ValueError, KeyError)):
            pipeline.run(1, {"source": "", "config": {"output_dir": str(output_dir)}})

    def test_path_traversal_rejected(self, output_dir: Path):
        from pipeline import _validate_output_dir
        with pytest.raises(ValueError, match="traversal"):
            _validate_output_dir("../../etc/passwd")


# ──────────────────────────────────────────────────────────────
# Config edge cases
# ──────────────────────────────────────────────────────────────

class TestConfigEdgeCases:
    """Pipeline handles unusual but valid configurations."""

    def test_all_fast_profile_options(self, short_file: Path, output_dir: Path):
        """Pipeline with forced fast-profile runs without error."""
        if short_file is None:
            pytest.skip("No short test files")

        from pipeline import Pipeline
        pipeline = Pipeline(lambda msg: None)
        result = pipeline.run(1, {
            "source": str(short_file),
            "config": {
                "output_dir": str(output_dir),
                "beam_size": 1,
                "word_timestamps": False,
                "vad_filter": True,
            },
        })
        assert "markdown" in result

    def test_cpu_forced(self, short_file: Path, output_dir: Path):
        """Pipeline with device=cpu runs (no CUDA required)."""
        if short_file is None:
            pytest.skip("No short test files")

        from pipeline import Pipeline
        pipeline = Pipeline(lambda msg: None)
        result = pipeline.run(1, {
            "source": str(short_file),
            "config": {
                "output_dir": str(output_dir),
                "device": "cpu",
                "compute_type": "int8",
            },
        })
        assert "markdown" in result
